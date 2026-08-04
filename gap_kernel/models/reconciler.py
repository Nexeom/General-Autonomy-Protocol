"""Reconciler configuration and dampening state."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# One day. A heartbeat or cooldown longer than this is indistinguishable from a
# stopped reconciler, and an interval of zero spins the loop with no pause.
_MAX_INTERVAL_SECONDS = 86_400


class ReconcilerConfig(BaseModel):
    """Configuration for the Reconciler Loop.

    Every field is bounded because this config is settable from outside the
    kernel (``PUT /reconciler/config``) and each value multiplies the work a
    single drift event costs: the retry budget is how many governance
    evaluations, proposals and lineage entries one drift produces, and the
    breaker threshold is how long a hopeless entity keeps being re-planned.
    Assignment is validated too, so a hostile value cannot be written to an
    already-constructed config.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    heartbeat_interval_seconds: int = Field(default=60, ge=1, le=_MAX_INTERVAL_SECONDS)
    drift_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    max_retry_budget: int = Field(default=3, ge=1, le=10)
    cooldown_seconds: int = Field(default=300, ge=0, le=_MAX_INTERVAL_SECONDS)
    circuit_breaker_threshold: int = Field(default=5, ge=1, le=100)


class DampeningState(BaseModel):
    """Prevents oscillation / flapping on a single entity."""

    entity_id: str
    last_intervention_at: datetime
    consecutive_failures: int = Field(default=0, ge=0)
    cooldown_until: Optional[datetime] = None
    circuit_broken: bool = False
    circuit_broken_at: Optional[datetime] = None
