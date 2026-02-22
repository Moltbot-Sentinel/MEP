import os
import json
from typing import Dict, Any

class SleepingAPI:
    """
    The L2 integration. Exposes local LLM APIs securely when the owner is asleep.
    """
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.is_sleeping = False
        self.available_models = ["gemini-3.1-pro-preview", "minimax-m2.5"]
        self.rate_multiplier = 1.0 # 1 SECOND per 1000 tokens (mock)
        
    def set_sleep_state(self, state: bool):
        self.is_sleeping = state
        print(f"[Node {self.node_id}] Sleep state set to: {'ASLEEP (Mining)' if state else 'AWAKE (Consuming)'}")

    def evaluate_task(self, task_payload: Dict[str, Any]) -> bool:
        """
        Evaluate if we want to take this task based on bounty and complexity.
        """
        if not self.is_sleeping:
            return False
            
        bounty = task_payload.get("bounty", 0)
        estimated_cost = len(task_payload.get("payload", "")) * 0.01 * self.rate_multiplier
        
        return bounty >= estimated_cost

    def execute_task(self, payload: str) -> str:
        """
        Mock execution. In reality, this routes to the local API via Clawdbot.
        """
        if not self.is_sleeping:
            raise PermissionError("Owner is awake. API locked.")
        
        # Simulate local LLM call
        return f"Processed payload '{payload[:10]}...' using local sleeping API."
