from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from typing import Dict, List
import uuid
import db

from models import NodeRegistration, TaskCreate, TaskResult, NodeBalance, TaskBid

app = FastAPI(title="Chronos Protocol L1 Hub", description="The Time Exchange Clearinghouse", version="0.1.1")

# In-memory storage for active tasks
active_tasks: Dict[str, dict] = {} # task_id -> task_details
completed_tasks: Dict[str, dict] = {} # task_id -> result
connected_nodes: Dict[str, WebSocket] = {} # node_id -> websocket

@app.post("/register")
async def register_node(node: NodeRegistration):
    balance = db.get_balance(node.pubkey)
    if balance is None:
        db.set_balance(node.pubkey, 10.0) # Starter bonus
        balance = 10.0
    return {"status": "success", "node_id": node.pubkey, "balance": balance}

@app.get("/balance/{node_id}")
async def get_balance(node_id: str):
    balance = db.get_balance(node_id)
    if balance is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"node_id": node_id, "balance_seconds": balance}

@app.post("/tasks/submit")
async def submit_task(task: TaskCreate):
    consumer_balance = db.get_balance(task.consumer_id)
    if consumer_balance is None:
        raise HTTPException(status_code=404, detail="Consumer node not found")
        
    # If bounty is positive, consumer is PAYING. Check consumer balance.
    if task.bounty > 0 and consumer_balance < task.bounty:
        raise HTTPException(status_code=400, detail="Insufficient SECONDS balance to pay for task")
        
    # Note: If bounty is negative, consumer is SELLING data. We don't deduct here.
    # We will deduct from the provider when they complete the task.
    if task.bounty > 0:
        success = db.deduct_balance(task.consumer_id, task.bounty)
        if not success:
            raise HTTPException(status_code=400, detail="Insufficient SECONDS balance")
        
    task_id = str(uuid.uuid4())
    task_data = {
        "id": task_id,
        "consumer_id": task.consumer_id,
        "payload": task.payload,
        "bounty": task.bounty,
        "status": "bidding",
        "target_node": task.target_node,
        "model_requirement": task.model_requirement
    }
    active_tasks[task_id] = task_data
    
    # Target specific node if requested (Direct Message skips bidding)
    if task.target_node:
        if task.target_node in connected_nodes:
            try:
                task_data["status"] = "assigned"
                task_data["provider_id"] = task.target_node
                await connected_nodes[task.target_node].send_json({"event": "new_task", "data": task_data})
                return {"status": "success", "task_id": task_id, "routed_to": task.target_node}
            except:
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
            except:
                pass
                
    return {"status": "success", "task_id": task_id}

@app.post("/tasks/bid")
async def place_bid(bid: TaskBid):
    task = active_tasks.get(bid.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or already completed")
        
    if task["status"] != "bidding":
        return {"status": "rejected", "detail": "Task already assigned to another node"}
        
    # Phase 2 Fast Auction: Accept the first valid bid
    task["status"] = "assigned"
    task["provider_id"] = bid.provider_id
    
    # Return the full payload to the winner
    return {
        "status": "accepted",
        "payload": task["payload"],
        "consumer_id": task["consumer_id"],
        "model_requirement": task.get("model_requirement")
    }

@app.post("/tasks/complete")
async def complete_task(result: TaskResult):
    task = active_tasks.get(result.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or already claimed")
        
    provider_balance = db.get_balance(result.provider_id)
    if provider_balance is None:
        db.set_balance(result.provider_id, 0.0)

    # Transfer SECONDS based on positive or negative bounty
    bounty = task["bounty"]
    if bounty >= 0:
        # Standard Compute Market: Provider earns SECONDS
        db.add_balance(result.provider_id, bounty)
    else:
        # Data Market: Provider PAYS to receive this payload/task
        cost = abs(bounty)
        success = db.deduct_balance(result.provider_id, cost)
        if not success:
            raise HTTPException(status_code=400, detail="Provider lacks SECONDS to buy this data")
        
        db.add_balance(task["consumer_id"], cost) # The sender earns SECONDS
    
    # Move task to completed
    task["status"] = "completed"
    task["provider_id"] = result.provider_id
    task["result"] = result.result_payload
    completed_tasks[result.task_id] = task
    del active_tasks[result.task_id]
    
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
        except:
            pass # Consumer disconnected, they can fetch it via REST later (TODO)

    return {"status": "success", "earned": task["bounty"], "new_balance": db.get_balance(result.provider_id)}

@app.websocket("/ws/{node_id}")
async def websocket_endpoint(websocket: WebSocket, node_id: str):
    await websocket.accept()
    connected_nodes[node_id] = websocket
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        if node_id in connected_nodes:
            del connected_nodes[node_id]
