"""
World Model Store — manages the structured representation of operational reality.

Updated by: Execution outcomes + External sensors
Queried by: Reconciler Loop + Strategy Layer

Part of what it carries is not telemetry but EVIDENCE: the Governance Kernel's
constraint evaluators rule on a lead's jurisdiction, its GDPR consent and its
local hour, so whatever can write those facts decides the verdict.

**This store is not where that guarantee lives, and it never was.** It sees only
the writers that go through it; a caller that assembles the ``WorldModel`` itself
never does. In the isolated posture the agent legitimately authors the whole
``world_state`` field of an ``evaluate`` request, so anything this store refuses
to write can simply be written on the other side of the boundary instead.

So the store no longer tries. Trust is a signature now
(``gap_kernel.world_model.attestation``), verified by the kernel against an
issuer registry the kernel resolved from its trust root. The store's two jobs
here are both modest and both honest:

  * **Carry** whatever ``_evidence_attestation`` blob a writer supplies, verbatim,
    after a shape check. In production the blob is minted OUTSIDE this process by
    the source of record, and the store cannot verify it — carrying an
    unverifiable blob is safe precisely because verification is entirely
    kernel-side. This inverts the old behaviour, which discarded a supplied
    stamp; discarding was necessary only while the stamp itself was the decision.
  * **Record** which declared channel last wrote a governance-relevant fact, and
    log every change to one. That is an audit trail. It decides nothing.

An :class:`EvidenceChannel` may carry an in-process signer for the prototype
path. Read :class:`gap_kernel.world_model.attestation.InProcessEvidenceSigner`
before using it: if the agent process can sign, the mechanism is theatre.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, ValidationError, model_validator

from gap_kernel._time import utcnow
from gap_kernel.models.world import (
    EVIDENCE_ATTESTATION_PROPERTY as _EVIDENCE_ATTESTATION_PROPERTY,
    EVIDENCE_PROPERTY as _EVIDENCE_PROPERTY,
    GOVERNANCE_RELEVANT_PROPERTIES as _GOVERNANCE_RELEVANT_PROPERTIES,
    EntityState,
    WorldModel,
)
from gap_kernel.world_model.attestation import (
    EvidenceAttestation,
    InProcessEvidenceSigner,
)

logger = logging.getLogger("gap_kernel.world_model")

# The governance-relevant property set: every key a ``_check_*`` evaluator in the
# Governance Kernel reads off an entity to decide whether a HARD constraint is
# satisfied.
#   gdpr_consent          -> _check_gdpr_consent
#   geo / jurisdiction    -> _check_gdpr_consent (which jurisdiction applies)
#   local_hour            -> _check_contact_hours
# A key added to a world-model-backed evaluator belongs here too, otherwise the
# kernel would rule on a fact nobody vouched for. This set is HAND-MAINTAINED and
# its failure mode is fail-OPEN, so
# ``tests/test_evidence_attestation.py::test_every_property_an_evaluator_reads_is_governance_relevant``
# re-derives it from the evaluators' own source.
# It is defined on the model (gap_kernel.models.world) rather than here because
# both the store and the attestation verifier need it, and neither owns it.
GOVERNANCE_RELEVANT_PROPERTIES = _GOVERNANCE_RELEVANT_PROPERTIES

# The reserved property carrying the store's channel breadcrumb. Audit only —
# no kernel code reads it and it cannot make a fact usable.
EVIDENCE_PROPERTY = _EVIDENCE_PROPERTY

# The reserved property carrying the SIGNED attestation. The store carries it;
# only the kernel verifies it.
EVIDENCE_ATTESTATION_PROPERTY = _EVIDENCE_ATTESTATION_PROPERTY

# The channel a write arrives on when none is declared. Naming it keeps an
# unattested write auditable rather than silent.
UNDECLARED_CHANNEL = "undeclared"

# The mutation log lives in process memory alongside the world model, so it is
# bounded; the durable history is the lineage chain.
MAX_EVIDENCE_MUTATIONS = 500

_MISSING = object()


class EvidenceChannel(BaseModel):
    """A declared source for governance-relevant world-model facts.

    ``attested`` is the deployment's assertion that this channel authenticates
    what it reports. On its own that assertion buys NOTHING at the kernel: a
    governed kernel accepts a signed attestation or it accepts nothing, and this
    flag is now audit metadata on the mutation log.

    ``signer`` is the prototype bridge. When set, the store mints a signed
    attestation over the governance-relevant keys of every entity written on this
    channel. Read
    :class:`gap_kernel.world_model.attestation.InProcessEvidenceSigner` first —
    it holds a private key in the agent's address space, which is a development
    convenience and not a boundary. The production shape is the opposite: the
    source of record signs, and GAP receives a blob it can only verify.
    """

    channel_id: str
    attested: bool = False
    description: str = ""
    signer: Optional[InProcessEvidenceSigner] = None

    @model_validator(mode="after")
    def _warn_if_attested_but_unsigned(self) -> "EvidenceChannel":
        if self.attested and self.signer is None:
            logger.warning(
                "evidence channel '%s' declares attested=True but carries no "
                "signer; a governed kernel verifies signatures and will treat "
                "every fact written on this channel as unattested",
                self.channel_id,
            )
        return self


UNATTESTED = EvidenceChannel(
    channel_id=UNDECLARED_CHANNEL,
    attested=False,
    description="No evidence channel was declared for this write",
)


def governance_properties(properties: Dict[str, Any]) -> List[str]:
    """The governance-relevant keys present in a property mapping."""
    return sorted(k for k in properties if k in GOVERNANCE_RELEVANT_PROPERTIES)


def is_attestation_shaped(blob: Any) -> bool:
    """True if ``blob`` parses as an :class:`EvidenceAttestation`.

    A SHAPE check, deliberately not a trust check. The store cannot verify a
    signature it has no issuer registry for, and should not pretend to: this
    only keeps a malformed blob from being carried into the world model where it
    would be noise in every snapshot.
    """
    if not isinstance(blob, dict):
        return False
    try:
        EvidenceAttestation.model_validate(blob)
    except ValidationError:
        return False
    return True


class WorldModelStore:
    """
    In-memory world model store for the prototype.
    Production would use a persistent database.
    """

    def __init__(self):
        self._model = WorldModel(
            entities={},
            last_reconciled=utcnow(),
        )
        self._evidence_mutations: List[dict] = []

    @property
    def model(self) -> WorldModel:
        """Get the current world model."""
        return self._model

    def upsert_entity(
        self,
        entity: EntityState,
        channel: Optional[EvidenceChannel] = None,
    ) -> None:
        """Insert or update an entity in the world model.

        Ordinary properties are stored as supplied. A supplied
        ``_evidence_attestation`` blob is CARRIED VERBATIM once it passes a shape
        check — in production it is minted outside this process by the source of
        record, and the store has no issuer registry to verify it with. That is
        safe because verification happens entirely kernel-side: an unverifiable
        blob buys its presenter nothing.

        The store's own ``_evidence_provenance`` breadcrumb is rewritten on every
        write, and every governance-relevant change is logged, so a consent flip
        is at minimum auditable.
        """
        channel = channel or UNATTESTED
        properties = entity.properties
        self._carry_attestation(entity, channel)

        previous = self._model.entities.get(entity.entity_id)
        self._record_mutations(
            entity_id=entity.entity_id,
            before=previous.properties if previous is not None else {},
            after=properties,
            channel=channel,
        )
        self._stamp(entity, channel)
        self._model.entities[entity.entity_id] = entity

    def get_entity(self, entity_id: str) -> Optional[EntityState]:
        """Get a specific entity by ID."""
        return self._model.entities.get(entity_id)

    def remove_entity(self, entity_id: str) -> bool:
        """Remove an entity from the world model."""
        if entity_id in self._model.entities:
            del self._model.entities[entity_id]
            return True
        return False

    def get_entities_by_type(self, entity_type: str) -> List[EntityState]:
        """Get all entities of a specific type."""
        return [
            e for e in self._model.entities.values()
            if e.entity_type == entity_type
        ]

    def get_entities_with_obligation(self, intent_id: str) -> List[EntityState]:
        """Get all entities governed by a specific intent."""
        return [
            e for e in self._model.entities.values()
            if intent_id in e.obligations
        ]

    def record_drift_event(self, drift_event: dict) -> None:
        """Record a detected drift event."""
        self._model.drift_events.append(drift_event)

    def get_recent_drift_events(self, limit: int = 10) -> List[dict]:
        """Get the most recent drift events."""
        return self._model.drift_events[-limit:]

    def governance_property_mutations(self, limit: int = MAX_EVIDENCE_MUTATIONS) -> List[dict]:
        """Recent changes to governance-relevant properties, newest last."""
        return list(self._evidence_mutations[-limit:])

    def mark_reconciled(self) -> None:
        """Mark the world model as reconciled at the current time."""
        self._model.last_reconciled = utcnow()

    def get_state_snapshot(self) -> dict:
        """Get a serializable snapshot of the current world state."""
        return self._model.model_dump(mode="json")

    def update_from_execution(
        self,
        entity_id: str,
        updates: dict,
        channel: Optional[EvidenceChannel] = None,
    ) -> None:
        """Apply execution result updates to an entity.

        This is a MERGE, so it is the path an executor's output takes into the
        world model. The write itself is allowed — what an executor cannot do is
        make it count: changing a governance-relevant value leaves the entity's
        signed attestation vouching for the OLD value, and the kernel's value
        check fails. Restoring standing requires a new signature, which needs the
        issuer key.

        An execution result is never a source of record, so this path does NOT
        mint an attestation even when the channel carries a signer. Minting here
        would let an executor write a governance value and re-sign it in the same
        call, which is the laundering this mechanism exists to stop — the signer
        would be certifying the agent's own output back to the kernel.
        """
        entity = self._model.entities.get(entity_id)
        if not entity:
            return
        channel = channel or UNATTESTED
        # The store owns its own breadcrumb; the attestation blob is carried,
        # because a presenter gains nothing by carrying one it cannot sign.
        merged = {k: v for k, v in updates.items() if k != EVIDENCE_PROPERTY}
        written = governance_properties(merged)

        self._record_mutations(
            entity_id=entity_id,
            before=entity.properties,
            after={**entity.properties, **merged},
            channel=channel,
            keys=written,
        )
        entity.properties.update(merged)
        entity.last_updated = utcnow()
        self._stamp(entity, channel)

    # --- Evidence provenance ------------------------------------------------

    def _carry_attestation(
        self, entity: EntityState, channel: EvidenceChannel
    ) -> None:
        """Keep a well-formed supplied attestation; mint one if the channel signs.

        A channel's signer takes precedence, because a deployment that wired one
        up wants the store's minted attestation and not whatever a caller
        attached. A malformed blob is dropped with a warning — it could never
        verify, and carrying it would only put noise in every world-state
        snapshot.
        """
        properties = entity.properties
        if channel.signer is not None:
            properties[EVIDENCE_ATTESTATION_PROPERTY] = channel.signer.mint(
                entity.entity_id, properties
            ).model_dump(mode="json")
            return
        supplied = properties.get(EVIDENCE_ATTESTATION_PROPERTY)
        if supplied is None:
            return
        if not is_attestation_shaped(supplied):
            properties.pop(EVIDENCE_ATTESTATION_PROPERTY, None)
            logger.warning(
                "discarded a malformed evidence attestation on %s (channel %s); "
                "it could not have verified",
                entity.entity_id, channel.channel_id,
            )

    def _stamp(self, entity: EntityState, channel: EvidenceChannel) -> None:
        """Record WHICH channel last wrote this entity's governance-relevant facts.

        An audit breadcrumb. Nothing in the kernel reads it, and it cannot make a
        fact usable — that is exactly the mistake it used to embody. Removed when
        the entity carries no governance-relevant facts, so ordinary telemetry
        stays unstamped.
        """
        if not governance_properties(entity.properties):
            entity.properties.pop(EVIDENCE_PROPERTY, None)
            return
        entity.properties[EVIDENCE_PROPERTY] = {
            "channel": channel.channel_id,
            "governance_properties": governance_properties(entity.properties),
            "recorded_at": utcnow().isoformat(),
        }

    def _record_mutations(
        self,
        entity_id: str,
        before: Dict[str, Any],
        after: Dict[str, Any],
        channel: EvidenceChannel,
        keys: Optional[Iterable[str]] = None,
    ) -> None:
        """Append an audit entry for every governance-relevant value that changed."""
        candidates = (
            sorted(set(keys))
            if keys is not None
            else sorted(set(governance_properties(before)) | set(governance_properties(after)))
        )
        for key in candidates:
            old = before.get(key, _MISSING)
            new = after.get(key, _MISSING)
            if old is not _MISSING and old == new:
                continue
            self._evidence_mutations.append({
                "entity_id": entity_id,
                "property": key,
                "previous_value": None if old is _MISSING else old,
                "new_value": None if new is _MISSING else new,
                "channel": channel.channel_id,
                "attested": bool(channel.attested),
                "recorded_at": utcnow().isoformat(),
            })
            logger.info(
                "governance-relevant property %s on %s changed via channel %s "
                "(attested=%s)",
                key, entity_id, channel.channel_id, bool(channel.attested),
            )
        excess = len(self._evidence_mutations) - MAX_EVIDENCE_MUTATIONS
        if excess > 0:
            del self._evidence_mutations[:excess]
