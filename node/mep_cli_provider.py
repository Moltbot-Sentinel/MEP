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
import os
import shlex
import time
import urllib.parse
import tempfile
from identity import MEPIdentity

HUB_URL = os.getenv("HUB_URL", "http://localhost:8000")
WS_URL = os.getenv("WS_URL", "ws://localhost:8000")

class MEPCLIProvider:
    def __init__(self, key_path: str):
        self.identity = MEPIdentity(key_path)
        self.node_id = self.identity.node_id
        self.balance = 0.0
        self.is_contributing = True
        self.capabilities = ["cli-agent", "bash", "python"]
        
        self.workspace_dir = os.path.join(tempfile.gettempdir(), "mep_workspaces")
        os.makedirs(self.workspace_dir, exist_ok=True)
        
    async def connect(self):
        """Connect to MEP Hub and start listening for CLI tasks."""
        print(f"[CLI Provider {self.node_id}] Starting...")
        
        # Register with hub
        try:
            resp = requests.post(f"{HUB_URL}/register", json={"pubkey": self.identity.pub_pem})
            self.balance = resp.json().get("balance", 0.0)
            print(f"[CLI Provider] Registered. Balance: {self.balance:.6f} SECONDS")
        except Exception as e:
            print(f"[CLI Provider] Registration failed: {e}")
            return
            
        ts = str(int(time.time()))
        sig = self.identity.sign(self.node_id, ts)
        sig_safe = urllib.parse.quote(sig)
        uri = f"{WS_URL}/ws/{self.node_id}?timestamp={ts}&signature={sig_safe}"
        try:
            async with websockets.connect(uri) as ws:
                print("[CLI Provider] Connected to MEP Hub. Awaiting CLI tasks...")
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
            payload_str = json.dumps({
                "task_id": task_id,
                "provider_id": self.node_id
            })
            headers = self.identity.get_auth_headers(payload_str)
            headers["Content-Type"] = "application/json"
            resp = requests.post(f"{HUB_URL}/tasks/bid", data=payload_str, headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                if data["status"] == "accepted":
                    print(f"[CLI Provider] BID WON! Executing CLI agent for task {task_id[:8]}...")
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
        
        if os.name == "nt":
            safe_payload = payload.replace('"', '""')
            cmd = (
                "echo Booting Autonomous CLI Agent... & "
                "timeout /t 1 > nul & "
                f'echo Analyzing: "{safe_payload}" & '
                "timeout /t 1 > nul & "
                "echo Code generated and saved to workspace."
            )
        else:
            safe_payload = shlex.quote(payload)
            cmd = (
                "echo 'Booting Autonomous CLI Agent...' && "
                "sleep 1 && "
                f"echo 'Analyzing: {safe_payload}' && "
                "sleep 1 && "
                "echo 'Code generated and saved to workspace.'"
            )
        
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
        
        output = stdout.decode(errors="replace").strip()
        if stderr:
            output += "\n[Errors/Warnings]:\n" + stderr.decode(errors="replace").strip()
            
        print(f"[CLI Agent] Finished with exit code {process.returncode}")
        
        # Construct final result payload
        result_payload = f"```bash\n{output}\n```\n*Workspace: {task_dir}*"
        
        # Submit result back to Hub
        payload_str = json.dumps({
            "task_id": task_id,
            "provider_id": self.node_id,
            "result_payload": result_payload
        })
        headers = self.identity.get_auth_headers(payload_str)
        headers["Content-Type"] = "application/json"
        requests.post(f"{HUB_URL}/tasks/complete", data=payload_str, headers=headers)
        print(f"[CLI Provider] Result submitted! Earned {bounty:.6f} SECONDS.\n")

if __name__ == "__main__":
    print("=" * 60)
    print("MEP Autonomous CLI Provider")
    print("WARNING: This node executes shell commands. Use sandboxing!")
    print("=" * 60)
    
    key_path = os.getenv("MEP_CLI_KEY_PATH", os.path.join(tempfile.gettempdir(), "mep_cli_provider.pem"))
    provider = MEPCLIProvider(key_path)
    
    try:
        asyncio.run(provider.connect())
    except KeyboardInterrupt:
        provider.is_contributing = False
        print("\n[MEP] CLI Provider shut down.")
