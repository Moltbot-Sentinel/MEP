#!/usr/bin/env python3
"""
MEP CLI Provider
A specialized node that routes tasks to local autonomous CLI agents 
(e.g., Aider, Claude-Code, Open-Interpreter).
"""
import asyncio
import websockets
import json
import requests
import uuid
import sys
import os
import shlex

HUB_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"

class MEPCLIProvider:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.balance = 0.0
        self.is_contributing = True
        self.capabilities = ["cli-agent", "bash", "python"]
        
        # Security: In production, run this inside a Docker container!
        self.workspace_dir = "/tmp/mep_workspaces"
        os.makedirs(self.workspace_dir, exist_ok=True)
        
    async def connect(self):
        """Connect to MEP Hub and start listening for CLI tasks."""
        print(f"[CLI Provider {self.node_id}] Starting...")
        
        # Register with hub
        try:
            resp = # Registration happens automatically now via Identity module, json={"pubkey": self.node_id})
            self.balance = resp.json().get("balance", 0.0)
            print(f"[CLI Provider] Registered. Balance: {self.balance:.6f} SECONDS")
        except Exception as e:
            print(f"[CLI Provider] Registration failed: {e}")
            return
            
        uri = f"{WS_URL}/ws/{self.node_id}"
        try:
            async with websockets.connect(uri) as ws:
                print(f"[CLI Provider] Connected to MEP Hub. Awaiting CLI tasks...")
                while self.is_contributing:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(msg)
                        
                        if data["event"] == "new_task":
                            await self.process_task(data["data"])
                        elif data["event"] == "rfc":
                            await self.handle_rfc(data["data"])
                            
                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        print("[CLI Provider] Connection closed")
                        break
        except Exception as e:
            print(f"[CLI Provider] WebSocket error: {e}")

    async def handle_rfc(self, rfc_data: dict):
        """Evaluate if we should bid on this CLI task."""
        task_id = rfc_data["id"]
        bounty = rfc_data["bounty"]
        model = rfc_data.get("model_requirement")
        
        # Only bid if the consumer specifically requested a CLI agent
        if model not in self.capabilities and model is not None:
            return
            
        print(f"[CLI Provider] Received matching RFC {task_id[:8]} for {bounty:.6f} SECONDS. Bidding...")
        
        try:
            resp = requests.post(f"{HUB_URL}/tasks/bid", json={
                "task_id": task_id,
                "provider_id": self.node_id
            })
            
            if resp.status_code == 200:
                data = resp.json()
                if data["status"] == "accepted":
                    print(f"[CLI Provider] 🏁 BID WON! Executing CLI agent for task {task_id[:8]}...")
                    task_data = {
                        "id": task_id,
                        "payload": data["payload"],
                        "bounty": bounty,
                        "consumer_id": data["consumer_id"]
                    }
                    # Run it in background so we don't block the websocket
                    asyncio.create_task(self.process_task(task_data))
        except Exception as e:
            print(f"[CLI Provider] Error placing bid: {e}")

    async def process_task(self, task_data: dict):
        """Execute the task using a local CLI agent."""
        task_id = task_data["id"]
        payload = task_data["payload"]
        bounty = task_data["bounty"]
        
        # Create an isolated workspace for this task
        task_dir = os.path.join(self.workspace_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)
        
        # Safely escape the payload to prevent shell injection
        safe_payload = shlex.quote(payload)
        
        # --- COMMAND TEMPLATE ---
        # Replace this with: f"aider --message {safe_payload}" 
        # or: f"claude-code --print {safe_payload}"
        cmd = f"echo '⚙️ Booting Autonomous CLI Agent...' && sleep 1 && echo 'Analyzing: {safe_payload}' && sleep 1 && echo '✅ Code generated and saved to workspace.'"
        
        print(f"\n[CLI Agent] Executing in {task_dir}:")
        print(f"$ {cmd[:100]}...\n")
        
        # Run the subprocess
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=task_dir
        )
        
        stdout, stderr = await process.communicate()
        
        output = stdout.decode().strip()
        if stderr:
            output += "\n[Errors/Warnings]:\n" + stderr.decode().strip()
            
        print(f"[CLI Agent] Finished with exit code {process.returncode}")
        
        # Construct final result payload
        result_payload = f"```bash\n{output}\n```\n*Workspace: {task_dir}*"
        
        # Submit result back to Hub
        requests.post(f"{HUB_URL}/tasks/complete", json={
            "task_id": task_id,
            "provider_id": self.node_id,
            "result_payload": result_payload
        })
        print(f"[CLI Provider] Result submitted! Earned {bounty:.6f} SECONDS.\n")

if __name__ == "__main__":
    print("=" * 60)
    print("MEP Autonomous CLI Provider")
    print("WARNING: This node executes shell commands. Use sandboxing!")
    print("=" * 60)
    
    provider_id = f"cli-agent-{uuid.uuid4().hex[:6]}"
    provider = MEPCLIProvider(provider_id)
    
    try:
        asyncio.run(provider.connect())
    except KeyboardInterrupt:
        provider.is_contributing = False
        print("\n[MEP] CLI Provider shut down.")
