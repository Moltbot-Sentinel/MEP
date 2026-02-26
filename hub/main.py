from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Header, Depends
from typing import Dict, List, Optional
import uuid
import time
import os
import db
import auth
from logger import log_event, log_audit

from models import NodeRegistration, TaskCreate, TaskResult, TaskBid, TaskCancel

app = FastAPI(title="Chronos Protocol L1 Hub", description="The Time Exchange Clearinghouse", version="0.1.2")

# In-memory storage for active tasks
active_tasks: Dict[str, dict] = {} # task_id -> task_details
completed_tasks: Dict[str, dict] = {} # task_id -> result
connected_nodes: Dict[str, WebSocket] = {} # node_id -> websocket
rate_limits: Dict[str, List[float]] = {}
MAX_BODY_BYTES = 200_000
MAX_PAYLOAD_CHARS = 20_000
RATE_LIMIT_WINDOW = 10.0
RATE_LIMIT_MAX = 50
MAX_SKEW_SECONDS = 300
ALLOWED_IPS = [ip.strip() for ip in os.getenv("MEP_ALLOWED_IPS", "").split(",") if ip.strip()]
for task in db.get_active_tasks():
    task_data = {
        "id": task["task_id"],
        "consumer_id": task["consumer_id"],
        "payload": task["payload"],
        "bounty": task["bounty"],
        "status": task["status"],
        "target_node": task["target_node"],
        "model_requirement": task["model_requirement"],
        "provider_id": task["provider_id"]
    }
    active_tasks[task_data["id"]] = task_data

# --- IDENTITY VERIFICATION MIDDLEWARE ---
def _is_allowed_ip(host: Optional[str]) -> bool:
    if not ALLOWED_IPS:
        return True
    return host in ALLOWED_IPS

def _apply_rate_limit(key: str):
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    timestamps = rate_limits.get(key, [])
    timestamps = [t for t in timestamps if t >= window_start]
    if len(timestamps) >= RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    timestamps.append(now)
    rate_limits[key] = timestamps

def _validate_timestamp(ts: str):
    try:
        ts_int = int(ts)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp")
    now = int(time.time())
    if abs(now - ts_int) > MAX_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="Timestamp out of allowed window")

async def verify_request(
    request: Request,
    x_mep_nodeid: str = Header(...),
    x_mep_timestamp: str = Header(...),
    x_mep_signature: str = Header(...)
) -> str:
    client_host = request.client.host if request.client else None
    if not _is_allowed_ip(client_host):
        raise HTTPException(status_code=403, detail="Client IP not allowed")

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    _apply_rate_limit(f"{x_mep_nodeid}:{request.url.path}")
    _validate_timestamp(x_mep_timestamp)

    payload_str = body.decode('utf-8')

    pub_pem = db.get_pub_pem(x_mep_nodeid)
    if not pub_pem:
        raise HTTPException(status_code=401, detail="Unknown Node ID. Please register first.")

    if not auth.verify_signature(pub_pem, payload_str, x_mep_timestamp, x_mep_signature):
        raise HTTPException(status_code=401, detail="Invalid cryptographic signature.")

    return x_mep_nodeid

@app.post("/register")
async def register_node(node: NodeRegistration, request: Request):
    client_host = request.client.host if request.client else None
    if not _is_allowed_ip(client_host):
        raise HTTPException(status_code=403, detail="Client IP not allowed")
    _apply_rate_limit(f"{client_host}:/register")
    # Registration derives the Node ID from the provided Public Key PEM
    node_id = auth.derive_node_id(node.pubkey)
    balance = db.register_node(node_id, node.pubkey)

    log_event("node_registered", f"Node {node_id} registered with starting balance {balance}", node_id=node_id, starting_balance=balance)
    log_audit("REGISTER", node_id, balance, balance, "START_BONUS")

    return {"status": "success", "node_id": node_id, "balance": balance}

@app.get("/balance/{node_id}")
async def get_balance(node_id: str):
    balance = db.get_balance(node_id)
    if balance is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"node_id": node_id, "balance_seconds": balance}

@app.post("/tasks/submit")
async def submit_task(
    task: TaskCreate,
    authenticated_node: str = Depends(verify_request),
    x_mep_idempotency_key: Optional[str] = Header(default=None)
):
    # Verify the signer is actually the consumer claiming to submit the task
    if authenticated_node != task.consumer_id:
        raise HTTPException(status_code=403, detail="Cannot submit tasks on behalf of another node")

    if len(task.payload) > MAX_PAYLOAD_CHARS:
        raise HTTPException(status_code=413, detail="Task payload too large")
    
    if x_mep_idempotency_key:
        existing = db.get_idempotency(authenticated_node, "/tasks/submit", x_mep_idempotency_key)
        if existing:
            return existing["response"]

    consumer_balance = db.get_balance(task.consumer_id)
    if consumer_balance is None:
        raise HTTPException(status_code=404, detail="Consumer node not found")

    # If bounty is positive, consumer is PAYING. Check consumer balance.
    if task.bounty > 0 and consumer_balance < task.bounty:
        raise HTTPException(status_code=400, detail="Insufficient SECONDS balance to pay for task")

    task_id = str(uuid.uuid4())
    now = time.time()

    # Note: If bounty is negative, consumer is SELLING data. We don't deduct here.
    # We will deduct from the provider when they complete the task.
    if task.bounty > 0:
        success = db.deduct_balance(task.consumer_id, task.bounty)
        if not success:
            log_event("task_rejected", f"Node {task.consumer_id} lacks SECONDS to submit task", consumer_id=task.consumer_id, bounty=task.bounty)
            raise HTTPException(status_code=400, detail="Insufficient SECONDS balance")
            
        new_balance = db.get_balance(task.consumer_id)
        log_audit("ESCROW", task.consumer_id, -task.bounty, new_balance, task_id)
        
    log_event("task_submitted", f"Task {task_id[:8]} broadcasted by {task.consumer_id} for {task.bounty}", consumer_id=task.consumer_id, task_id=task_id, bounty=task.bounty)

    task_data = {
        "id": task_id,
        "consumer_id": task.consumer_id,
        "payload": task.payload,
        "bounty": task.bounty,
        "status": "bidding",
        "target_node": task.target_node,
        "model_requirement": task.model_requirement
    }
    db.create_task(task_id, task.consumer_id, task.payload, task.bounty, "bidding", task.target_node, task.model_requirement, now)
    active_tasks[task_id] = task_data

    # Target specific node if requested (Direct Message skips bidding)
    if task.target_node:
        if task.target_node in connected_nodes:
            try:
                task_data["status"] = "assigned"
                task_data["provider_id"] = task.target_node
                db.update_task_assignment(task_id, task.target_node, "assigned", time.time())
                await connected_nodes[task.target_node].send_json({"event": "new_task", "data": task_data})
                response_payload = {"status": "success", "task_id": task_id, "routed_to": task.target_node}
                if x_mep_idempotency_key:
                    db.set_idempotency(authenticated_node, "/tasks/submit", x_mep_idempotency_key, response_payload, 200, time.time())
                return response_payload
            except Exception:
                return {"status": "error", "detail": "Target node disconnected"}
        else:
            return {"status": "error", "detail": "Target node not currently connected to Hub"}

    # Phase 2: Broadcast RFC (Request For Compute) to all connected nodes EXCEPT the consumer
    rfc_data = {
        "id": task_id,
        "consumer_id": task.consumer_id,
        "bounty": task.bounty,
        "model_requirement": task.model_requirement
    }
    for node_id, ws in list(connected_nodes.items()):
        if node_id != task.consumer_id:
            try:
                await ws.send_json({"event": "rfc", "data": rfc_data})
            except Exception:
                pass

    response_payload = {"status": "success", "task_id": task_id}
    if x_mep_idempotency_key:
        db.set_idempotency(authenticated_node, "/tasks/submit", x_mep_idempotency_key, response_payload, 200, time.time())
    return response_payload

@app.post("/tasks/cancel")
async def cancel_task(
    cancel: TaskCancel,
    authenticated_node: str = Depends(verify_request),
    x_mep_idempotency_key: Optional[str] = Header(default=None)
):
    if x_mep_idempotency_key:
        existing = db.get_idempotency(authenticated_node, "/tasks/cancel", x_mep_idempotency_key)
        if existing:
            return existing["response"]

    task = active_tasks.get(cancel.task_id)
    if not task:
        db_task = db.get_task(cancel.task_id)
        if not db_task:
            raise HTTPException(status_code=404, detail="Task not found")
        task = {
            "id": db_task["task_id"],
            "consumer_id": db_task["consumer_id"],
            "payload": db_task["payload"],
            "bounty": db_task["bounty"],
            "status": db_task["status"],
            "target_node": db_task["target_node"],
            "model_requirement": db_task["model_requirement"],
            "provider_id": db_task["provider_id"]
        }

    if authenticated_node != task["consumer_id"]:
        raise HTTPException(status_code=403, detail="Cannot cancel tasks on behalf of another node")

    now = time.time()
    if not db.cancel_task_if_open(cancel.task_id, now):
        raise HTTPException(status_code=400, detail="Task cannot be cancelled at this stage")

    if task["bounty"] > 0:
        db.add_balance(task["consumer_id"], task["bounty"])
        new_balance = db.get_balance(task["consumer_id"])
        log_audit("REFUND", task["consumer_id"], task["bounty"], new_balance, cancel.task_id)

    if cancel.task_id in active_tasks:
        del active_tasks[cancel.task_id]

    log_event("task_cancelled", f"Task {cancel.task_id[:8]} cancelled by {task['consumer_id']}", consumer_id=task["consumer_id"], task_id=cancel.task_id, bounty=task["bounty"])

    response_payload = {"status": "success", "task_id": cancel.task_id, "state": "cancelled"}
    if x_mep_idempotency_key:
        db.set_idempotency(authenticated_node, "/tasks/cancel", x_mep_idempotency_key, response_payload, 200, time.time())
    return response_payload

@app.post("/tasks/bid")
async def place_bid(bid: TaskBid, authenticated_node: str = Depends(verify_request)):
    if authenticated_node != bid.provider_id:
        raise HTTPException(status_code=403, detail="Cannot bid on behalf of another node")

    task = active_tasks.get(bid.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or already completed")

    if task["status"] != "bidding":
        return {"status": "rejected", "detail": "Task already assigned to another node"}

    if not db.assign_task_if_open(bid.task_id, bid.provider_id, time.time()):
        return {"status": "rejected", "detail": "Task already assigned to another node"}

    task["status"] = "assigned"
    task["provider_id"] = bid.provider_id

    log_event("bid_accepted", f"Task {bid.task_id[:8]} assigned to {bid.provider_id}", task_id=bid.task_id, provider_id=bid.provider_id, bounty=task["bounty"])

    # Return the full payload to the winner
    return {
        "status": "accepted",
        "payload": task["payload"],
        "consumer_id": task["consumer_id"],
        "model_requirement": task.get("model_requirement")
    }

@app.post("/tasks/complete")
async def complete_task(
    result: TaskResult,
    authenticated_node: str = Depends(verify_request),
    x_mep_idempotency_key: Optional[str] = Header(default=None)
):
    if authenticated_node != result.provider_id:
        raise HTTPException(status_code=403, detail="Cannot complete tasks on behalf of another node")

    if len(result.result_payload) > MAX_PAYLOAD_CHARS:
        raise HTTPException(status_code=413, detail="Result payload too large")
    
    if x_mep_idempotency_key:
        existing = db.get_idempotency(authenticated_node, "/tasks/complete", x_mep_idempotency_key)
        if existing:
            return existing["response"]

    task = active_tasks.get(result.task_id)
    if not task:
        db_task = db.get_task(result.task_id)
        if not db_task or db_task["status"] not in ("bidding", "assigned"):
            raise HTTPException(status_code=404, detail="Task not found or already claimed")
        task = {
            "id": db_task["task_id"],
            "consumer_id": db_task["consumer_id"],
            "payload": db_task["payload"],
            "bounty": db_task["bounty"],
            "status": db_task["status"],
            "target_node": db_task["target_node"],
            "model_requirement": db_task["model_requirement"],
            "provider_id": db_task["provider_id"]
        }
        active_tasks[result.task_id] = task

    provider_balance = db.get_balance(result.provider_id)
    if provider_balance is None:
        db.set_balance(result.provider_id, 0.0)

    # Transfer SECONDS based on positive or negative bounty
    bounty = task["bounty"]
    if bounty >= 0:
        # Standard Compute Market: Provider earns SECONDS
        db.add_balance(result.provider_id, bounty)
        new_balance = db.get_balance(result.provider_id)
        log_audit("EARN_COMPUTE", result.provider_id, bounty, new_balance, result.task_id)
    else:
        # Data Market: Provider PAYS to receive this payload/task
        cost = abs(bounty)
        success = db.deduct_balance(result.provider_id, cost)
        if not success:
            log_event("data_purchase_failed", f"Provider {result.provider_id} lacks SECONDS to buy {result.task_id}", task_id=result.task_id, provider_id=result.provider_id, cost=cost)
            raise HTTPException(status_code=400, detail="Provider lacks SECONDS to buy this data")

        p_balance = db.get_balance(result.provider_id)
        log_audit("BUY_DATA", result.provider_id, -cost, p_balance, result.task_id)

        db.add_balance(task["consumer_id"], cost) # The sender earns SECONDS
        c_balance = db.get_balance(task["consumer_id"])
        log_audit("SELL_DATA", task["consumer_id"], cost, c_balance, result.task_id)

    log_event("task_completed", f"Task {result.task_id[:8]} completed by {result.provider_id}", task_id=result.task_id, provider_id=result.provider_id, bounty=bounty)

    # Move task to completed
    task["status"] = "completed"
    task["provider_id"] = result.provider_id
    task["result"] = result.result_payload
    completed_tasks[result.task_id] = task
    del active_tasks[result.task_id]
    db.update_task_result(result.task_id, result.provider_id, result.result_payload, "completed", time.time())

    # ROUTE RESULT BACK TO CONSUMER VIA WEBSOCKET
    consumer_id = task["consumer_id"]
    if consumer_id in connected_nodes:
        try:
            await connected_nodes[consumer_id].send_json({
                "event": "task_result",
                "data": {
                    "task_id": result.task_id,
                    "provider_id": result.provider_id,
                    "result_payload": result.result_payload,
                    "bounty_spent": task["bounty"]
                }
            })
        except Exception:
            pass # Consumer disconnected, they can fetch it via REST later (TODO)

    response_payload = {"status": "success", "earned": task["bounty"], "new_balance": db.get_balance(result.provider_id)}
    if x_mep_idempotency_key:
        db.set_idempotency(authenticated_node, "/tasks/complete", x_mep_idempotency_key, response_payload, 200, time.time())
    return response_payload

@app.get("/tasks/result/{task_id}")
async def get_task_result(task_id: str, authenticated_node: str = Depends(verify_request)):
    task = db.get_task(task_id)
    if not task or task["status"] != "completed":
        raise HTTPException(status_code=404, detail="Task not found or not completed")
    if authenticated_node not in (task["consumer_id"], task["provider_id"]):
        raise HTTPException(status_code=403, detail="Not authorized to view this result")
    return {
        "task_id": task["task_id"],
        "consumer_id": task["consumer_id"],
        "provider_id": task["provider_id"],
        "bounty": task["bounty"],
        "result_payload": task["result_payload"]
    }

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str, timestamp: str, signature: str):
    client_host = websocket.client.host if websocket.client else None
    if not _is_allowed_ip(client_host):
        await websocket.close(code=4003, reason="Client IP not allowed")
        return

    try:
        _apply_rate_limit(f"{node_id}:/ws")
        _validate_timestamp(timestamp)
    except HTTPException as exc:
        await websocket.close(code=4004, reason=exc.detail)
        return

    pub_pem = db.get_pub_pem(node_id)
    if not pub_pem:
        await websocket.close(code=4001, reason="Unknown Node ID")
        return

    if not auth.verify_signature(pub_pem, node_id, timestamp, signature):
        await websocket.close(code=4002, reason="Invalid Signature")
        return

    await websocket.accept()
    connected_nodes[node_id] = websocket
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if node_id in connected_nodes:
            del connected_nodes[node_id]
