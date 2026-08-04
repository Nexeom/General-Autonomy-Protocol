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

# The reserved property carrying an entity's evidence provenance.
EVIDENCE_PROPERTY = "_evidence_provenance"

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
        self._invalidate_provenance(merged)
        super().update(merged)

    def __setitem__(self, key, value) -> None:
        self._invalidate_provenance((key,))
        super().__setitem__(key, value)

    def setdefault(self, key, default=None):
        if key in PROTECTED_PROPERTIES and key not in self:
            return None
        if key not in self:
            self._invalidate_provenance((key,))
        return super().setdefault(key, default)

    def __ior__(self, other):
        self.update(other)
        return self

    def _invalidate_provenance(self, keys) -> None:
        """Drop the attested standing of any governance-relevant key being written.

        A provenance stamp vouches for the values that were present when the
        store wrote it. Any later write reaching these attributes directly —
        an executor's result write-back, a sensor payload, anything that did not
        arrive through ``WorldModelStore`` — leaves the stamp vouching for a value
        it never saw, which is how an agent launders its own consent. The write
        is allowed; what it loses is its attested standing, until an attested
        channel re-establishes it. ``WorldModelStore`` stamps after writing, so
        its own path is unaffected.
        """
        touched = {k for k in keys if k in GOVERNANCE_RELEVANT_PROPERTIES}
        if not touched:
            return
        stamp = dict.get(self, EVIDENCE_PROPERTY)
        if not isinstance(stamp, dict):
            return
        declared = stamp.get("governance_properties")
        if not isinstance(declared, (list, tuple, set)):
            return
        remaining = [k for k in declared if k not in touched]
        if len(remaining) == len(declared):
            return
        revoked = sorted(set(declared) - set(remaining))
        updated = dict(stamp)
        updated["governance_properties"] = remaining
        # dict.__setitem__ directly: this key is not governance-relevant, and
        # going through __setitem__ would re-enter this method.
        dict.__setitem__(self, EVIDENCE_PROPERTY, updated)
        logger.warning(
            "Evidence written outside the world-model store; %s no longer attested: %s",
            "is" if len(revoked) == 1 else "are",
            ", ".join(revoked),
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
