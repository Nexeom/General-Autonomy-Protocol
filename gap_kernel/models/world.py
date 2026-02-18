"""World Model — structured representation of operational reality."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class EntityState(BaseModel):
    """A single entity being tracked in the World Model."""

    entity_type: str                        # e.g., "lead", "ticket"
    entity_id: str                          # External system ID
    properties: dict                        # Current known state
    last_updated: datetime
    source: str                             # Where this data came from
    confidence: float = Field(ge=0, le=1, default=1.0)
    obligations: List[str] = []             # Active intent IDs that govern this entity


class WorldModel(BaseModel):
    """The system's internal representation of operational reality."""

    entities: Dict[str, EntityState] = {}
    last_reconciled: datetime
    drift_events: List[dict] = []
