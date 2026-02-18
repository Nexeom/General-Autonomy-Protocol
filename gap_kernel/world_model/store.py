"""
World Model Store — manages the structured representation of operational reality.

Updated by: Execution outcomes + External sensors
Queried by: Reconciler Loop + Strategy Layer
"""

from datetime import datetime
from typing import Dict, List, Optional

from gap_kernel.models.world import EntityState, WorldModel


class WorldModelStore:
    """
    In-memory world model store for the prototype.
    Production would use a persistent database.
    """

    def __init__(self):
        self._model = WorldModel(
            entities={},
            last_reconciled=datetime.utcnow(),
        )

    @property
    def model(self) -> WorldModel:
        """Get the current world model."""
        return self._model

    def upsert_entity(self, entity: EntityState) -> None:
        """Insert or update an entity in the world model."""
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

    def mark_reconciled(self) -> None:
        """Mark the world model as reconciled at the current time."""
        self._model.last_reconciled = datetime.utcnow()

    def get_state_snapshot(self) -> dict:
        """Get a serializable snapshot of the current world state."""
        return self._model.model_dump(mode="json")

    def update_from_execution(self, entity_id: str, updates: dict) -> None:
        """Apply execution result updates to an entity."""
        entity = self._model.entities.get(entity_id)
        if entity:
            entity.properties.update(updates)
            entity.last_updated = datetime.utcnow()
