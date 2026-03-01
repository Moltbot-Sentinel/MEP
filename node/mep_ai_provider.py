#!/usr/bin/env python3
"""
MEP AI Provider - For AI agents like Hub Sentinel (Gemini 3.1 Pro).
Based on mep_cli_provider.py but uses AI API instead of CLI.
"""
import asyncio
import os
import sys
import json
import subprocess
import tempfile
import shutil
from typing import Optional
import aiohttp
import websockets
import requests
from identity_enhanced import MEPIdentityEnhanced

HUB_URL = os.getenv("HUB_URL", "https://mep-hub.silentcopilot.ai")
WS_URL = os.getenv("WS_URL", "wss://mep-hub.silentcopilot.ai")

class MEPAIProvider:
    def __init__(self, key_path: str):
        self.identity = MEPIdentityEnhanced(key_path)
        self.node_id = self.identity.node_id
        self.balance = 0.0
        self.is_mining = True
        self.workspace_dir = os.path.join(tempfile.gettempdir(), "mep_ai_workspaces")
        os.makedirs(self.workspace_dir, exist_ok=True)
        
        # AI API configuration (override in environment)
        self.ai_api_cmd = os.getenv("MEP_AI_AGENT_CMD", "echo")
        
    async def connect(self):
        """Connect to MEP Hub and start mining."""
        print(f"[AI Provider {self.node_id}] Starting...")
        
        # Register with hub
        try:
            resp = requests.post(
                f"{HUB_URL}/register",
                json={
                    "pubkey": "dummy",  # Hub expects this
                    "capabilities": ["ai-agent"],
                    "x25519_public_key": self.identity.get_x25519_public_key().hex()
                },
                headers=self.identity.get_auth_headers(""),
                timeout=10
            )
            data = resp.json()
            self.balance = data.get("balance", 0.0)
            print(f"[AI Provider {self.node_id}] Registered. Balance: {self.balance:.6f} SECONDS")
        except Exception as e:
            print(f"[AI Provider {self.node_id}] Registration failed: {e}")
            return
        
        # WebSocket connection
        uri = f"{WS_URL.replace('https://', 'wss://').replace('http://', 'ws://')}/ws?node_id={self.node_id}"
        try:
            async with websockets.connect(uri) as ws:
                print(f"[AI Provider {self.node_id}] Connected to MEP Hub. Awaiting AI tasks...")
                
                while self.is_mining:
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
                        print("[AI Provider] Connection closed")
                        break
        except Exception as e:
            print(f"[AI Provider] WebSocket error: {e}")
    
    async def handle_rfc(self, rfc_data: dict):
        """Evaluate Request For Compute and submit Bid."""
        task_id = rfc_data["id"]
        bounty = rfc_data["bounty"]
        
        # Safety: Don't buy expensive data unless allowed
        max_purchase_price = float(os.getenv("MEP_MAX_PURCHASE_PRICE", "0.0"))
        if bounty < max_purchase_price:
            print(f"[AI Provider] Ignored RFC {task_id[:8]} (Bounty {bounty} exceeds max purchase)")
            return
            
        print(f"[AI Provider] Received RFC {task_id[:8]} for {bounty:.6f} SECONDS. Placing bid...")
        
        try:
            payload_str = json.dumps({
                "task_id": task_id,
                "provider_id": self.node_id
            })
            headers = self.identity.get_auth_headers(payload_str)
            headers["Content-Type"] = "application/json"
            resp = requests.post(f"{HUB_URL}/tasks/bid", data=payload_str, headers=headers, timeout=10)
            
            if resp.status_code == 200:
                bid_data = resp.json()
                print(f"[AI Provider] Bid accepted! Task assigned.")
                # Process task with secret_data if provided
                secret_data = bid_data.get("secret_data")
                await self.process_task(rfc_data, secret_data=secret_data)
            else:
                print(f"[AI Provider] Bid failed: {resp.text}")
        except Exception as e:
            print(f"[AI Provider] Bid error: {e}")
    
    async def process_task(self, task_data: dict, secret_data: Optional[str] = None):
        """Execute the task using AI API."""
        task_id = task_data["id"]
        payload = task_data["payload"]
        payload_uri = task_data.get("payload_uri")
        bounty = task_data["bounty"]
        
        print(f"[AI Provider] Processing task {task_id[:8]} for {bounty:.6f} SECONDS")
        
        # Handle Data Market purchase
        if secret_data:
            print(f"[AI Provider] 💾 Received purchased data")
            # TODO: Decrypt if X25519 encrypted
        
        # Download payload from IPFS if provided
        if payload_uri:
            print(f"[AI Provider] 📥 Downloading from {payload_uri}...")
            # TODO: Implement download like in mep_cli_provider.py
        
        # Call AI API
        print(f"[AI Provider] 🤖 Thinking about: {payload[:50]}...")
        
        try:
            # Use configured AI command
            cmd = self.ai_api_cmd.split()
            cmd.append(payload)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                ai_response = result.stdout.strip()
                print(f"[AI Provider] ✅ AI response generated")
            else:
                ai_response = f"AI API error: {result.stderr}"
                print(f"[AI Provider] ❌ AI API failed: {result.stderr}")
            
            # Submit result
            result_payload = {
                "task_id": task_id,
                "provider_id": self.node_id,
                "result_payload": ai_response
            }
            
            result_str = json.dumps(result_payload)
            headers = self.identity.get_auth_headers(result_str)
            headers["Content-Type"] = "application/json"
            
            resp = requests.post(f"{HUB_URL}/tasks/complete", data=result_str, headers=headers, timeout=20)
            
            if resp.status_code == 200:
                data = resp.json()
                self.balance = data["new_balance"]
                print(f"[AI Provider] Earned {bounty:.6f} SECONDS!")
                print(f"  New balance: {self.balance:.6f} SECONDS")
            else:
                print(f"[AI Provider] Failed to submit: {resp.text}")
                
        except Exception as e:
            print(f"[AI Provider] Processing error: {e}")
    
    def stop(self):
        """Stop mining."""
        self.is_mining = False
        print(f"[AI Provider {self.node_id}] Stopping...")

async def main():
    key_dir = os.getenv("MEP_KEY_DIR", os.path.join(os.path.expanduser("~"), ".mep"))
    os.makedirs(key_dir, exist_ok=True)
    key_path = os.getenv("MEP_PROVIDER_KEY_PATH", os.path.join(key_dir, "mep_ai_provider.pem"))
    
    provider = MEPAIProvider(key_path)
    try:
        await provider.connect()
    except KeyboardInterrupt:
        provider.stop()

if __name__ == "__main__":
    asyncio.run(main())
