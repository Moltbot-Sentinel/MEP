"""
Unit tests for hub/auth.py — Ed25519 signature verification and node ID derivation.
"""
import base64
import time
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "hub"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from auth import derive_node_id, verify_signature


def _generate_keypair():
    """Generate an Ed25519 keypair and return (private_key, pub_pem)."""
    private_key = Ed25519PrivateKey.generate()
    pub_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_key, pub_pem


def _sign(private_key, payload_str: str, timestamp: str) -> str:
    message = f"{payload_str}{timestamp}".encode("utf-8")
    signature = private_key.sign(message)
    return base64.b64encode(signature).decode("utf-8")


class TestDeriveNodeId(unittest.TestCase):

    def test_deterministic(self):
        _, pub_pem = _generate_keypair()
        id1 = derive_node_id(pub_pem)
        id2 = derive_node_id(pub_pem)
        self.assertEqual(id1, id2)

    def test_starts_with_node_prefix(self):
        _, pub_pem = _generate_keypair()
        node_id = derive_node_id(pub_pem)
        self.assertTrue(node_id.startswith("node_"))

    def test_different_keys_produce_different_ids(self):
        _, pem1 = _generate_keypair()
        _, pem2 = _generate_keypair()
        self.assertNotEqual(derive_node_id(pem1), derive_node_id(pem2))


class TestVerifySignature(unittest.TestCase):

    def test_valid_signature(self):
        priv, pub_pem = _generate_keypair()
        payload = '{"hello": "world"}'
        ts = str(int(time.time()))
        sig = _sign(priv, payload, ts)
        self.assertTrue(verify_signature(pub_pem, payload, ts, sig))

    def test_tampered_payload(self):
        priv, pub_pem = _generate_keypair()
        payload = '{"hello": "world"}'
        ts = str(int(time.time()))
        sig = _sign(priv, payload, ts)
        self.assertFalse(verify_signature(pub_pem, "tampered", ts, sig))

    def test_expired_timestamp(self):
        priv, pub_pem = _generate_keypair()
        payload = "test"
        ts = str(int(time.time()) - 600)  # 10 minutes ago, beyond 300s window
        sig = _sign(priv, payload, ts)
        self.assertFalse(verify_signature(pub_pem, payload, ts, sig))

    def test_wrong_key(self):
        priv1, _ = _generate_keypair()
        _, pub_pem2 = _generate_keypair()
        payload = "test"
        ts = str(int(time.time()))
        sig = _sign(priv1, payload, ts)
        self.assertFalse(verify_signature(pub_pem2, payload, ts, sig))

    def test_garbage_signature(self):
        _, pub_pem = _generate_keypair()
        self.assertFalse(verify_signature(pub_pem, "x", str(int(time.time())), "not-valid-base64!!!"))


if __name__ == "__main__":
    unittest.main()
