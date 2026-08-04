"""
World Model Store — manages the structured representation of operational reality.

Updated by: Execution outcomes + External sensors
Queried by: Reconciler Loop + Strategy Layer

Part of what it carries is not telemetry but EVIDENCE: the Governance Kernel's
constraint evaluators rule on a lead's jurisdiction, its GDPR consent and its
local hour, so whatever can write those facts decides the verdict. Those keys
are declared here and only count as evidence when they arrive through a channel
the deployment has declared attested; the store is their sole provenance writer.

Scope of that guarantee, stated bluntly: **the provenance record is a plain
field, not a signature, so it is forgeable and this store is not where the
guarantee lives.** It stops every writer that goes through the store — an
anonymous HTTP ingest, an executor's write-back, a merge carrying a supplied
stamp — and it is downgrade-only, so such a write can turn an approval into a
rejection but never the reverse.

It does NOT stop a caller that assembles the ``WorldModel`` itself. That is not
an exotic case: in the isolated posture the agent legitimately authors the whole
``world_state`` field of an ``evaluate`` request, and ``model_validate``
reconstructs the stamp from that JSON without it ever passing through this
store. A hand-written ``{"attested": True, "governance_properties": [...]}``
therefore reads as attested to the kernel. This needs no code execution — it is
reachable through the boundary's own published interface by any party that can
supply data.

Closing it requires the evidence to be signed by a key the agent side does not
hold, and verified by the kernel rather than read off the entity.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel

from gap_kernel._time import utcnow
from gap_kernel.models.world import (
    EVIDENCE_PROPERTY as _EVIDENCE_PROPERTY,
    GOVERNANCE_RELEVANT_PROPERTIES as _GOVERNANCE_RELEVANT_PROPERTIES,
    EntityState,
    WorldModel,
)

logger = logging.getLogger("gap_kernel.world_model")

# The governance-relevant property set: every key a ``_check_*`` evaluator in the
# Governance Kernel reads off an entity to decide whether a HARD constraint is
# satisfied.
#   gdpr_consent          -> _check_gdpr_consent
#   geo / jurisdiction    -> _check_gdpr_consent (which jurisdiction applies)
#   local_hour            -> _check_contact_hours
# A key added to a world-model-backed evaluator belongs here too, otherwise the
# kernel would rule on a fact nobody vouched for.
# Both are defined on the model (gap_kernel.models.world) so that the properties
# themselves can defend the invariant: a direct write to a governance-relevant
# key revokes its attested standing wherever that write comes from, not only on
# the paths this store owns.
GOVERNANCE_RELEVANT_PROPERTIES = _GOVERNANCE_RELEVANT_PROPERTIES

# The reserved property carrying an entity's evidence provenance. The store is
# its only *attesting* writer: a caller-supplied value is discarded before every
# write, so an anonymous ingest cannot declare its own facts attested.
EVIDENCE_PROPERTY = _EVIDENCE_PROPERTY

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
    what it reports — a consent-of-record system, a signed regulatory feed. An
    anonymous HTTP POST is a channel like any other; it simply is not an attested
    one, and facts arriving on it cannot satisfy a HARD constraint.
    """

    channel_id: str
    attested: bool = False
    description: str = ""


UNATTESTED = EvidenceChannel(
    channel_id=UNDECLARED_CHANNEL,
    attested=False,
    description="No evidence channel was declared for this write",
)


def governance_properties(properties: Dict[str, Any]) -> List[str]:
    """The governance-relevant keys present in a property mapping."""
    return sorted(k for k in properties if k in GOVERNANCE_RELEVANT_PROPERTIES)


def attested_properties(entity: EntityState) -> Set[str]:
    """The governance-relevant keys on ``entity`` backed by attested provenance.

    Anything not in here is a fact the kernel has no attested source for. An
    entity that never passed through the store — assembled by hand, or decoded
    from an RPC payload that carried no stamp — returns the empty set, which is
    the fail-closed answer.
    """
    stamp = entity.properties.get(EVIDENCE_PROPERTY)
    if not isinstance(stamp, dict) or not stamp.get("attested"):
        return set()
    declared = stamp.get("governance_properties")
    if not isinstance(declared, (list, tuple, set)):
        return set()
    return {k for k in declared if k in GOVERNANCE_RELEVANT_PROPERTIES}


def evidence_is_attested(entity: EntityState, property_name: str) -> bool:
    """True if ``property_name`` on ``entity`` came through an attested channel."""
    return property_name in attested_properties(entity)


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

        Ordinary properties are stored as supplied. Governance-relevant ones are
        stamped with the provenance they arrived under: without an attested
        ``channel`` they are recorded UNATTESTED, and a governed kernel refuses to
        certify a constraint that depends on them. Every governance-relevant
        change is recorded, so a consent flip is at minimum auditable.
        """
        channel = channel or UNATTESTED
        properties = entity.properties
        # Provenance is never caller-supplied — the stamp is the whole attack in
        # one field, so anything that arrived under that key is discarded.
        properties.pop(EVIDENCE_PROPERTY, None)

        relevant = governance_properties(properties)
        previous = self._model.entities.get(entity.entity_id)
        self._record_mutations(
            entity_id=entity.entity_id,
            before=previous.properties if previous is not None else {},
            after=properties,
            channel=channel,
        )
        self._stamp(entity, relevant if channel.attested else [], channel)
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
        world model. A governance-relevant key written here carries only the
        provenance of the channel that wrote it: an unattested outcome demotes
        that key, and no outcome can upgrade one it did not supply.
        """
        entity = self._model.entities.get(entity_id)
        if not entity:
            return
        channel = channel or UNATTESTED
        merged = {k: v for k, v in updates.items() if k != EVIDENCE_PROPERTY}
        written = governance_properties(merged)

        self._record_mutations(
            entity_id=entity_id,
            before=entity.properties,
            after={**entity.properties, **merged},
            channel=channel,
            keys=written,
        )
        still_attested = attested_properties(entity) - set(written)
        if channel.attested:
            still_attested |= set(written)
        entity.properties.update(merged)
        entity.last_updated = utcnow()
        self._stamp(entity, sorted(still_attested), channel)

    # --- Evidence provenance ------------------------------------------------

    def _stamp(
        self,
        entity: EntityState,
        attested: List[str],
        channel: EvidenceChannel,
    ) -> None:
        """Write the entity's provenance record, or none if it carries no
        governance-relevant facts (nothing to vouch for)."""
        if not governance_properties(entity.properties):
            entity.properties.pop(EVIDENCE_PROPERTY, None)
            return
        entity.properties[EVIDENCE_PROPERTY] = {
            "channel": channel.channel_id,
            "attested": bool(attested),
            "governance_properties": list(attested),
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
