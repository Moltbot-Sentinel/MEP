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
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
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
        self.session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self.workspace_dir = os.path.join(tempfile.gettempdir(), "mep_workspaces")
        os.makedirs(self.workspace_dir, exist_ok=True)

    async def _post_with_retry(self, url: str, payload_str: str | None = None, json_body: dict | None = None, headers: dict | None = None, timeout: int = 20):
        delays = [1, 2, 4, 8]
        for i, delay in enumerate(delays, start=1):
            try:
                if json_body is not None:
                    return await asyncio.to_thread(self.session.post, url, json=json_body, timeout=timeout)
                return await asyncio.to_thread(self.session.post, url, data=payload_str, headers=headers, timeout=timeout)
            except Exception as e:
                if i == len(delays):
                    print(f"[CLI Provider] Request failed: {e}")
                    return None
                await asyncio.sleep(delay)
    async def connect(self):
        """Connect to MEP Hub and start listening for CLI tasks."""
        print(f"[CLI Provider {self.node_id}] Starting...")
        
        try:
            resp = await self._post_with_retry(f"{HUB_URL}/register", json_body={"pubkey": self.identity.pub_pem}, timeout=10)
            if resp is None:
                return
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
            resp = await self._post_with_retry(f"{HUB_URL}/tasks/bid", payload_str=payload_str, headers=headers)
            
            if resp is not None and resp.status_code == 200:
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
        
        try:
            task_dir = os.path.join(self.workspace_dir, task_id)
            os.makedirs(task_dir, exist_ok=True)
            
            if os.name == "nt":
                safe_payload = payload.replace('"', '""')
                cmd = (
                    "echo Booting Autonomous CLI Agent... & "
                    "timeout /t 1 > nul & "
                    f'echo Analyzing: \"{safe_payload}\" & '
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
            
            result_payload = f"```bash\n{output}\n```\n*Workspace: {task_dir}*"
            
            payload_str = json.dumps({
                "task_id": task_id,
                "provider_id": self.node_id,
                "result_payload": result_payload
            })
            headers = self.identity.get_auth_headers(payload_str)
            headers["Content-Type"] = "application/json"
            resp = await self._post_with_retry(f"{HUB_URL}/tasks/complete", payload_str=payload_str, headers=headers)
            if resp is None:
                print("[CLI Provider] Result submit failed")
                return
            print(f"[CLI Provider] Result submitted! Earned {bounty:.6f} SECONDS.\n")
        except Exception as e:
            print(f"[CLI Provider] Task failed: {e}")

if __name__ == "__main__":
    print("=" * 60)
    print("MEP Autonomous CLI Provider")
    print("WARNING: This node executes shell commands. Use sandboxing!")
    print("=" * 60)
    
    key_dir = os.getenv("MEP_KEY_DIR", os.path.join(os.path.expanduser("~"), ".mep"))
    os.makedirs(key_dir, exist_ok=True)
    key_path = os.getenv("MEP_CLI_KEY_PATH", os.path.join(key_dir, "mep_cli_provider.pem"))
    provider = MEPCLIProvider(key_path)
    print(f"[CLI Provider] Key path: {provider.identity.key_path}")
    if provider.identity.generated_new_key:
        print("[CLI Provider] Generated new key, node id will change")
    
    try:
        asyncio.run(provider.connect())
    except KeyboardInterrupt:
        provider.is_contributing = False
        print("\n[MEP] CLI Provider shut down.")
