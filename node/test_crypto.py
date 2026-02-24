import asyncio
import requests
import json
import websockets
from identity import MEPIdentity

HUB_URL = "http://localhost:8000"

async def test():
    # 1. Generate identity automatically
    bot = MEPIdentity("test_bot.pem")
    print(f"✅ Generated Identity: {bot.node_id}")
    
    # 2. Register
    resp = requests.post(f"{HUB_URL}/register", json={"pubkey": bot.pub_pem})
    print("Registration:", resp.json())
    
    # 3. Connect WS with query params
    import time
    ts = str(int(time.time()))
    sig = bot.sign(bot.node_id, ts)
    
    # Add url encoding for signature to be safe in URL
    import urllib.parse
    sig_safe = urllib.parse.quote(sig)
    
    ws_url = f"ws://localhost:8000/ws/{bot.node_id}?timestamp={ts}&signature={sig_safe}"
    try:
        async with websockets.connect(ws_url) as ws:
            print("✅ WebSocket Authenticated!")
            
            # 4. Submit Task
            payload_dict = {"consumer_id": bot.node_id, "payload": "Test secure task", "bounty": 1.0}
            payload_str = json.dumps(payload_dict)
            headers = bot.get_auth_headers(payload_str)
            
            r = requests.post(f"{HUB_URL}/tasks/submit", data=payload_str, headers=headers)
            print("Submit Task:", r.json())
            
    except Exception as e:
        print("❌ Error:", e)

if __name__ == "__main__":
    asyncio.run(test())
