from pydantic import BaseModel, Field
from typing import Optional

class NodeRegistration(BaseModel):
    pubkey: str = Field(..., description="Node's public key or UUID")
    alias: Optional[str] = None

class TaskCreate(BaseModel):
    consumer_id: str
    payload: str
    bounty: float
    target_node: Optional[str] = None  # Direct messaging / specific bot targeting
    model_requirement: Optional[str] = None

class TaskBid(BaseModel):
    task_id: str
    provider_id: str

class TaskResult(BaseModel):
    task_id: str
    provider_id: str
    result_payload: str

class TaskCancel(BaseModel):
    task_id: str

class NodeBalance(BaseModel):
    node_id: str
    balance_seconds: float
