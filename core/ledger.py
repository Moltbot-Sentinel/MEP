import uuid
import time
from typing import Dict

class ChronosLedger:
    """
    L1 Ledger: Tracks Identity and SECONDS balance.
    No economics, no inflation control, just pure time accounting.
    """
    def __init__(self):
        self.accounts: Dict[str, float] = {}  # Node ID -> Balance in SECONDS
        self.tasks: Dict[str, dict] = {}      # Task ID -> Task details

    def register_node(self, node_id: str) -> None:
        if node_id not in self.accounts:
            self.accounts[node_id] = 0.0
            print(f"[Ledger] Node registered: {node_id}")

    def create_task(self, consumer_id: str, payload: str, bounty_seconds: float) -> str:
        """
        Consumer creates a task, locking in SECONDS.
        """
        if self.accounts.get(consumer_id, 0) < bounty_seconds:
            raise ValueError("Insufficient SECONDS balance.")
        
        task_id = str(uuid.uuid4())
        self.accounts[consumer_id] -= bounty_seconds
        
        self.tasks[task_id] = {
            "consumer": consumer_id,
            "payload": payload,
            "bounty": bounty_seconds,
            "status": "pending",
            "provider": None
        }
        print(f"[Ledger] Task {task_id} created by {consumer_id} for {bounty_seconds}s.")
        return task_id

    def submit_result(self, task_id: str, provider_id: str, result: str) -> bool:
        """
        Provider submits result and claims the bounty.
        """
        task = self.tasks.get(task_id)
        if not task or task["status"] != "pending":
            return False
            
        task["status"] = "completed"
        task["provider"] = provider_id
        task["result"] = result
        
        self.register_node(provider_id)
        self.accounts[provider_id] += task["bounty"]
        print(f"[Ledger] Task {task_id} completed by {provider_id}. Earned {task['bounty']}s.")
        return True

    def get_balance(self, node_id: str) -> float:
        return self.accounts.get(node_id, 0.0)

# Global singleton for simulation
_global_ledger = ChronosLedger()

def get_ledger() -> ChronosLedger:
    return _global_ledger
