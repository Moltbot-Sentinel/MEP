import asyncio
import websockets
import json
import requests
import uuid

HUB_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"

async def test_direct_message():
    print("=== Testing MEP Direct Messaging (Zero Bounty) ===")
    
    # 1. Start Alice (Provider)
    alice_id = "alice-specialist-88"
    # Registration happens automatically now via Identity module, json={"pubkey": alice_id})
    
    # 2. Start Bob (Consumer)
    bob_id = "bob-general-12"
    # Registration happens automatically now via Identity module, json={"pubkey": bob_id})
    
    print(f"✅ Registered Alice ({alice_id}) and Bob ({bob_id})")
    
    async def alice_listen():
        async with websockets.connect(f"{WS_URL}/ws/{alice_id}") as ws:
            print("👧 Alice: Online and listening...")
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            
            print(f"👧 Alice: Received DIRECT MESSAGE!")
            print(f"👧 Alice: Payload: {data['data']['payload']}")
            print(f"👧 Alice: Bounty: {data['data']['bounty']} SECONDS")
            
            # Alice replies for free
            requests.post(f"{HUB_URL}/tasks/complete", json={
                "task_id": data['data']['id'],
                "provider_id": alice_id,
                "result_payload": "Yes Bob, I am available for a meeting tomorrow at 2 PM. Free of charge! 🐱"
            })
            print("👧 Alice: Sent reply!")

    async def bob_listen():
        async with websockets.connect(f"{WS_URL}/ws/{bob_id}") as ws:
            # Bob submits a direct task to Alice with 0 bounty
            await asyncio.sleep(1) # Let Alice connect first
            print("👦 Bob: Sending Direct Message to Alice (0.0 SECONDS)...")
            requests.post(f"{HUB_URL}/tasks/submit", json={
                "consumer_id": bob_id,
                "payload": "Hey Alice, are you free for a meeting tomorrow at 2 PM?",
                "bounty": 0.0,
                "target_node": alice_id
            })
            
            # Bob waits for Alice's reply
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            print(f"👦 Bob: Received reply from {data['data']['provider_id']}:")
            print(f"👦 Bob: \"{data['data']['result_payload']}\"")

    await asyncio.gather(alice_listen(), bob_listen())
    print("=== Direct Messaging Test Complete! ===")

if __name__ == "__main__":
    asyncio.run(test_direct_message())