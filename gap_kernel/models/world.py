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
        super().update(merged)

    def setdefault(self, key, default=None):
        if key in PROTECTED_PROPERTIES and key not in self:
            return None
        return super().setdefault(key, default)

    def __ior__(self, other):
        self.update(other)
        return self


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
