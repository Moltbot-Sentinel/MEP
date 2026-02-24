import os
import time
import base64
import hashlib
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

class MEPIdentity:
    def __init__(self, key_path="private.pem"):
        self.key_path = key_path
        self._load_or_generate()
        
    def _load_or_generate(self):
        if os.path.exists(self.key_path):
            with open(self.key_path, "rb") as f:
                self.private_key = serialization.load_pem_private_key(f.read(), password=None)
        else:
            self.private_key = ed25519.Ed25519PrivateKey.generate()
            with open(self.key_path, "wb") as f:
                f.write(self.private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))
                
        self.public_key = self.private_key.public_key()
        self.pub_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode('utf-8')
        
        sha = hashlib.sha256(self.pub_pem.encode('utf-8')).hexdigest()
        self.node_id = f"node_{sha[:12]}"
        
    def sign(self, payload: str, timestamp: str) -> str:
        message = f"{payload}{timestamp}".encode('utf-8')
        signature = self.private_key.sign(message)
        return base64.b64encode(signature).decode('utf-8')
        
    def get_auth_headers(self, payload: str) -> dict:
        ts = str(int(time.time()))
        sig = self.sign(payload, ts)
        return {
            "X-MEP-NodeID": self.node_id,
            "X-MEP-Timestamp": ts,
            "X-MEP-Signature": sig
        }
