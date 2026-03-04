
import sys
import os
import json
import requests
from identity import MEPIdentity

HUB_URL = os.getenv("HUB_URL", "https://mep-hub.silentcopilot.ai")

def buy_data(target_node):
    key_path = os.path.expanduser("~/.mep/mep_ai_provider.pem")
    identity = MEPIdentity(key_path)
    
    payload = {
        "consumer_id": identity.node_id,
        "payload": "I want to buy the secret dataset.",
        "bounty": 0.5,
        "model_requirement": "data-purchase",
        "target_node": target_node,
        "payload_uri": None,
        "secret_data": None
    }
    
    payload_str = json.dumps(payload)
    headers = identity.get_auth_headers(payload_str)
    headers["Content-Type"] = "application/json"
    
    print(f"Buying Data from {target_node}...")
    try:
        resp = requests.post(f"{HUB_URL}/tasks/submit", data=payload_str, headers=headers, timeout=10)
        print(resp.text)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 buy_data.py <target_node_id>")
        sys.exit(1)
    
    target = sys.argv[1]
    buy_data(target)
