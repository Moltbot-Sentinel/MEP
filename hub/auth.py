import time
import base64
import hashlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature

def derive_node_id(pub_pem: str) -> str:
    """Derive deterministic Node ID from Public Key PEM."""
    sha = hashlib.sha256(pub_pem.encode('utf-8')).hexdigest()
    return f"node_{sha[:12]}"

def verify_signature(pub_pem: str, payload_str: str, timestamp: str, signature_b64: str) -> bool:
    """Verify Ed25519 signature to prevent identity spoofing."""
    try:
        # Replay protection: Reject if timestamp is more than 5 minutes old
        if abs(time.time() - float(timestamp)) > 300:
            return False
            
        public_key = serialization.load_pem_public_key(pub_pem.encode('utf-8'))
        
        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            return False
            
        signature = base64.b64decode(signature_b64)
        message = f"{payload_str}{timestamp}".encode('utf-8')
        
        public_key.verify(signature, message)
        return True
    except (InvalidSignature, ValueError, Exception) as e:
        print(f"[Auth Error] {e}")
        return False
