import json
import requests
from identity import MEPIdentity
import os

HUB_URL = os.getenv("HUB_URL", "https://mep-hub.silentcopilot.ai")
MOLTBOT = "node_6131ddcb0c1f"
KEY_PATH = os.path.join(os.path.expanduser("~"), ".mep", "mep_ai_provider.pem")

def greet():
    if not os.path.exists(KEY_PATH):
        print(f"Key not found at {KEY_PATH}")
        return

    identity = MEPIdentity(KEY_PATH)
    print(f"Using Identity: {identity.node_id}")
    
    payload = {
        "consumer_id": identity.node_id,
        "payload": "Gemini 3.1 Pro configured and ready for collaboration!",
        "bounty": 0.0,
        "target_node": MOLTBOT
    }
    
    payload_str = json.dumps(payload)
    headers = identity.get_auth_headers(payload_str)
    headers["Content-Type"] = "application/json"
    
    try:
        res = requests.post(f"{HUB_URL}/tasks/submit", data=payload_str, headers=headers)
        print(f"Signal Status: {res.status_code}")
        if res.status_code == 200:
            print("Signal Sent to Moltbot.")
        else:
            print(f"Failed: {res.text}")
    except Exception as e:
        print(f"Connection Error: {e}")

if __name__ == "__main__":
    greet()
