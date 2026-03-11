import os
import time
import json
import base64
import hashlib
import sys
from urllib.parse import urlparse
import requests
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


HUB_URL = os.getenv("HUB_URL", "https://mep-hub.silentcopilot.ai").rstrip("/")
FORCE_TARGET_NODE = os.getenv("FORCE_TARGET_NODE", "").strip()
IMAGE_ONLY = os.getenv("IMAGE_ONLY", "").strip().lower() in ("1", "true", "yes")
EXPECT_RESULT_URI = os.getenv("EXPECT_RESULT_URI", "").strip().lower() in ("1", "true", "yes")
IMAGE_PROMPT = os.getenv(
    "IMAGE_PROMPT",
    "Generate a tiny PNG image and include either a downloadable URI or base64 data in your final output."
).strip()


class Identity:
    def __init__(self):
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        pub_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("utf-8")
        node_hash = hashlib.sha256(pub_pem.encode("utf-8")).hexdigest()[:12]
        self.private_key = private_key
        self.pub_pem = pub_pem
        self.node_id = f"node_{node_hash}"

    def auth_headers(self, payload: str) -> dict:
        timestamp = str(int(time.time()))
        signature = self.private_key.sign(f"{payload}{timestamp}".encode("utf-8"))
        signature_b64 = base64.b64encode(signature).decode("utf-8")
        return {
            "Content-Type": "application/json",
            "X-MEP-NodeID": self.node_id,
            "X-MEP-Timestamp": timestamp,
            "X-MEP-Signature": signature_b64
        }


def submit_task(session: requests.Session, identity: Identity, body: dict) -> requests.Response:
    payload = json.dumps(body)
    headers = identity.auth_headers(payload)
    response = session.post(f"{HUB_URL}/tasks/submit", data=payload, headers=headers, timeout=30)
    print(f"SUBMIT bounty={body.get('bounty')} status={response.status_code} body={response.text}")
    return response


def read_result(session: requests.Session, identity: Identity, task_id: str) -> requests.Response:
    headers = identity.auth_headers("")
    return session.get(f"{HUB_URL}/tasks/result/{task_id}", headers=headers, timeout=20)

def is_valid_external_uri(value: str) -> bool:
    parsed = urlparse(value.strip())
    if parsed.scheme == "ipfs":
        return True
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def run():
    identity = Identity()
    session = requests.Session()

    register_payload = {"pubkey": identity.pub_pem, "alias": "trae-live-test"}
    register_response = session.post(f"{HUB_URL}/register", json=register_payload, timeout=20)
    print(f"REGISTER status={register_response.status_code} body={register_response.text}")
    register_response.raise_for_status()

    discovery_response = session.get(
        f"{HUB_URL}/registry/search",
        params={"availability": "online", "limit": 30},
        timeout=20
    )
    online_nodes = discovery_response.json().get("results", []) if discovery_response.ok else []
    target_nodes = [
        item.get("node_id")
        for item in online_nodes
        if item.get("node_id") and item.get("node_id") != identity.node_id
    ]
    dm_target = FORCE_TARGET_NODE or (target_nodes[0] if target_nodes else None)
    print(f"DM_TARGET {dm_target}")

    pending: list[tuple[str, str]] = []

    if not IMAGE_ONLY and dm_target:
        dm_response = submit_task(session, identity, {
            "consumer_id": identity.node_id,
            "payload": "Live DM ping from Trae test node. Please reply ACK.",
            "bounty": 0.0,
            "target_node": dm_target
        })
        if dm_response.ok:
            pending.append(("zero", dm_response.json().get("task_id")))

    if not IMAGE_ONLY:
        positive_response = submit_task(session, identity, {
            "consumer_id": identity.node_id,
            "payload": "Compute market live test: return one-line ACK with your node id.",
            "bounty": 0.5
        })
        if positive_response.ok:
            pending.append(("positive", positive_response.json().get("task_id")))

        negative_body = {
            "consumer_id": identity.node_id,
            "payload": "Data market offer metadata: synthetic sample bundle.",
            "secret_data": "SAMPLE_DATA_PAYLOAD_FOR_NEGATIVE_BOUNTY_TEST",
            "bounty": -0.5
        }
        if dm_target:
            negative_body["target_node"] = dm_target
        negative_response = submit_task(session, identity, negative_body)
        if negative_response.ok:
            pending.append(("negative", negative_response.json().get("task_id")))

    image_body = {
        "consumer_id": identity.node_id,
        "payload": IMAGE_PROMPT,
        "bounty": 0.5
    }
    if dm_target:
        image_body["target_node"] = dm_target
    image_response = submit_task(session, identity, image_body)
    if image_response.ok:
        pending.append(("image", image_response.json().get("task_id")))

    completed: dict[str, dict] = {}
    started = time.time()
    while time.time() - started < 120 and pending:
        for mode, task_id in list(pending):
            if not task_id:
                pending.remove((mode, task_id))
                continue
            result_response = read_result(session, identity, task_id)
            if result_response.status_code == 200:
                payload = result_response.json()
                completed[mode] = payload
                print(f"RESULT mode={mode} task_id={task_id} body={json.dumps(payload)}")
                result_uri = payload.get("result_uri")
                if result_uri:
                    print(
                        f"RESULT_URI task_id={task_id} value={result_uri} "
                        f"valid={is_valid_external_uri(result_uri)}"
                    )
                pending.remove((mode, task_id))
        time.sleep(3)

    if FORCE_TARGET_NODE:
        image_result = completed.get("image")
        if image_result:
            provider_id = image_result.get("provider_id")
            if provider_id != FORCE_TARGET_NODE:
                print(f"TARGET_MISMATCH expected={FORCE_TARGET_NODE} actual={provider_id}")
                sys.exit(2)
    if EXPECT_RESULT_URI:
        image_result = completed.get("image")
        if not image_result:
            print("EXPECT_RESULT_URI_FAILED missing image result")
            sys.exit(3)
        result_uri = image_result.get("result_uri")
        if not result_uri or not is_valid_external_uri(result_uri):
            print(f"EXPECT_RESULT_URI_FAILED value={result_uri}")
            sys.exit(4)

    print(f"SUMMARY_COMPLETED {sorted(completed.keys())}")
    print(f"SUMMARY_PENDING {pending}")
    print(f"NODE_ID {identity.node_id}")


if __name__ == "__main__":
    run()
