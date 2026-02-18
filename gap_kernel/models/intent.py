"""Intent Vector — the primary declaration object for GAP."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ConstraintType(str, Enum):
    HARD = "hard"   # Never violate. Rejection is automatic.
    SOFT = "soft"   # Prefer to satisfy. Can be deprioritized with lineage record.


class PolicyActivation(BaseModel):
    """Temporal authority — when this policy is active."""

    always: bool = True
    schedule: Optional[str] = None          # Cron expression
    condition: Optional[str] = None         # Runtime condition
    emergency_override: bool = False        # Suspends during declared emergencies


class Constraint(BaseModel):
    """A governance constraint attached to an intent."""

    name: str                               # e.g., "gdpr_consent_required"
    type: ConstraintType
    description: str                        # Human-readable rule
    activation: PolicyActivation = PolicyActivation()


class IntentVector(BaseModel):
    """The primary declaration object. Users define what 'good' looks like."""

    id: str
    objective: str
    priority: int = Field(ge=1, le=100)     # 1 = lowest, 100 = highest
    hard_constraints: List[Constraint]
    soft_constraints: List[Constraint]
    cost_ceiling: Optional[float] = None    # Max $ per action cycle
    created_by: str                         # Human who declared this intent
    created_at: datetime
    active: bool = True
