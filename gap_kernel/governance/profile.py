"""Applicability Profile — the signed, runtime-immutable Tier-1 floor (Fix 3).

A deployment's Tier-1 (regulatory floor) constraints are declared in an
``ApplicabilityProfile`` that is cryptographically signed by an authority key.
The Governance Kernel verifies the signature on load and refuses any profile
that is unsigned, tampered, or signed by an unregistered key — so the regulatory
floor cannot be weakened, narrowed, or forged by the running system.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from gap_kernel.crypto.signing import PublicKeyRegistry, sign, verify
from gap_kernel.models.intent import Constraint


class ProfileVerificationError(Exception):
    """Raised when an Applicability Profile fails signature verification."""


class ApplicabilityProfile(BaseModel):
    """A signed declaration of the Tier-1 regulatory floor for a deployment."""

    profile_id: str
    tier1_constraints: List[Constraint] = []
    issued_by: str = "regulatory_authority"
    issued_at: Optional[datetime] = None
    signature: Optional[str] = None        # hex Ed25519 over the canonical payload
    signing_key_id: Optional[str] = None


def signing_payload(profile: ApplicabilityProfile) -> str:
    """Canonical, signature-excluded serialization the authority signs."""
    data = profile.model_dump(mode="json", exclude={"signature", "signing_key_id"})
    return json.dumps(data, sort_keys=True, default=str)


def sign_profile(
    profile: ApplicabilityProfile, private_key_hex: str, signing_key_id: str
) -> ApplicabilityProfile:
    """Return a signed copy of ``profile`` (the issuing authority's step)."""
    unsigned = profile.model_copy(
        update={"signing_key_id": signing_key_id, "signature": None}
    )
    signature = sign(private_key_hex, signing_payload(unsigned))
    return unsigned.model_copy(update={"signature": signature})


def verify_profile(profile: ApplicabilityProfile, registry: PublicKeyRegistry) -> None:
    """Verify a profile's signature; raise :class:`ProfileVerificationError` if invalid.

    Fail closed: an unsigned profile, an unknown signing key, or a payload that
    does not match the signature are all rejected.
    """
    if not profile.signature or not profile.signing_key_id:
        raise ProfileVerificationError(
            f"Applicability Profile '{profile.profile_id}' is unsigned; the "
            f"regulatory floor must be loaded from a signed profile."
        )
    public_key_hex = registry.get(profile.signing_key_id)
    if not public_key_hex:
        raise ProfileVerificationError(
            f"Applicability Profile '{profile.profile_id}' is signed by an "
            f"unregistered key '{profile.signing_key_id}'."
        )
    if not verify(public_key_hex, signing_payload(profile), profile.signature):
        raise ProfileVerificationError(
            f"Applicability Profile '{profile.profile_id}' signature is invalid "
            f"(tampered payload or wrong key)."
        )
