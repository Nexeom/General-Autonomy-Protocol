"""World Model — structured representation of operational reality."""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from gap_kernel._time import utcnow

logger = logging.getLogger("gap_kernel.world_model")

# Properties GAP derives from its own executed outcomes. They are evidence that
# the kernel acted, and safety logic reads them back — the reconciler treats
# ``last_contacted`` as proof an SLA obligation was served and stops watching the
# entity. So a bulk merge of caller-supplied properties (an executor writing
# arbitrary fields, an external sensor payload) must not be able to forge them:
# they are set only by a deliberate, single-key write from the component that
# produced the outcome (see ``EntityState.record_contact``).
PROTECTED_PROPERTIES = frozenset({"last_contacted", "contact_method"})

# Properties a governed kernel's constraint evaluators read to decide whether an
# action is permitted. They live here rather than in the store because the
# invariant they carry belongs to the data: a value is only as trustworthy as the
# channel that last wrote it, and any writer can reach these attributes.
GOVERNANCE_RELEVANT_PROPERTIES = frozenset({
    "gdpr_consent",
    "geo",
    "jurisdiction",
    "local_hour",
})

# The reserved property carrying the store's channel breadcrumb: which declared
# channel last wrote this entity's governance-relevant facts. It is an AUDIT
# record and nothing more — no kernel code reads it, and it cannot make a fact
# usable. It was once the trust decision itself, which is precisely the defect
# Signed Evidence Attestation replaces: a plain dict on an entity the agent
# assembles is a claim the agent writes.
EVIDENCE_PROPERTY = "_evidence_provenance"

# The reserved property carrying an entity's SIGNED evidence attestation — an
# Ed25519 signature by a registered issuer over (entity_id, governance key ->
# value, issued_at, expires_at, issuer_key_id). This is the only thing a
# governed kernel accepts as a source for a governance-relevant fact, and the
# kernel verifies it itself rather than reading a flag off the entity. See
# ``gap_kernel.world_model.attestation``.
EVIDENCE_ATTESTATION_PROPERTY = "_evidence_attestation"

# The drift log is embedded in every world-state snapshot, and every snapshot is
# copied into a lineage record, so an unbounded log grows each audit record
# without limit. Only recent drift is operationally meaningful; the durable
# history lives in the lineage chain.
MAX_DRIFT_EVENTS = 250


class EntityProperties(dict):
    """Entity properties whose protected keys survive an untrusted bulk merge.

    A merge (``update``, ``|=``, ``setdefault``) folds a caller-supplied mapping
    into the entity, so it is the path arbitrary values take. Protected keys are
    dropped from it rather than being allowed to overwrite kernel-derived
    evidence; every other key merges normally.
    """

    def update(self, *args, **kwargs) -> None:
        merged = dict(*args, **kwargs)
        refused = [key for key in merged if key in PROTECTED_PROPERTIES]
        for key in refused:
            del merged[key]
        if refused:
            logger.warning(
                "Refused a property merge writing kernel-derived evidence: %s",
                ", ".join(sorted(refused)),
            )
        self._warn_on_direct_evidence_write(merged)
        super().update(merged)

    def __setitem__(self, key, value) -> None:
        self._warn_on_direct_evidence_write((key,))
        super().__setitem__(key, value)

    def setdefault(self, key, default=None):
        if key in PROTECTED_PROPERTIES and key not in self:
            return None
        if key not in self:
            self._warn_on_direct_evidence_write((key,))
        return super().setdefault(key, default)

    def __ior__(self, other):
        self.update(other)
        return self

    def _warn_on_direct_evidence_write(self, keys) -> None:
        """Log a direct write to a governance-relevant key. It edits nothing.

        This method used to strike the written key out of the ``_evidence_provenance``
        stamp, so that a write reaching these attributes directly lost its
        attested standing. Signed Evidence Attestation subsumes that and does it
        better: the signature binds the VALUES it was issued over, so a write
        that CHANGES a governance value already fails the verifier's value check,
        while a write setting the SAME value is a no-op that should not revoke
        anything. Editing the stamp on top of that could only revoke attestations
        that were still perfectly valid.

        Two honest limits on this warning. It is a log line, not a control — it
        does not decide anything, and nothing downstream reads it. And on the RPC
        path it never fired even before: ``EntityState._guard_properties``
        rebuilds properties via ``EntityProperties(value)``, a plain dict
        initialization that goes nowhere near ``__setitem__``, so an entity
        arriving in an ``evaluate`` request reaches the kernel without this code
        ever running. What defends that path is the kernel-side verifier.
        """
        touched = sorted(k for k in keys if k in GOVERNANCE_RELEVANT_PROPERTIES)
        if not touched or EVIDENCE_ATTESTATION_PROPERTY not in self:
            return
        logger.warning(
            "Governance-relevant evidence written outside the world-model store: "
            "%s. The signed attestation on this entity binds the values it was "
            "issued over, so a write that changes one leaves the attestation "
            "failing its value check.",
            ", ".join(touched),
        )


class DriftEventLog(list):
    """Drift events, capped at the most recent ``MAX_DRIFT_EVENTS`` entries."""

    def append(self, item) -> None:
        super().append(item)
        self._trim()

    def extend(self, items) -> None:
        super().extend(items)
        self._trim()

    def __iadd__(self, items):
        self.extend(items)
        return self

    def _trim(self) -> None:
        excess = len(self) - MAX_DRIFT_EVENTS
        if excess > 0:
            del self[:excess]


class EntityState(BaseModel):
    """A single entity being tracked in the World Model."""

    entity_type: str                        # e.g., "lead", "ticket"
    entity_id: str                          # External system ID
    properties: dict                        # Current known state
    last_updated: datetime
    source: str                             # Where this data came from
    confidence: float = Field(ge=0, le=1, default=1.0)
    obligations: List[str] = []             # Active intent IDs that govern this entity

    @field_validator("properties", mode="after")
    @classmethod
    def _guard_properties(cls, value: dict) -> dict:
        if isinstance(value, EntityProperties):
            return value
        return EntityProperties(value)

    def record_contact(self, method: str, at: Optional[datetime] = None) -> dict:
        """Record that GAP contacted this entity — the sanctioned writer for the
        protected contact properties. Returns the world-state change it made."""
        contacted_at = (at or utcnow()).isoformat()
        self.properties["last_contacted"] = contacted_at
        self.properties["contact_method"] = method
        self.last_updated = utcnow()
        return {
            "entity_id": self.entity_id,
            "field": "last_contacted",
            "new_value": contacted_at,
            "source": method,
        }


class WorldModel(BaseModel):
    """The system's internal representation of operational reality."""

    entities: Dict[str, EntityState] = {}
    last_reconciled: datetime
    drift_events: List[dict] = Field(default_factory=DriftEventLog)

    @field_validator("drift_events", mode="after")
    @classmethod
    def _bound_drift_events(cls, value: List[dict]) -> List[dict]:
        if isinstance(value, DriftEventLog):
            return value
        return DriftEventLog(value[-MAX_DRIFT_EVENTS:])

    @field_validator("entities", mode="after")
    @classmethod
    def _entity_key_is_its_identity(
        cls, value: Dict[str, "EntityState"]
    ) -> Dict[str, "EntityState"]:
        """An entity's map key and its ``entity_id`` must be the same string.

        There are two ways to name an entity here, and different code reaches
        for different ones: the kernel resolves a proposal's target by MAP KEY
        (``world_state.entities.get(action.target)``) while an evidence
        attestation binds to the ``entity_id`` FIELD. If those may diverge, a
        caller files a genuinely attested entity under any key it likes and
        targets that key — the attestation verifies against the field, the
        gate reads the entity under the key, and one real consent record
        authorizes outreach to arbitrarily many made-up targets.

        ``WorldModelStore`` keys by ``entity.entity_id`` and so cannot produce
        the divergence, but a ``WorldModel`` deserialized from an ``evaluate``
        request is authored by the agent and can. Rejecting here closes it on
        that path, which is the one that matters.
        """
        mismatched = sorted(
            f"{key!r} -> {entity.entity_id!r}"
            for key, entity in value.items()
            if key != entity.entity_id
        )
        if mismatched:
            raise ValueError(
                "world model entities must be keyed by their own entity_id; "
                f"mismatched: {', '.join(mismatched)}"
            )
        return value
