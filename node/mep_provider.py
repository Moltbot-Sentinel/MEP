#!/usr/bin/env python3
"""
Miao Exchange Protocol (MEP) Miner
A sleeping node that earns SECONDS by processing tasks.
"""
import asyncio
import json
import websockets
import requests
import uuid
import sys
import os
import time
import urllib.parse
import tempfile

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from identity import MEPIdentity

HUB_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"

class MEPProvider:
    def __init__(self, key_path: str):
        self.identity = MEPIdentity(key_path)
        self.node_id = self.identity.node_id
        self.balance = 0.0
        self.is_mining = True
        
    async def connect(self):
        """Connect to MEP Hub and start mining."""
        print(f"[MEP Provider {self.node_id}] Starting...")
        
        # Register with hub
        try:
            resp = requests.post(f"{HUB_URL}/register", json={"pubkey": self.identity.pub_pem})
            data = resp.json()
            self.balance = data.get("balance", 0.0)
            print(f"[MEP Provider {self.node_id}] Registered. Balance: {self.balance:.6f} SECONDS")
        except Exception as e:
            print(f"[MEP Provider {self.node_id}] Registration failed: {e}")
            return
        
        # Connect to WebSocket
        ts = str(int(time.time()))
        sig = self.identity.sign(self.node_id, ts)
        sig_safe = urllib.parse.quote(sig)
        uri = f"{WS_URL}/ws/{self.node_id}?timestamp={ts}&signature={sig_safe}"
        try:
            async with websockets.connect(uri) as ws:
                print(f"[MEP Provider {self.node_id}] Connected to MEP Hub")
                
                # Listen for tasks
                while self.is_mining:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(msg)
                        
                        if data["event"] == "new_task":
                            await self.process_task(data["data"])
                        elif data["event"] == "rfc":
                            await self.handle_rfc(data["data"])
                            
                    except asyncio.TimeoutError:
                        continue  # Keep connection alive
                    except websockets.exceptions.ConnectionClosed:
                        print(f"[MEP Provider {self.node_id}] Connection closed")
                        break
                        
        except Exception as e:
            print(f"[MEP Provider {self.node_id}] WebSocket error: {e}")
    
    async def handle_rfc(self, rfc_data: dict):
        """Phase 2: Evaluate Request For Compute and submit Bid."""
        task_id = rfc_data["id"]
        bounty = rfc_data["bounty"]
        # SAFETY SWITCH: Prevent purchasing data unless explicitly allowed
        max_purchase_price = 0.0 # Set to e.g., -5.0 to buy premium data
        if bounty < max_purchase_price:
            print(f"[MEP Provider {self.node_id}] Ignored RFC {task_id[:8]} (Bounty {bounty} exceeds max purchase price)")
            return
            
        print(f"[MEP Provider {self.node_id}] Received RFC {task_id[:8]} for {bounty:.6f} SECONDS. Placing bid...")
        
        # Place bid
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
                    print(f"[MEP Provider {self.node_id}] 🏁 BID WON for task {task_id[:8]}! Processing payload...")
                    
                    # Reconstruct task_data to pass to process_task
                    task_data = {
                        "id": task_id,
                        "payload": data["payload"],
                        "bounty": bounty,
                        "consumer_id": data["consumer_id"]
                    }
                    await self.process_task(task_data)
                else:
                    print(f"[MEP Provider {self.node_id}] Bid rejected (too slow): {data.get('detail', '')}")
        except Exception as e:
            print(f"[MEP Provider {self.node_id}] Error placing bid: {e}")

    async def process_task(self, task_data: dict):
        """Process a task and earn SECONDS."""
        task_id = task_data["id"]
        payload = task_data["payload"]
        bounty = task_data["bounty"]
        print(f"[MEP Provider {self.node_id}] Received task {task_id[:8]} for {bounty:.6f} SECONDS")
        print(f"  Payload: {payload[:50]}...")
        
        # Simulate processing (in real version, this would call local LLM API)
        await asyncio.sleep(0.5)  # Simulate thinking
        
        # Generate a realistic response
        result = f"""I've processed your request: "{payload[:30]}..."

As a MEP miner, I analyzed this task and generated the following response:

The core concept here aligns with the Miao Exchange Protocol philosophy - creating efficient yet human-centric compute exchange. The SECONDS-based economy allows for precise valuation of AI compute time while maintaining the essential "Miao" moments of unpredictability.

Key insights:
1. Time is the fundamental currency of computation
2. 6 decimal precision allows micro-transactions
3. The protocol enables global compute liquidity

Would you like me to elaborate on any specific aspect?"""
        
        # Submit result
        try:
            payload_str = json.dumps({
                "task_id": task_id,
                "provider_id": self.node_id,
                "result_payload": result
            })
            headers = self.identity.get_auth_headers(payload_str)
            headers["Content-Type"] = "application/json"
            resp = requests.post(f"{HUB_URL}/tasks/complete", data=payload_str, headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                self.balance = data["new_balance"]
                print(f"[MEP Provider {self.node_id}] Earned {bounty:.6f} SECONDS!")
                print(f"  New balance: {self.balance:.6f} SECONDS")
            else:
                print(f"[MEP Provider {self.node_id}] Failed to submit: {resp.text}")
                
        except Exception as e:
            print(f"[MEP Provider {self.node_id}] Submission error: {e}")
    
    def stop(self):
        """Stop mining."""
        self.is_mining = False
        print(f"[MEP Provider {self.node_id}] Stopping...")

async def main():
    key_path = os.path.join(tempfile.gettempdir(), f"mep_provider_{uuid.uuid4().hex[:8]}.pem")
    miner = MEPProvider(key_path)
    
    try:
        await miner.connect()
    except KeyboardInterrupt:
        miner.stop()
        print("\n[MEP] Contribution stopped by user")

if __name__ == "__main__":
    print("=" * 60)
    print("Miao Exchange Protocol (MEP) Miner")
    print("Earn SECONDS by contributing idle compute")
    print("=" * 60)
    asyncio.run(main())
