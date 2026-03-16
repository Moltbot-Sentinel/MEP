from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Header, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse
from typing import Dict, List, Optional
import asyncio
import uuid
import time
import json
from datetime import datetime
import os
import ctypes
from urllib.parse import urlparse
import db
import auth
from logger import log_event, log_audit

from models import NodeRegistration, TaskCreate, TaskResult, TaskBid, TaskCancel, RegistryUpdate, AvailabilityUpdate, RegistryHeartbeat, ReputationSubmit, DisputeOpen, DisputeResolve

app = FastAPI(title="Chronos Protocol L1 Hub", description="The Time Exchange Clearinghouse", version="0.1.2")

# In-memory storage for active tasks
active_tasks: Dict[str, dict] = {}
completed_tasks: Dict[str, dict] = {}
connected_nodes: Dict[str, WebSocket] = {}
rate_limits: Dict[str, List[float]] = {}
task_lock = asyncio.Lock()
node_lock = asyncio.Lock()
MAX_BODY_BYTES = 200_000
MAX_PAYLOAD_CHARS = 20_000
RATE_LIMIT_WINDOW = 10.0
RATE_LIMIT_MAX = 50
MAX_SKEW_SECONDS = 300
ALLOWED_IPS = [ip.strip() for ip in os.getenv("MEP_ALLOWED_IPS", "").split(",") if ip.strip()]
REQUEUE_ASSIGNED_ON_START = os.getenv("MEP_REQUEUE_ASSIGNED_ON_START", "false").lower() in ("1", "true", "yes")
ADMIN_KEY = os.getenv("MEP_ADMIN_KEY")
DISPUTE_WINDOW_SECONDS = int(os.getenv("MEP_DISPUTE_WINDOW_SECONDS", "86400"))
ASSIGNMENT_TIMEOUT_SECONDS = int(os.getenv("MEP_ASSIGNMENT_TIMEOUT_SECONDS", "3600"))
ASSIGNMENT_SWEEP_INTERVAL_SECONDS = int(os.getenv("MEP_ASSIGNMENT_SWEEP_INTERVAL_SECONDS", "60"))
TIMEOUT_POLICY = os.getenv("MEP_TIMEOUT_POLICY", "refund").lower()
VALID_AVAILABILITY = {"online", "idle", "busy", "offline", "unknown"}
DEFAULT_REGISTRY_MAX_AGE_MINUTES = float(os.getenv("MEP_REGISTRY_MAX_AGE_MINUTES", "0") or "0")
ASSIGNMENT_REPUTATION_WEIGHT = float(os.getenv("MEP_ASSIGNMENT_REPUTATION_WEIGHT", "0.55"))
ASSIGNMENT_AVAILABILITY_WEIGHT = float(os.getenv("MEP_ASSIGNMENT_AVAILABILITY_WEIGHT", "0.25"))
ASSIGNMENT_CAPABILITY_WEIGHT = float(os.getenv("MEP_ASSIGNMENT_CAPABILITY_WEIGHT", "0.20"))
ASSIGNMENT_REPUTATION_CONFIDENCE_REVIEWS = int(os.getenv("MEP_ASSIGNMENT_REPUTATION_CONFIDENCE_REVIEWS", "10"))
RISK_MIN_REPUTATION_SCORE = float(os.getenv("MEP_RISK_MIN_REPUTATION_SCORE", "2.5"))
RISK_MIN_REPUTATION_REVIEWS = int(os.getenv("MEP_RISK_MIN_REPUTATION_REVIEWS", "3"))
RISK_REJECT_AVAILABILITY = {
    item.strip().lower()
    for item in os.getenv("MEP_RISK_REJECT_AVAILABILITY", "offline").split(",")
    if item.strip()
}
RFC_TOP_K = int(os.getenv("MEP_RFC_TOP_K", "0"))
active_db_tasks = db.get_active_tasks()
if REQUEUE_ASSIGNED_ON_START and active_db_tasks:
    now = time.time()
    for task in active_db_tasks:
        if task["status"] == "assigned" and not task["target_node"]:
            db.update_task_status(task["task_id"], "bidding", now)
            log_event("task_requeued", f"Task {task['task_id'][:8]} requeued on startup", consumer_id=task["consumer_id"], task_id=task["task_id"], bounty=task["bounty"])
for task in active_db_tasks:
    task_data = {
        "id": task["task_id"],
        "consumer_id": task["consumer_id"],
        "payload": task["payload"],
        "bounty": task["bounty"],
        "status": task["status"],
        "target_node": task["target_node"],
        "model_requirement": task["model_requirement"],
        "provider_id": task["provider_id"],
        "payload_uri": task.get("payload_uri"),
        "secret_data": task.get("result_payload")
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

def _require_admin(x_mep_admin_key: Optional[str]):
    if not ADMIN_KEY or not x_mep_admin_key or x_mep_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin key required")

def _normalize_availability(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in VALID_AVAILABILITY:
        raise HTTPException(status_code=400, detail="Invalid availability")
    return normalized

def _normalize_model_requirement(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return normalized

def _normalize_artifact_uri(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.startswith("ipfs://"):
        return normalized
    parsed = urlparse(normalized)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be an http(s) or ipfs URI")
    return normalized

def _provider_matches_requirement(provider_id: str, model_requirement: Optional[str]) -> bool:
    if not model_requirement:
        return True
    registry = db.get_registry(provider_id)
    if not registry:
        return True
    models = registry.get("models") or []
    skills = registry.get("skills") or []
    if not models and not skills:
        return True
    return model_requirement in models or model_requirement in skills

def _compute_provider_assignment_profile(provider_id: str, model_requirement: Optional[str]) -> dict:
    model_requirement_normalized = _normalize_model_requirement(model_requirement)
    registry = db.get_registry(provider_id) or {}
    reputation = db.get_reputation(provider_id) or {}
    models = [item.strip().lower() for item in registry.get("models", []) if isinstance(item, str)]
    skills = [item.strip().lower() for item in registry.get("skills", []) if isinstance(item, str)]
    availability = (registry.get("availability") or "unknown").strip().lower()
    capability_match = _provider_matches_requirement(provider_id, model_requirement_normalized)
    if not model_requirement_normalized:
        capability_score = 1.0
    elif model_requirement_normalized in models:
        capability_score = 1.0
    elif model_requirement_normalized in skills:
        capability_score = 0.8
    elif not models and not skills:
        capability_score = 0.5
    else:
        capability_score = 0.0
    availability_score_map = {
        "online": 1.0,
        "idle": 0.9,
        "busy": 0.4,
        "unknown": 0.3,
        "offline": 0.0
    }
    availability_score = availability_score_map.get(availability, 0.2)
    raw_score = float(reputation.get("score", 0.0) or 0.0)
    raw_score = max(0.0, min(5.0, raw_score))
    total_reviews = int(reputation.get("total_reviews", 0) or 0)
    confidence_base = max(1, ASSIGNMENT_REPUTATION_CONFIDENCE_REVIEWS)
    confidence = min(1.0, total_reviews / confidence_base)
    reputation_score = (raw_score / 5.0) * confidence + 0.5 * (1.0 - confidence)
    assignment_score = (
        ASSIGNMENT_REPUTATION_WEIGHT * reputation_score
        + ASSIGNMENT_AVAILABILITY_WEIGHT * availability_score
        + ASSIGNMENT_CAPABILITY_WEIGHT * capability_score
    )
    risk_reasons: list[str] = []
    if model_requirement_normalized and not capability_match:
        risk_reasons.append("capability_mismatch")
    if availability in RISK_REJECT_AVAILABILITY:
        risk_reasons.append(f"availability_{availability}")
    if total_reviews >= RISK_MIN_REPUTATION_REVIEWS and raw_score < RISK_MIN_REPUTATION_SCORE:
        risk_reasons.append("low_reputation")
    return {
        "provider_id": provider_id,
        "availability": availability,
        "reputation_score": raw_score,
        "total_reviews": total_reviews,
        "capability_match": capability_match,
        "assignment_score": assignment_score,
        "risk_reasons": risk_reasons
    }

def _select_rfc_recipients(consumer_id: str, model_requirement: Optional[str], nodes: list[tuple[str, WebSocket]]) -> list[tuple[str, WebSocket]]:
    selected: list[tuple[str, WebSocket, float]] = []
    for node_id, ws in nodes:
        if node_id == consumer_id:
            continue
        profile = _compute_provider_assignment_profile(node_id, model_requirement)
        if profile["risk_reasons"]:
            continue
        selected.append((node_id, ws, float(profile["assignment_score"])))
    selected.sort(key=lambda item: item[2], reverse=True)
    if RFC_TOP_K > 0:
        selected = selected[:RFC_TOP_K]
    return [(node_id, ws) for node_id, ws, _ in selected]

def _validate_timestamp(ts: str):
    try:
        ts_int = int(ts)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp")
    now = int(time.time())
    if abs(now - ts_int) > MAX_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="Timestamp out of allowed window")

def _format_uptime(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def _get_system_uptime_seconds() -> Optional[float]:
    proc_uptime = "/proc/uptime"
    if os.path.exists(proc_uptime):
        try:
            with open(proc_uptime, "r", encoding="utf-8") as f:
                value = f.read().split()[0]
            return float(value)
        except Exception:
            return None
    try:
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:
        return None

def _resolve_log_path(filename: str) -> Optional[str]:
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", filename),
        os.path.join(os.getcwd(), "logs", filename)
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def _tail_lines(path: str, limit: int) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.readlines()[-limit:]

def _escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def _sweep_assigned_timeouts():
    if ASSIGNMENT_TIMEOUT_SECONDS <= 0:
        return
    cutoff = time.time() - ASSIGNMENT_TIMEOUT_SECONDS
    expired_tasks = db.get_assigned_tasks_before(cutoff)
    if not expired_tasks:
        return
    now = time.time()
    for task in expired_tasks:
        if TIMEOUT_POLICY == "rebroadcast" and not task["target_node"]:
            if not db.requeue_task_if_assigned(task["task_id"], now):
                continue
            task_data = {
                "id": task["task_id"],
                "consumer_id": task["consumer_id"],
                "payload": task["payload"],
                "bounty": task["bounty"],
                "status": "bidding",
                "target_node": task["target_node"],
                "model_requirement": task["model_requirement"],
                "payload_uri": task.get("payload_uri"),
                "secret_data": task.get("result_payload")
            }
            async with task_lock:
                active_tasks[task["task_id"]] = task_data
            model_requirement = _normalize_model_requirement(task["model_requirement"])
            rfc_data = {
                "id": task["task_id"],
                "consumer_id": task["consumer_id"],
                "bounty": task["bounty"],
                "model_requirement": model_requirement,
                "payload_uri": task.get("payload_uri")
            }
            async with node_lock:
                broadcast_nodes = list(connected_nodes.items())
            candidate_nodes = _select_rfc_recipients(task["consumer_id"], model_requirement, broadcast_nodes)
            for node_id, ws in candidate_nodes:
                try:
                    await ws.send_json({"event": "rfc", "data": rfc_data})
                except Exception as exc:
                    log_event("broadcast_error", f"Failed to send RFC to {node_id}: {exc}", node_id=node_id, task_id=task["task_id"])
                    async with node_lock:
                        if connected_nodes.get(node_id) is ws:
                            del connected_nodes[node_id]
            log_event("task_requeued_timeout", f"Task {task['task_id'][:8]} requeued after timeout", task_id=task["task_id"], consumer_id=task["consumer_id"], bounty=task["bounty"])
            continue
        if not db.expire_task_if_assigned(task["task_id"], now):
            continue
        if task["bounty"] > 0:
            refunded = db.refund_escrow(task["task_id"], now)
            if refunded is None:
                db.add_balance(task["consumer_id"], task["bounty"])
            new_balance = db.get_balance(task["consumer_id"])
            log_audit("TIMEOUT_REFUND", task["consumer_id"], task["bounty"], new_balance, task["task_id"])
        async with task_lock:
            if task["task_id"] in active_tasks:
                del active_tasks[task["task_id"]]
        log_event("task_expired", f"Task {task['task_id'][:8]} expired after timeout", task_id=task["task_id"], consumer_id=task["consumer_id"], provider_id=task["provider_id"], bounty=task["bounty"])

async def _assignment_timeout_worker():
    while True:
        try:
            await _sweep_assigned_timeouts()
        except Exception as exc:
            log_event("timeout_sweep_failed", f"Timeout sweep failed: {exc}")
        await asyncio.sleep(ASSIGNMENT_SWEEP_INTERVAL_SECONDS)

@app.on_event("startup")
async def start_timeout_worker():
    asyncio.create_task(_assignment_timeout_worker())

def _read_recent_events(limit: int) -> list[dict]:
    path = _resolve_log_path("hub.json")
    if not path:
        return []
    lines = _tail_lines(path, limit)
    events = []
    for line in lines:
        try:
            entry = json.loads(line)
            events.append({
                "timestamp": entry.get("timestamp", ""),
                "event": entry.get("event", ""),
                "message": entry.get("message", "")
            })
        except Exception:
            continue
    return events

def _read_audit_entries_for_node(node_id: str, limit: int) -> list[str]:
    path = _resolve_log_path("ledger_audit.log")
    if not path:
        return []
    scan_limit = min(max(limit * 50, 200), 5000)
    lines = _tail_lines(path, scan_limit)
    needle = f"Node: {node_id} |"
    matches = [line.strip() for line in reversed(lines) if needle in line]
    return list(reversed(matches[:limit]))

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
    if node.alias or getattr(node, 'x25519_public_key', None):
        db.upsert_registry(node_id, node.alias, [], [], {}, "offline", time.time(), getattr(node, 'x25519_public_key', None))

    log_event("node_registered", f"Node {node_id} registered with starting balance {balance}", node_id=node_id, starting_balance=balance)
    log_audit("REGISTER", node_id, balance, balance, "START_BONUS")

    return {"status": "success", "node_id": node_id, "balance": balance}

@app.post("/registry/update")
async def update_registry(payload: RegistryUpdate, authenticated_node: str = Depends(verify_request)):
    skills = [item.strip().lower() for item in payload.skills or [] if item and item.strip()]
    models = [item.strip().lower() for item in payload.models or [] if item and item.strip()]
    metadata = payload.metadata or {}
    availability = _normalize_availability(payload.availability)
    if availability is None:
        existing = db.get_registry(authenticated_node)
        availability = existing.get("availability") if existing else "unknown"
    db.upsert_registry(authenticated_node, payload.alias, skills, models, metadata, availability, time.time())
    return {"status": "success", "node_id": authenticated_node}

@app.post("/registry/availability")
async def update_availability(payload: AvailabilityUpdate, authenticated_node: str = Depends(verify_request)):
    availability = _normalize_availability(payload.availability)
    db.update_registry_availability(authenticated_node, availability, time.time())
    return {"status": "success", "node_id": authenticated_node, "availability": availability}

@app.post("/registry/heartbeat")
async def registry_heartbeat(payload: RegistryHeartbeat, authenticated_node: str = Depends(verify_request)):
    availability = _normalize_availability(payload.availability)
    if availability is None:
        existing = db.get_registry(authenticated_node)
        availability = existing.get("availability") if existing else "unknown"
    db.update_registry_availability(authenticated_node, availability, time.time())
    return {"status": "success", "node_id": authenticated_node, "availability": availability}

@app.get("/registry/search")
async def search_registry(
    alias: Optional[str] = None,
    skill: Optional[str] = None,
    model: Optional[str] = None,
    availability: Optional[str] = None,
    min_score: Optional[float] = None,
    min_reviews: Optional[int] = None,
    max_age_minutes: Optional[float] = None,
    limit: int = 20
):
    safe_limit = max(1, min(limit, 100))
    safe_min_score = min_score if min_score is None else max(0.0, min(min_score, 5.0))
    safe_min_reviews = min_reviews if min_reviews is None else max(0, min_reviews)
    if max_age_minutes is None:
        safe_max_age = DEFAULT_REGISTRY_MAX_AGE_MINUTES if DEFAULT_REGISTRY_MAX_AGE_MINUTES > 0 else None
    else:
        safe_max_age = max(0.0, max_age_minutes)
    min_updated_at = None if safe_max_age is None else time.time() - safe_max_age * 60.0
    normalized_alias = alias.strip().lower() if alias else None
    normalized_skill = skill.strip().lower() if skill else None
    normalized_model = model.strip().lower() if model else None
    normalized_availability = _normalize_availability(availability) if availability else None
    results = db.search_registry(normalized_alias, normalized_skill, normalized_model, normalized_availability, safe_min_score, safe_min_reviews, min_updated_at, safe_limit)
    return {"count": len(results), "results": results}

@app.get("/registry/{node_id}")
async def get_registry(node_id: str):
    entry = db.get_registry(node_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Registry entry not found")
    return entry

@app.post("/reputation/submit")
async def submit_reputation(payload: ReputationSubmit, authenticated_node: str = Depends(verify_request)):
    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    task = db.get_task(payload.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="Task not completed")
    if task["consumer_id"] != authenticated_node:
        raise HTTPException(status_code=403, detail="Only the consumer can submit a review")
    if task.get("provider_id") != payload.provider_id:
        raise HTTPException(status_code=400, detail="Provider does not match task")
    result = db.submit_review(payload.task_id, authenticated_node, payload.provider_id, payload.rating, time.time())
    if result["status"] == "exists":
        raise HTTPException(status_code=409, detail="Review already submitted for this task")
    return {"status": "success", "provider_id": payload.provider_id, "score": result["score"], "total_reviews": result["total_reviews"]}

@app.get("/reputation/{node_id}")
async def get_reputation(node_id: str):
    entry = db.get_reputation(node_id)
    if not entry:
        return {"node_id": node_id, "score": 0.0, "total_reviews": 0}
    return entry

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

    normalized_payload_uri = _normalize_artifact_uri(task.payload_uri, "payload_uri")
    payload = task.payload or ""
    if not payload and not normalized_payload_uri:
        raise HTTPException(status_code=400, detail="Task requires payload or payload_uri")
    if len(payload) > MAX_PAYLOAD_CHARS:
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
    if task.bounty < 0 and not task.secret_data:
        raise HTTPException(status_code=400, detail="Data market tasks (bounty < 0) require secret_data")

    task_id = str(uuid.uuid4())
    now = time.time()

    # Note: If bounty is negative, consumer is SELLING data. We don't deduct here.
    # We will deduct from the provider when they complete the task.
    if task.bounty > 0:
        success = db.deduct_balance(task.consumer_id, task.bounty)
        if not success:
            log_event("task_rejected", f"Node {task.consumer_id} lacks SECONDS to submit task", consumer_id=task.consumer_id, bounty=task.bounty)
            raise HTTPException(status_code=400, detail="Insufficient SECONDS balance")
            
        db.create_escrow(task_id, task.consumer_id, task.bounty, now)
        new_balance = db.get_balance(task.consumer_id)
        log_audit("ESCROW", task.consumer_id, -task.bounty, new_balance, task_id)
        
    log_event("task_submitted", f"Task {task_id[:8]} broadcasted by {task.consumer_id} for {task.bounty}", consumer_id=task.consumer_id, task_id=task_id, bounty=task.bounty)

    task_data = {
        "id": task_id,
        "consumer_id": task.consumer_id,
        "payload": payload,
        "bounty": task.bounty,
        "status": "bidding",
        "target_node": task.target_node,
        "model_requirement": task.model_requirement,
        "payload_uri": normalized_payload_uri,
        "secret_data": task.secret_data
    }
    db.create_task(task_id, task.consumer_id, payload, task.bounty, "bidding", task.target_node, task.model_requirement, now, result_payload=task.secret_data, payload_uri=normalized_payload_uri)
    async with task_lock:
        active_tasks[task_id] = task_data

    # Target specific node if requested (Direct Message skips bidding)
    if task.target_node:
        async with node_lock:
            target_ws = connected_nodes.get(task.target_node)
        if target_ws:
            try:
                async with task_lock:
                    task_data["status"] = "assigned"
                    task_data["provider_id"] = task.target_node
                db.update_task_assignment(task_id, task.target_node, "assigned", time.time())
                await target_ws.send_json({"event": "new_task", "data": task_data})
                response_payload = {"status": "success", "task_id": task_id, "routed_to": task.target_node}
                if x_mep_idempotency_key:
                    db.set_idempotency(authenticated_node, "/tasks/submit", x_mep_idempotency_key, response_payload, 200, time.time())
                return response_payload
            except Exception as exc:
                log_event("direct_message_failed", f"Failed to route {task_id[:8]} to {task.target_node}: {exc}", task_id=task_id, provider_id=task.target_node)
                async with task_lock:
                    task_data["status"] = "bidding"
                    if "provider_id" in task_data:
                        del task_data["provider_id"]
                db.update_task_status(task_id, "bidding", time.time())
                async with node_lock:
                    if connected_nodes.get(task.target_node) is target_ws:
                        del connected_nodes[task.target_node]
                return {"status": "error", "detail": "Target node disconnected"}
        return {"status": "error", "detail": "Target node not currently connected to Hub"}

    model_requirement = _normalize_model_requirement(task.model_requirement)
    rfc_data = {
        "id": task_id,
        "consumer_id": task.consumer_id,
        "bounty": task.bounty,
        "model_requirement": model_requirement,
        "payload_uri": normalized_payload_uri
    }
    async with node_lock:
        broadcast_nodes = list(connected_nodes.items())
    candidate_nodes = _select_rfc_recipients(task.consumer_id, model_requirement, broadcast_nodes)
    for node_id, ws in candidate_nodes:
        try:
            await ws.send_json({"event": "rfc", "data": rfc_data})
        except Exception as exc:
            log_event("broadcast_error", f"Failed to send RFC to {node_id}: {exc}", node_id=node_id, task_id=task_id)
            async with node_lock:
                if connected_nodes.get(node_id) is ws:
                    del connected_nodes[node_id]

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

    async with task_lock:
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
            "provider_id": db_task["provider_id"],
            "payload_uri": db_task.get("payload_uri"),
            "secret_data": db_task.get("result_payload")
        }

    if authenticated_node != task["consumer_id"]:
        raise HTTPException(status_code=403, detail="Cannot cancel tasks on behalf of another node")

    now = time.time()
    if not db.cancel_task_if_open(cancel.task_id, now):
        raise HTTPException(status_code=400, detail="Task cannot be cancelled at this stage")

    if task["bounty"] > 0:
        refunded = db.refund_escrow(cancel.task_id, now)
        if refunded is None:
            db.add_balance(task["consumer_id"], task["bounty"])
        new_balance = db.get_balance(task["consumer_id"])
        log_audit("REFUND", task["consumer_id"], task["bounty"], new_balance, cancel.task_id)

    async with task_lock:
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

    async with task_lock:
        task = active_tasks.get(bid.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found or already completed")
        if task["status"] != "bidding":
            return {"status": "rejected", "detail": "Task already assigned to another node"}
        model_requirement = _normalize_model_requirement(task.get("model_requirement"))
        assignment_profile = _compute_provider_assignment_profile(bid.provider_id, model_requirement)
        if assignment_profile["risk_reasons"]:
            risk_reasons = ", ".join(assignment_profile["risk_reasons"])
            return {"status": "rejected", "detail": f"Provider rejected by risk control: {risk_reasons}"}
        if not db.assign_task_if_open(bid.task_id, bid.provider_id, time.time()):
            return {"status": "rejected", "detail": "Task already assigned to another node"}
        task["status"] = "assigned"
        task["provider_id"] = bid.provider_id
        payload = task["payload"]
        consumer_id = task["consumer_id"]
        model_requirement = task.get("model_requirement")
        payload_uri = task.get("payload_uri")
        bounty = task["bounty"]

    log_event("bid_accepted", f"Task {bid.task_id[:8]} assigned to {bid.provider_id}", task_id=bid.task_id, provider_id=bid.provider_id, bounty=bounty)

    return {
        "status": "accepted",
        "payload": payload,
        "consumer_id": consumer_id,
        "model_requirement": model_requirement,
        "payload_uri": payload_uri,
        "secret_data": task.get("secret_data", task.get("result_payload")),
        "assignment_score": assignment_profile["assignment_score"]
    }

@app.post("/tasks/complete")
async def complete_task(
    result: TaskResult,
    authenticated_node: str = Depends(verify_request),
    x_mep_idempotency_key: Optional[str] = Header(default=None)
):
    if authenticated_node != result.provider_id:
        raise HTTPException(status_code=403, detail="Cannot complete tasks on behalf of another node")

    normalized_result_uri = _normalize_artifact_uri(result.result_uri, "result_uri")
    result_payload = result.result_payload or ""
    if not result_payload and not normalized_result_uri:
        raise HTTPException(status_code=400, detail="Task result requires result_payload or result_uri")
    if len(result_payload) > MAX_PAYLOAD_CHARS:
        raise HTTPException(status_code=413, detail="Result payload too large")
    
    if x_mep_idempotency_key:
        existing = db.get_idempotency(authenticated_node, "/tasks/complete", x_mep_idempotency_key)
        if existing:
            return existing["response"]

    async with task_lock:
        task = active_tasks.get(result.task_id)
    if not task:
        db_task = db.get_task(result.task_id)
        # FIX: Allow more statuses for task completion (bidding, assigned, pending)
        if not db_task or db_task["status"] not in ("bidding", "assigned", "pending"):
            raise HTTPException(status_code=404, detail="Task not found or already claimed")
        task = {
            "id": db_task["task_id"],
            "consumer_id": db_task["consumer_id"],
            "payload": db_task["payload"],
            "bounty": db_task["bounty"],
            "status": db_task["status"],
            "target_node": db_task["target_node"],
            "model_requirement": db_task["model_requirement"],
            "provider_id": db_task["provider_id"],
            "payload_uri": db_task.get("payload_uri"),
            "secret_data": db_task.get("result_payload")
        }
        async with task_lock:
            active_tasks[result.task_id] = task

    provider_balance = db.get_balance(result.provider_id)
    if provider_balance is None:
        db.set_balance(result.provider_id, 0.0)

    # Transfer SECONDS based on positive or negative bounty
    bounty = task["bounty"]
    if bounty >= 0:
        released = db.release_escrow(result.task_id, result.provider_id, time.time())
        if released is None:
            db.add_balance(result.provider_id, bounty)
        new_balance = db.get_balance(result.provider_id)
        log_audit("ESCROW_RELEASE", result.provider_id, bounty, new_balance, result.task_id)
    else:
        # Data Market: Provider PAYS to receive this payload/task
        cost = abs(bounty)
        success = db.deduct_balance(result.provider_id, cost)
        if not success:
            log_event("data_purchase_failed", f"Provider {result.provider_id} lacks SECONDS to buy {result.task_id}", task_id=result.task_id, provider_id=result.provider_id, cost=cost)
            raise HTTPException(status_code=400, detail="Provider lacks SECONDS to buy this data")

        p_balance = db.get_balance(result.provider_id) or 0.0
        log_audit("BUY_DATA", result.provider_id, -cost, p_balance, result.task_id)

        db.add_balance(task["consumer_id"], cost) # The sender earns SECONDS
        c_balance = db.get_balance(task["consumer_id"]) or 0.0
        log_audit("SELL_DATA", task["consumer_id"], cost, c_balance, result.task_id)

    log_event("task_completed", f"Task {result.task_id[:8]} completed by {result.provider_id}", task_id=result.task_id, provider_id=result.provider_id, bounty=bounty)

    # Move task to completed
    async with task_lock:
        task["status"] = "completed"
        task["provider_id"] = result.provider_id
        task["result"] = result_payload
        completed_tasks[result.task_id] = task
        if result.task_id in active_tasks:
            del active_tasks[result.task_id]
    db.update_task_result(result.task_id, result.provider_id, result_payload, "completed", time.time(), result_uri=normalized_result_uri)

    # ROUTE RESULT BACK TO CONSUMER VIA WEBSOCKET
    consumer_id = task["consumer_id"]
    async with node_lock:
        consumer_ws = connected_nodes.get(consumer_id)
    if consumer_ws:
        try:
            await consumer_ws.send_json({
                "event": "task_result",
                "data": {
                    "task_id": result.task_id,
                    "provider_id": result.provider_id,
                    "result_payload": result_payload,
                    "result_uri": normalized_result_uri,
                    "bounty_spent": task["bounty"]
                }
            })
        except Exception as exc:
            log_event("deliver_result_failed", f"Failed to deliver result {result.task_id[:8]} to {consumer_id}: {exc}", task_id=result.task_id, consumer_id=consumer_id)
            async with node_lock:
                if connected_nodes.get(consumer_id) is consumer_ws:
                    del connected_nodes[consumer_id]

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
        "result_payload": task["result_payload"],
        "result_uri": task.get("result_uri")
    }

@app.post("/disputes/open")
async def open_dispute(payload: DisputeOpen, authenticated_node: str = Depends(verify_request)):
    task = db.get_task(payload.task_id)
    if not task or task["status"] != "completed":
        raise HTTPException(status_code=404, detail="Task not found or not completed")
    if task["consumer_id"] != authenticated_node:
        raise HTTPException(status_code=403, detail="Only the consumer can open a dispute")
    if task["updated_at"] and (time.time() - float(task["updated_at"])) > DISPUTE_WINDOW_SECONDS:
        raise HTTPException(status_code=400, detail="Dispute window expired")
    dispute_id = db.open_dispute(payload.task_id, task["consumer_id"], task["provider_id"], payload.reason, time.time())
    if dispute_id == "exists":
        raise HTTPException(status_code=409, detail="Dispute already exists")
    return {"status": "success", "dispute_id": dispute_id, "task_id": payload.task_id}

@app.post("/disputes/resolve")
async def resolve_dispute(payload: DisputeResolve, x_mep_admin_key: Optional[str] = Header(default=None)):
    _require_admin(x_mep_admin_key)
    resolution = payload.resolution.strip().lower()
    if resolution not in ("consumer", "provider"):
        raise HTTPException(status_code=400, detail="Resolution must be consumer or provider")
    if resolution == "consumer":
        chargeback = db.chargeback_escrow(payload.task_id, time.time())
        if chargeback["status"] == "invalid":
            raise HTTPException(status_code=400, detail="Escrow not eligible for chargeback")
        if chargeback["status"] == "insufficient":
            raise HTTPException(status_code=400, detail="Provider lacks funds for chargeback")
    if not db.resolve_dispute(payload.task_id, resolution, time.time()):
        raise HTTPException(status_code=404, detail="No open dispute found")
    return {"status": "success", "task_id": payload.task_id, "resolution": resolution}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/logs/ledger_audit.log", response_class=PlainTextResponse)
async def ledger_audit_log():
    path = _resolve_log_path("ledger_audit.log")
    if not path:
        raise HTTPException(status_code=404, detail="Audit log not found")
    lines = _tail_lines(path, 200)
    return PlainTextResponse("".join(lines))

@app.get("/ledger/entries")
async def ledger_entries(limit: int = 50, authenticated_node: str = Depends(verify_request)):
    safe_limit = max(1, min(limit, 200))
    entries = _read_audit_entries_for_node(authenticated_node, safe_limit)
    return {"node_id": authenticated_node, "entries": entries, "count": len(entries)}

@app.get("/events/recent")
async def recent_events(limit: int = 50, x_mep_admin_key: Optional[str] = Header(default=None)):
    _require_admin(x_mep_admin_key)
    safe_limit = max(1, min(limit, 200))
    events = _read_recent_events(safe_limit)
    return {"count": len(events), "events": events}

@app.get("/", response_class=HTMLResponse)
async def hub_landing(request: Request):
    async with node_lock:
        online_count = len(connected_nodes)
    async with task_lock:
        active_count = len(active_tasks)
    uptime_seconds = _get_system_uptime_seconds()
    uptime = _format_uptime(int(uptime_seconds)) if uptime_seconds is not None else "unknown"
    status = "online" if uptime_seconds is not None else "unknown"
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto in ("http", "https"):
        base_url = str(request.base_url).replace(request.url.scheme + "://", f"{forwarded_proto}://", 1).rstrip("/")
    else:
        base_url = str(request.base_url).rstrip("/")
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    total_nodes = db.get_node_count()
    last_completed_ts = db.get_last_completed_task_time()
    last_completed = datetime.utcfromtimestamp(last_completed_ts).strftime("%Y-%m-%d %H:%M:%S UTC") if last_completed_ts else "—"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MEP Hub 0</title>
  <style>
    body {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; margin: 0; padding: 20px; color: #111; background-color: #f9fafb; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 24px; max-width: 720px; margin: 0 auto; box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1); }}
    .kpi {{ font-size: 32px; font-weight: 700; line-height: 1.2; }}
    .label {{ color: #6b7280; font-size: 14px; font-weight: 500; }}
    .row {{ display: flex; gap: 24px; margin-top: 20px; flex-wrap: wrap; }}
    .section {{ margin-top: 24px; padding-top: 20px; border-top: 1px solid #f3f4f6; }}
    .mono {{ background: #f8fafc; padding: 12px; border-radius: 8px; font-size: 13px; overflow-wrap: break-word; word-break: break-all; border: 1px solid #e2e8f0; }}
    a {{ color: #2563eb; text-decoration: none; font-weight: 500; }}
    a:hover {{ text-decoration: underline; }}
    @media (max-width: 600px) {{
        body {{ padding: 16px; }}
        .card {{ padding: 16px; }}
        .row {{ gap: 16px; }}
        .kpi {{ font-size: 28px; }}
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="label">Welcome to MEP Hub 0</div>
    <div>Version {app.version} • Uptime {uptime} • Status {status}</div>
    <div class="row">
      <div>
        <div class="kpi">{online_count}</div>
        <div class="label">Bots online</div>
      </div>
      <div>
        <div class="kpi">{active_count}</div>
        <div class="label">Active tasks</div>
      </div>
      <div>
        <div class="kpi">{total_nodes}</div>
        <div class="label">Total nodes registered</div>
      </div>
    </div>
    <div class="section">
      <div class="label">Docs</div>
      <div><a href="https://github.com/WUAIBING/MEP/blob/main/README.md">GitHub README</a></div>
    </div>
    <div class="section">
      <div class="label">How to connect</div>
      <div class="mono">HUB_URL={base_url}<br>WS_URL={ws_url}</div>
    </div>
    <div class="section">
      <div class="label">Health</div>
      <div>
        <a href="#" onclick="checkHealth(event)">Check Status</a>
        <div id="health-status" class="mono" style="display:none; margin-top: 8px;"></div>
      </div>
    </div>
    <div class="section">
      <div class="label">Last task completed</div>
      <div>{last_completed}</div>
    </div>
    <div class="section" style="padding-bottom: 20px;">
      <div class="label">Auth headers</div>
      <div style="word-wrap: break-word;">Requests must include x-mep-nodeid, x-mep-timestamp, x-mep-signature.</div>
    </div>
  </div>
  <script>
    async function checkHealth(e) {{
      e.preventDefault();
      const el = document.getElementById('health-status');
      el.style.display = 'block';
      el.innerText = 'Checking...';
      try {{
        const res = await fetch('{base_url}/health');
        const data = await res.json();
        el.innerText = JSON.stringify(data, null, 2);
        el.style.color = 'green';
      }} catch (err) {{
        el.innerText = 'Error: ' + err.message;
        el.style.color = 'red';
      }}
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(html)

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
    async with node_lock:
        connected_nodes[node_id] = websocket
    db.update_registry_availability(node_id, "online", time.time())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        async with node_lock:
            if connected_nodes.get(node_id) is websocket:
                del connected_nodes[node_id]
        db.update_registry_availability(node_id, "offline", time.time())
