"""Signed Evidence Attestation (SEA) — the kernel verifies evidence, not a flag.

Two of the Governance Kernel's nine constraint evaluators rule on facts the
world model carries: ``_check_gdpr_consent`` reads a target's jurisdiction and
its ``gdpr_consent``, and ``_check_contact_hours`` reads its ``local_hour``.
Whatever can write those facts decides those verdicts.

The previous mechanism recorded provenance in a plain dict on the entity
(``_evidence_provenance``) and the kernel read a boolean off it. That is not a
control against anyone who can supply the entity: in the isolated posture the
agent legitimately authors the whole ``world_state`` field of an ``evaluate``
request, so a hand-written ``{"attested": true, "governance_properties": [...]}``
read as attested. Reproduced against a governed kernel, the identical proposal
went from ``rejected ['gdpr_consent_required']`` to ``approved []``, with no code
execution anywhere.

This module replaces that with an Ed25519 signature over the specific facts, by
an issuer key resolved from the deployment's trust root, verified by the kernel
itself. What the entity carries is opaque: it is either a valid signature by a
registered issuer over these exact values for this exact entity, still inside its
window, or it is nothing.

**What a valid signature does and does not prove.** It proves a registered issuer
asserted these values for this entity at this time. It does not prove the values
are TRUE — a compromised or mistaken source of record signs falsehoods and GAP
certifies them faithfully. This is the same shape as the existing limitation that
GAP verifies a signature, not a person.

**The deployment decides whether any of this is worth anything.** If the issuer
private key sits on the same host under the same OS user as the agent, the agent
reads it and mints whatever it likes. See :class:`InProcessEvidenceSigner` for
the three topologies, stated in descending order of honesty.

**Scope.** Per-entity signatures bind per-entity facts. The agent still assembles
the world model as a whole and can omit an entity or replay an old one; freshness
is BOUNDED by the kernel's max-age ceiling, not eliminated. And this hardens the
two world-model-backed evaluators only — the other seven rule on agent-authored
``action.parameters`` and ``estimated_cost``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from gap_kernel._time import ensure_utc, utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, sign, verify
from gap_kernel.models.world import (
    EVIDENCE_ATTESTATION_PROPERTY,
    GOVERNANCE_RELEVANT_PROPERTIES,
    EntityState,
)

logger = logging.getLogger("gap_kernel.world_model")

# Domain separation, exactly as ``canonical_decision_payload`` does it: a
# signature over this format can never be replayed as a Decision Record or an
# Applicability Profile.
ATTESTATION_DOMAIN = "gap.evidence_attestation.v1"

# The fields inside the signature. This tuple is a HARD BOUNDARY, not a
# convenience — ``tests/test_evidence_attestation.py`` pins it.
#
# IN, and why each one must be:
#   attestation_id  — names the specific assertion in an audit trail.
#   entity_id       — binds the attestation to one entity. This is only a
#                     binding because ``WorldModel`` requires an entity's map
#                     key to equal its own ``entity_id``: the kernel resolves a
#                     target by KEY and this check reads the FIELD, so without
#                     that invariant a genuine blob could be filed under any
#                     key and lifted onto arbitrary targets.
#   properties      — the governance key -> VALUE map. Binding the value is what
#                     stops substitution: flipping ``gdpr_consent`` to True under
#                     a genuine signature breaks the value check.
#   issued_at /
#   expires_at      — the window, which the kernel narrows further (see
#                     ``EvidenceVerifier``).
#   issuer_key_id   — inside the payload, following the decision-v2 precedent
#                     rather than ``ApplicabilityProfile.signing_payload()``,
#                     which excludes its key id and is the weaker one. Inside,
#                     the issuer cannot be swapped without breaking the
#                     signature.
#
# OUT, and why each one must stay out:
#   the full properties dict — ``name``, ``value``, ``status``, ``last_contacted``
#     are executor-written BY DESIGN. Binding them would invalidate the
#     attestation on every legitimate update, which is the over-scoping failure
#     ``test_ordinary_properties_stay_ingestible_and_unstamped`` guards.
#   last_updated, source, confidence, obligations — bookkeeping GAP writes.
#   anything about the proposal or the decision — an attestation is about a fact,
#     not about what anyone proposes to do with it.
SIGNED_ATTESTATION_FIELDS = (
    "attestation_id",
    "entity_id",
    "properties",
    "issued_at",
    "expires_at",
    "issuer_key_id",
)

# How long the KERNEL will act on an attestation, regardless of the expiry the
# issuer wrote into it.
#
# This is a COMPLIANCE parameter, not an engineering default. It is deliberately
# not the 900s decision TTL, because it answers a different question: this value
# decides how long a consent that has already been WITHDRAWN at the source of
# record may still be acted on by this deployment. A subject withdraws consent;
# the last attestation asserting ``gdpr_consent: true`` stays cryptographically
# valid until it ages out, and an agent holding a copy can re-present it for
# exactly this long. Five minutes is short enough to be defensible and is still a
# window, not a closure — raise it only against a written retention decision, and
# never to a number you would be uncomfortable reading aloud to a regulator.
DEFAULT_MAX_ATTESTATION_AGE_SECONDS = 300

# The default lifetime an in-process signer writes into ``expires_at``. The
# kernel's ceiling applies on top and is what actually bounds the window.
DEFAULT_ATTESTATION_TTL_SECONDS = 300

# Tolerance for a not-yet-valid attestation, so ordinary clock skew between an
# issuer host and the kernel host is not read as an attack. Without it, dating an
# attestation into the future would extend ``issued_at + max_age`` arbitrarily
# and defeat the ceiling.
CLOCK_SKEW_TOLERANCE_SECONDS = 60


class AttestationVerificationError(Exception):
    """Raised when an evidence attestation fails verification. Fail closed."""


class EvidenceAttestation(BaseModel):
    """A registered issuer's signed assertion about one entity's governance facts.

    ``properties`` carries ONLY governance-relevant keys mapped to the exact
    values being vouched for. Everything else on the entity is out of scope by
    design (see :data:`SIGNED_ATTESTATION_FIELDS`).
    """

    attestation_id: str
    entity_id: str
    properties: Dict[str, Any] = {}
    issued_at: datetime
    expires_at: datetime
    issuer_key_id: str
    signature: Optional[str] = None     # hex Ed25519 over the canonical payload


def attestation_payload(attestation: EvidenceAttestation) -> str:
    """The canonical, signature-excluded serialization the issuer signs.

    Built from an explicit ALLOW-LIST of fields rather than by excluding the
    signature, so the signed scope cannot widen by someone adding a field to the
    model. Mirrors ``canonical_decision_payload``: JSON, ``sort_keys=True``,
    domain-tagged.
    """
    data = attestation.model_dump(mode="json", include=set(SIGNED_ATTESTATION_FIELDS))
    data["_domain"] = ATTESTATION_DOMAIN
    return json.dumps(data, sort_keys=True, default=str)


def sign_attestation(
    attestation: EvidenceAttestation, private_key_hex: str, issuer_key_id: str
) -> EvidenceAttestation:
    """Return a signed copy of ``attestation`` — the ISSUER's step, not GAP's.

    In the topology this design is written for, this function runs in the source
    of record and never in a GAP process at all.
    """
    unsigned = attestation.model_copy(
        update={"issuer_key_id": issuer_key_id, "signature": None}
    )
    signature = sign(private_key_hex, attestation_payload(unsigned))
    return unsigned.model_copy(update={"signature": signature})


def verify_attestation(
    attestation: EvidenceAttestation, issuers: PublicKeyRegistry
) -> None:
    """Verify the signature only; raise :class:`AttestationVerificationError` if invalid.

    Signature validity is necessary and NOT sufficient — freshness, the entity
    binding and the value match are enforced by :class:`EvidenceVerifier`, which
    is the only thing a kernel should call.
    """
    if not attestation.signature or not attestation.issuer_key_id:
        raise AttestationVerificationError(
            f"Evidence attestation '{attestation.attestation_id}' is unsigned; an "
            f"unsigned attestation is a claim the presenter wrote."
        )
    public_key_hex = issuers.get(attestation.issuer_key_id)
    if not public_key_hex:
        raise AttestationVerificationError(
            f"Evidence attestation '{attestation.attestation_id}' names issuer "
            f"'{attestation.issuer_key_id}', which is not a registered evidence "
            f"issuer for this deployment."
        )
    if not verify(public_key_hex, attestation_payload(attestation), attestation.signature):
        raise AttestationVerificationError(
            f"Evidence attestation '{attestation.attestation_id}' signature is "
            f"invalid (tampered payload or wrong key)."
        )


def canonical_value(value: Any) -> str:
    """The comparison form for an attested property value. NEVER use ``==``.

    In this interpreter ``True == 1`` and ``1 == 1.0`` are both True, so an
    equality check would let a signature issued over ``gdpr_consent: 1`` certify
    a live value of ``True`` — exactly the boolean the GDPR gate turns on, and a
    value-substitution bypass that survives a genuine signature. ``json.dumps``
    yields ``true`` / ``1`` / ``1.0``, which are three different strings.
    """
    return json.dumps(value, sort_keys=True, default=str)


class EvidenceVerifier:
    """Decides, kernel-side, whether a governance-relevant fact is attested.

    Constructed by the kernel from an issuer registry the kernel resolved itself
    — from the trust root in the deployed posture. Nothing about this decision is
    read off the entity, which is the entire difference from what it replaces.
    """

    def __init__(
        self,
        issuers: PublicKeyRegistry,
        max_age: Optional[timedelta] = None,
    ):
        self._issuers = issuers
        self._max_age = (
            timedelta(seconds=DEFAULT_MAX_ATTESTATION_AGE_SECONDS)
            if max_age is None
            else max_age
        )
        if self._max_age <= timedelta(0):
            raise ValueError(
                f"max_age must be positive; got {self._max_age}. A non-positive "
                f"ceiling would make every attestation unusable."
            )

    @property
    def max_age(self) -> timedelta:
        """The kernel's own ceiling on how long an attestation may be acted on."""
        return self._max_age

    def attests(self, entity: EntityState, key: str) -> bool:
        """True only if ALL FOUR hold; anything else is unattested.

        1. The attestation carries a valid signature by a REGISTERED issuer.
        2. ``utcnow() < min(expires_at, issued_at + max_age)`` and it is not
           dated into the future beyond clock skew — read from the KERNEL's
           clock, never from anything a caller supplied.
        3. The signed value for ``key`` matches the live property under canonical
           JSON, and the attestation names THIS entity.
        4. ``key`` is in ``GOVERNANCE_RELEVANT_PROPERTIES``.

        Unattested stays unevaluable stays a violation — the existing fail-closed
        rule, unchanged.
        """
        if key not in GOVERNANCE_RELEVANT_PROPERTIES:
            return False

        attestation = self._parse(entity)
        if attestation is None:
            return False

        try:
            verify_attestation(attestation, self._issuers)
        except AttestationVerificationError as exc:
            logger.warning("evidence attestation on %s rejected: %s", entity.entity_id, exc)
            return False

        if attestation.entity_id != entity.entity_id:
            logger.warning(
                "evidence attestation issued for %s was presented on entity %s; "
                "an attestation binds one entity",
                attestation.entity_id, entity.entity_id,
            )
            return False

        if not self._is_fresh(attestation, entity.entity_id):
            return False

        if key not in attestation.properties:
            return False

        signed = canonical_value(attestation.properties[key])
        live = canonical_value(entity.properties.get(key))
        if signed != live:
            logger.warning(
                "evidence attestation on %s vouches for %s=%s but the entity "
                "carries %s; the value was changed after it was signed",
                entity.entity_id, key, signed, live,
            )
            return False

        return True

    def attested_keys(self, entity: EntityState) -> set:
        """Every governance-relevant key on ``entity`` this verifier will accept."""
        return {k for k in GOVERNANCE_RELEVANT_PROPERTIES if self.attests(entity, k)}

    # --- internals ---------------------------------------------------------

    def _parse(self, entity: EntityState) -> Optional[EvidenceAttestation]:
        blob = entity.properties.get(EVIDENCE_ATTESTATION_PROPERTY)
        if not isinstance(blob, dict):
            return None
        try:
            return EvidenceAttestation.model_validate(blob)
        except ValidationError as exc:
            logger.warning(
                "evidence attestation on %s is malformed (%d validation errors: %s); "
                "it could not have verified",
                entity.entity_id,
                exc.error_count(),
                ", ".join(sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})),
            )
            return None

    def _is_fresh(self, attestation: EvidenceAttestation, entity_id: str) -> bool:
        now = utcnow()
        issued_at = ensure_utc(attestation.issued_at)
        deadline = min(ensure_utc(attestation.expires_at), issued_at + self._max_age)
        skew = timedelta(seconds=CLOCK_SKEW_TOLERANCE_SECONDS)
        if now < issued_at - skew:
            logger.warning(
                "evidence attestation on %s is dated into the future (%s); refusing "
                "to let a forward-dated issue time extend the kernel's ceiling",
                entity_id, issued_at.isoformat(),
            )
            return False
        if now >= deadline:
            logger.warning(
                "evidence attestation on %s expired at %s (kernel ceiling %s)",
                entity_id, deadline.isoformat(), self._max_age,
            )
            return False
        return True


class InProcessEvidenceSigner(BaseModel):
    """Mints signed attestations INSIDE the GAP process. Prototype path only.

    Three topologies, in descending order of honesty:

    1. **The consent-of-record system signs at source.** The CRM, the consent
       platform, the regulatory feed holds the issuer private key, signs the
       facts it already owns, and hands GAP a blob. The key never enters a GAP
       process at all. This is the only topology in which the signature means
       what the surrounding documentation says it means.
    2. **An ingestion sidecar signs.** A separate process, running as a
       DIFFERENT OS user, holds the key, reads the source system, and hands GAP
       an opaque blob. GAP can carry and verify it but cannot mint one. Weaker
       than (1) — the sidecar becomes the thing that must not be compromised —
       but the agent still cannot sign.
    3. **In-process minting — this class.** Prototype and test only. The private
       key is an attribute in the same address space as the agent.

    The invariant that carries the whole design: **if the agent process can sign,
    the mechanism is theatre.** Under (3) an adversary with code execution in the
    agent process reads this key and mints any consent it likes, so the
    attestation certifies nothing that adversary did not already control. This is
    the same shape of caveat as ``provision_trust_root``'s 0600 note: on a host
    where the agent runs as the same user it is hygiene, not a boundary. Develop
    against this class; do not cite it as a guarantee.
    """

    issuer_key_id: str
    private_key_hex: str = Field(repr=False)
    ttl_seconds: int = DEFAULT_ATTESTATION_TTL_SECONDS

    def mint(self, entity_id: str, properties: Dict[str, Any]) -> EvidenceAttestation:
        """Sign the governance-relevant subset of ``properties`` for ``entity_id``.

        Only governance-relevant keys are bound — see
        :data:`SIGNED_ATTESTATION_FIELDS` for why binding the whole property bag
        would invalidate the attestation on every ordinary update.
        """
        bound = {
            key: value
            for key, value in properties.items()
            if key in GOVERNANCE_RELEVANT_PROPERTIES
        }
        issued_at = utcnow()
        attestation = EvidenceAttestation(
            attestation_id=f"att_{uuid4().hex[:12]}",
            entity_id=entity_id,
            properties=bound,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=self.ttl_seconds),
            issuer_key_id=self.issuer_key_id,
        )
        return sign_attestation(attestation, self.private_key_hex, self.issuer_key_id)
