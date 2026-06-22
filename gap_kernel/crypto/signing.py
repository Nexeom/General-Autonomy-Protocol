"""Ed25519 signing primitives for GAP (Fix 4 cryptographic substrate).

A thin wrapper over ``cryptography``'s Ed25519. Keys are exchanged as hex
strings so they serialize cleanly into Decision Records and configuration.

This module is the shared substrate the higher-leverage fixes build on:
  * Fix 4 — Out-of-Band Authority Verification binds a human approval to the
    specific Decision Record ID via a signature verified here.
  * Fix 5 — signed decision lineage.
  * Fix 2 — the governance kernel signs every decision so the execution layer
    can verify it was not forged.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def generate_keypair() -> Tuple[str, str]:
    """Generate an Ed25519 key pair. Returns ``(private_key_hex, public_key_hex)``."""
    private_key = Ed25519PrivateKey.generate()
    private_hex = private_key.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    ).hex()
    public_hex = private_key.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    ).hex()
    return private_hex, public_hex


def sign(private_key_hex: str, message: str) -> str:
    """Sign a UTF-8 ``message`` with an Ed25519 private key; return a hex signature."""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return private_key.sign(message.encode("utf-8")).hex()


def verify(public_key_hex: str, message: str, signature_hex: str) -> bool:
    """Verify an Ed25519 signature over a UTF-8 ``message``.

    Returns ``True`` only on a valid signature; never raises (malformed keys or
    signatures return ``False``), so callers fail closed.
    """
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), message.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError):
        return False


class PublicKeyRegistry:
    """Resolves a human-approver key id to an Ed25519 public key (hex).

    This is the trust boundary for Out-of-Band verification: the runtime must
    know which public keys belong to authorized human approvers, through
    configuration the governed agent cannot influence. In-memory here; a
    production deployment backs it with a vault / HSM.
    """

    def __init__(self, keys: Optional[Dict[str, str]] = None):
        self._keys: Dict[str, str] = dict(keys or {})

    def register(self, key_id: str, public_key_hex: str) -> None:
        self._keys[key_id] = public_key_hex

    def get(self, key_id: str) -> Optional[str]:
        return self._keys.get(key_id)
