import asyncio
import json
import requests
import websockets
import time
import urllib.parse
from identity import MEPIdentity
import uuid
from typing import Optional

HUB_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"

def get_auth_url(identity: MEPIdentity):
    ts = str(int(time.time()))
    sig = identity.sign(identity.node_id, ts)
    sig_safe = urllib.parse.quote(sig)
    return f"{WS_URL}/{identity.node_id}?timestamp={ts}&signature={sig_safe}"

def submit_task(identity: MEPIdentity, payload: str, bounty: float, target: Optional[str] = None):
    data = {
        "consumer_id": identity.node_id,
        "payload": payload,
        "bounty": bounty
    }
    if target:
        data["target_node"] = target
    
    payload_str = json.dumps(data)
    headers = identity.get_auth_headers(payload_str)
    headers["Content-Type"] = "application/json"
    r = requests.post(f"{HUB_URL}/tasks/submit", data=payload_str, headers=headers)
    return r.json()

def place_bid(identity: MEPIdentity, task_id: str):
    data = {
        "task_id": task_id,
        "provider_id": identity.node_id
    }
    payload_str = json.dumps(data)
    headers = identity.get_auth_headers(payload_str)
    headers["Content-Type"] = "application/json"
    r = requests.post(f"{HUB_URL}/tasks/bid", data=payload_str, headers=headers)
    return r.json()

def complete_task(identity: MEPIdentity, task_id: str, result: str):
    data = {
        "task_id": task_id,
        "provider_id": identity.node_id,
        "result_payload": result
    }
    payload_str = json.dumps(data)
    headers = identity.get_auth_headers(payload_str)
    headers["Content-Type"] = "application/json"
    r = requests.post(f"{HUB_URL}/tasks/complete", data=payload_str, headers=headers)
    return r.json()

def get_balance(identity: MEPIdentity):
    r = requests.get(f"{HUB_URL}/balance/{identity.node_id}")
    return r.json().get("balance_seconds", 0.0)

async def test_three_markets():
    print("=" * 60)
    print("Testing the 3 MEP Markets (+, 0, -)")
    print("=" * 60)
    
    alice = MEPIdentity(f"alice_{uuid.uuid4().hex[:6]}.pem")
    bob = MEPIdentity(f"bob_{uuid.uuid4().hex[:6]}.pem")
    
    requests.post(f"{HUB_URL}/register", json={"pubkey": alice.pub_pem})
    requests.post(f"{HUB_URL}/register", json={"pubkey": bob.pub_pem})
    
    print(f"👩 Alice (Consumer): {alice.node_id} | Starting Bal: {get_balance(alice)}")
    print(f"👦 Bob   (Provider): {bob.node_id} | Starting Bal: {get_balance(bob)}\n")

    async def bob_listener():
        async with websockets.connect(get_auth_url(bob)) as ws:
            # 1. Wait for Compute Market RFC (+5.0)
            msg = await ws.recv()
            data = json.loads(msg)
            if data["event"] == "rfc" and data["data"]["bounty"] > 0:
                task_id = data["data"]["id"]
                print(f"👦 Bob: Received Compute RFC {task_id[:8]} for +{data['data']['bounty']} SECONDS")
                bid_res = place_bid(bob, task_id)
                if bid_res["status"] == "accepted":
                    print("👦 Bob: Won Compute Bid! Completing task...")
                    complete_task(bob, task_id, "Here is the code you requested.")
                    print("👦 Bob: Compute task done.\n")

            # 2. Wait for Cyberspace Direct Message (0.0)
            msg = await ws.recv()
            data = json.loads(msg)
            if data["event"] == "new_task" and data["data"]["bounty"] == 0.0:
                task_id = data["data"]["id"]
                print(f"👦 Bob: Received Cyberspace DM {task_id[:8]} from Alice (0.0 SECONDS)")
                print(f"👦 Bob: Message = '{data['data']['payload']}'")
                complete_task(bob, task_id, "Yes Alice, I am free.")
                print("👦 Bob: Sent free reply.\n")

            # 3. Wait for Data Market RFC (-2.0)
            msg = await ws.recv()
            data = json.loads(msg)
            if data["event"] == "rfc" and data["data"]["bounty"] < 0:
                task_id = data["data"]["id"]
                cost = data["data"]["bounty"]
                print(f"👦 Bob: Received Data Market RFC {task_id[:8]} costing {cost} SECONDS")
                
                # Bob's local configuration allows him to spend up to 5.0 SECONDS
                max_purchase_price = 5.0
                cost = abs(data["data"]["bounty"])
                if cost <= max_purchase_price:
                    print("👦 Bob: Budget allows it! Bidding on premium data...")
                    bid_res = place_bid(bob, task_id)
                    if bid_res["status"] == "accepted":
                        print(f"👦 Bob: Paid {abs(cost)} SECONDS to download premium data: '{bid_res['payload']}'")
                        complete_task(bob, task_id, "Data received successfully.")
                        print("👦 Bob: Premium data acquisition complete.\n")
                else:
                    print("👦 Bob: Too expensive. Ignored.")
                    
            await asyncio.sleep(0.5)

    async def alice_sender():
        # Let Bob connect
        await asyncio.sleep(0.5)
        
        async with websockets.connect(get_auth_url(alice)) as ws:
            # Market 1: Compute Market (+5.0)
            print("👩 Alice: Submitting Compute Task (+5.0 SECONDS)...")
            submit_task(alice, "Write me a python script", 5.0)
            await asyncio.wait_for(ws.recv(), timeout=6.0)
            
            # Market 2: Cyberspace Market (0.0)
            print("👩 Alice: Sending Cyberspace DM to Bob (0.0 SECONDS)...")
            submit_task(alice, "Are you free to chat?", 0.0, target=bob.node_id)
            await asyncio.wait_for(ws.recv(), timeout=6.0)
            
            # Market 3: Data Market (-2.0)
            print("👩 Alice: Broadcasting Premium Dataset (-2.0 SECONDS)...")
            submit_task(alice, "SECRET_TRADING_ALGO_V9", -2.0)
            await asyncio.wait_for(ws.recv(), timeout=6.0)
            
            await asyncio.sleep(0.5)

    await asyncio.gather(bob_listener(), alice_sender())
    
    print("=" * 60)
    print("Final Balances:")
    print(f"👩 Alice (Started 10.0): {get_balance(alice)} (Paid 5.0, Earned 2.0 = Expected 7.0)")
    print(f"👦 Bob   (Started 10.0): {get_balance(bob)} (Earned 5.0, Paid 2.0 = Expected 13.0)")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_three_markets())
