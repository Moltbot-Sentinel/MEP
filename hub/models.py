from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class NodeRegistration(BaseModel):
    pubkey: str = Field(..., description="Node's public key or UUID")
    alias: Optional[str] = None

class TaskCreate(BaseModel):
    consumer_id: str
    payload: str
    bounty: float
    target_node: Optional[str] = None  # Direct messaging / specific bot targeting
    model_requirement: Optional[str] = None
    secret_data: Optional[str] = None

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

class RegistryUpdate(BaseModel):
    alias: Optional[str] = None
    skills: Optional[List[str]] = None
    models: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    availability: Optional[str] = None

class AvailabilityUpdate(BaseModel):
    availability: str

class RegistryHeartbeat(BaseModel):
    availability: Optional[str] = None

class ReputationSubmit(BaseModel):
    task_id: str
    provider_id: str
    rating: int

class DisputeOpen(BaseModel):
    task_id: str
    reason: str

class DisputeResolve(BaseModel):
    task_id: str
    resolution: str
