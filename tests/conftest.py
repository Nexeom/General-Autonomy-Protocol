"""Shared test scaffolding for Signed Evidence Attestation.

Three test modules used to carry their own ``EvidenceChannel`` constant
(``CRM_ATTESTED``, ``ATTESTED_CRM``, ``CRM_OF_RECORD``) and about eleven use
sites between them. Under SEA an attested channel needs a keypair, the kernel
needs the public half registered, and the entity needs a signature — so the
constant is now built once here and each module binds its own name to it.

The authority object is a MODULE-LEVEL singleton as well as a fixture, because
those three constants are evaluated at import time and a fixture cannot be.
``evidence_authority`` is the fixture; ``EVIDENCE_AUTHORITY`` is the same object
for module-level use.

What this is NOT: it mints attestations in the test process, which is topology
(3) in :class:`~gap_kernel.world_model.attestation.InProcessEvidenceSigner` and
is a development convenience, not a boundary. A test suite is exactly where that
is fine.
"""

from typing import Any, Dict, Optional

import pytest

from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.world_model.attestation import (
    DEFAULT_ATTESTATION_TTL_SECONDS,
    EvidenceVerifier,
    InProcessEvidenceSigner,
)
from gap_kernel.world_model.store import EvidenceChannel

EVIDENCE_ISSUER_KEY_ID = "consent_of_record"


class EvidenceAuthority:
    """One issuer keypair, plus everything the three suites need to use it."""

    def __init__(self, key_id: str = EVIDENCE_ISSUER_KEY_ID):
        self.key_id = key_id
        self.private_key_hex, self.public_key_hex = generate_keypair()

    def registry(self) -> PublicKeyRegistry:
        """The public half, in the shape a kernel takes as ``evidence_issuers``."""
        return PublicKeyRegistry({self.key_id: self.public_key_hex})

    def verifier(self, **kwargs) -> EvidenceVerifier:
        """A verifier that accepts this authority — what the kernel builds itself."""
        return EvidenceVerifier(self.registry(), **kwargs)

    def signer(self, ttl_seconds: int = DEFAULT_ATTESTATION_TTL_SECONDS) -> InProcessEvidenceSigner:
        return InProcessEvidenceSigner(
            issuer_key_id=self.key_id,
            private_key_hex=self.private_key_hex,
            ttl_seconds=ttl_seconds,
        )

    def channel(
        self,
        channel_id: str = "crm_of_record",
        description: str = "Consent-of-record system",
        ttl_seconds: int = DEFAULT_ATTESTATION_TTL_SECONDS,
    ) -> EvidenceChannel:
        """An attested channel that actually signs what it writes."""
        return EvidenceChannel(
            channel_id=channel_id,
            attested=True,
            description=description,
            signer=self.signer(ttl_seconds=ttl_seconds),
        )

    def attest(
        self,
        entity_id: str,
        properties: Dict[str, Any],
        issuer_key_id: Optional[str] = None,
        private_key_hex: Optional[str] = None,
    ) -> dict:
        """A signed attestation blob, in the form an entity carries it.

        ``issuer_key_id`` / ``private_key_hex`` let a test sign as somebody else
        — an unregistered issuer, or a registered one with the wrong key.
        """
        signer = InProcessEvidenceSigner(
            issuer_key_id=issuer_key_id or self.key_id,
            private_key_hex=private_key_hex or self.private_key_hex,
        )
        return signer.mint(entity_id, properties).model_dump(mode="json")


# The singleton. Module-level so the three suites can build their channel
# constant at import time; the fixture below hands out the same object.
EVIDENCE_AUTHORITY = EvidenceAuthority()


@pytest.fixture(scope="session")
def evidence_authority() -> EvidenceAuthority:
    """The shared evidence issuer: keypair, registry, signing channel, verifier."""
    return EVIDENCE_AUTHORITY
