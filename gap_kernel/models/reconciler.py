"""Reconciler configuration and dampening state."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ReconcilerConfig(BaseModel):
    """Configuration for the Reconciler Loop."""

    heartbeat_interval_seconds: int = 60
    drift_threshold: float = 0.7
    max_retry_budget: int = 3
    cooldown_seconds: int = 300
    circuit_breaker_threshold: int = 5


class DampeningState(BaseModel):
    """Prevents oscillation / flapping on a single entity."""

    entity_id: str
    last_intervention_at: datetime
    consecutive_failures: int = 0
    cooldown_until: Optional[datetime] = None
    circuit_broken: bool = False
