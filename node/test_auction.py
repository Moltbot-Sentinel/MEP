import asyncio
import websockets
import json
import requests
import uuid
import time
import urllib.parse
from identity import MEPIdentity

HUB_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"

def auth_ws_url(identity: MEPIdentity) -> str:
    ts = str(int(time.time()))
    sig = identity.sign(identity.node_id, ts)
    sig_safe = urllib.parse.quote(sig)
    return f"{WS_URL}/ws/{identity.node_id}?timestamp={ts}&signature={sig_safe}"

def update_registry(identity: MEPIdentity, skills: list[str], models: list[str]):
    payload = json.dumps({
        "skills": skills,
        "models": models,
        "availability": "online"
    })
    headers = identity.get_auth_headers(payload)
    headers["Content-Type"] = "application/json"
    resp = requests.post(f"{HUB_URL}/registry/update", data=payload, headers=headers)
    resp.raise_for_status()

async def test_secret_data_delivery():
    provider = MEPIdentity(f"test_provider_{uuid.uuid4().hex[:6]}.pem")
    consumer = MEPIdentity(f"test_consumer_{uuid.uuid4().hex[:6]}.pem")
    secret_data = "TOP_SECRET_DATA_MARKET_SAMPLE"
    requests.post(f"{HUB_URL}/register", json={"pubkey": provider.pub_pem}).raise_for_status()
    requests.post(f"{HUB_URL}/register", json={"pubkey": consumer.pub_pem}).raise_for_status()
    async with websockets.connect(auth_ws_url(provider)) as ws:
        submit_payload = json.dumps({
            "consumer_id": consumer.node_id,
            "payload": "Test payload",
            "bounty": -1.0,
            "secret_data": secret_data
        })
        submit_headers = consumer.get_auth_headers(submit_payload)
        submit_headers["Content-Type"] = "application/json"
        requests.post(f"{HUB_URL}/tasks/submit", data=submit_payload, headers=submit_headers).raise_for_status()
        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
        data = json.loads(msg)
        print("Received:", data)
        assert data["event"] == "rfc", f"Expected rfc event, got {data.get('event')}"
        assert "secret_data" not in data["data"], "RFC leaked secret_data"
        task_id = data["data"]["id"]
        bid_payload = json.dumps({"task_id": task_id, "provider_id": provider.node_id})
        bid_headers = provider.get_auth_headers(bid_payload)
        bid_headers["Content-Type"] = "application/json"
        bid_resp = requests.post(f"{HUB_URL}/tasks/bid", data=bid_payload, headers=bid_headers)
        bid_resp.raise_for_status()
        bid_data = bid_resp.json()
        print("Bid response:", bid_data)
        assert bid_data.get("secret_data") == secret_data, "Assigned provider did not receive secret_data"
        complete_payload = json.dumps({
            "task_id": task_id,
            "provider_id": provider.node_id,
            "result_payload": "Done!"
        })
        complete_headers = provider.get_auth_headers(complete_payload)
        complete_headers["Content-Type"] = "application/json"
        complete_resp = requests.post(f"{HUB_URL}/tasks/complete", data=complete_payload, headers=complete_headers)
        complete_resp.raise_for_status()
        print("Complete response:", complete_resp.json())

async def test_capability_routing():
    provider_python = MEPIdentity(f"test_py_{uuid.uuid4().hex[:6]}.pem")
    provider_bash = MEPIdentity(f"test_bash_{uuid.uuid4().hex[:6]}.pem")
    consumer = MEPIdentity(f"test_consumer_{uuid.uuid4().hex[:6]}.pem")
    requests.post(f"{HUB_URL}/register", json={"pubkey": provider_python.pub_pem}).raise_for_status()
    requests.post(f"{HUB_URL}/register", json={"pubkey": provider_bash.pub_pem}).raise_for_status()
    requests.post(f"{HUB_URL}/register", json={"pubkey": consumer.pub_pem}).raise_for_status()

    update_registry(provider_python, skills=["python"], models=["python"])
    update_registry(provider_bash, skills=["bash"], models=["bash"])

    async with websockets.connect(auth_ws_url(provider_python)) as ws_python, websockets.connect(auth_ws_url(provider_bash)) as ws_bash:
        submit_payload = json.dumps({
            "consumer_id": consumer.node_id,
            "payload": "Write a tiny python function",
            "bounty": 1.0,
            "model_requirement": "python"
        })
        submit_headers = consumer.get_auth_headers(submit_payload)
        submit_headers["Content-Type"] = "application/json"
        requests.post(f"{HUB_URL}/tasks/submit", data=submit_payload, headers=submit_headers).raise_for_status()

        msg_python = await asyncio.wait_for(ws_python.recv(), timeout=2.0)
        data_python = json.loads(msg_python)
        print("Python provider received:", data_python)
        assert data_python["event"] == "rfc"
        task_id = data_python["data"]["id"]

        try:
            msg_bash = await asyncio.wait_for(ws_bash.recv(), timeout=1.0)
            raise AssertionError(f"Bash provider should not receive RFC but got: {msg_bash}")
        except asyncio.TimeoutError:
            pass

        bid_payload = json.dumps({"task_id": task_id, "provider_id": provider_bash.node_id})
        bid_headers = provider_bash.get_auth_headers(bid_payload)
        bid_headers["Content-Type"] = "application/json"
        bid_resp = requests.post(f"{HUB_URL}/tasks/bid", data=bid_payload, headers=bid_headers)
        bid_resp.raise_for_status()
        bid_data = bid_resp.json()
        assert bid_data.get("status") == "rejected", f"Expected rejected bid, got: {bid_data}"

        bid_payload = json.dumps({"task_id": task_id, "provider_id": provider_python.node_id})
        bid_headers = provider_python.get_auth_headers(bid_payload)
        bid_headers["Content-Type"] = "application/json"
        bid_resp = requests.post(f"{HUB_URL}/tasks/bid", data=bid_payload, headers=bid_headers)
        bid_resp.raise_for_status()
        bid_data = bid_resp.json()
        assert bid_data.get("status") == "accepted", f"Expected accepted bid, got: {bid_data}"

        complete_payload = json.dumps({
            "task_id": task_id,
            "provider_id": provider_python.node_id,
            "result_payload": "Done!"
        })
        complete_headers = provider_python.get_auth_headers(complete_payload)
        complete_headers["Content-Type"] = "application/json"
        complete_resp = requests.post(f"{HUB_URL}/tasks/complete", data=complete_payload, headers=complete_headers)
        complete_resp.raise_for_status()
        print("Complete response:", complete_resp.json())

if __name__ == '__main__':
    asyncio.run(test_secret_data_delivery())
    asyncio.run(test_capability_routing())
