"""Intent Vector — the primary declaration object for GAP."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ConstraintType(str, Enum):
    HARD = "hard"   # Never violate. Rejection is automatic.
    SOFT = "soft"   # Prefer to satisfy. Can be deprioritized with lineage record.


class PolicyTier(int, Enum):
    """Policy Tier Classification (Fix 3). Lower tiers may only add restrictions;
    they can never weaken an upper tier. The ordering is Tier 3 <= Tier 2 <= Tier 1.

    Tier 1 (regulatory floor) is loaded from a signed Applicability Profile the
    runtime cannot mutate and is always active — it cannot be suspended, narrowed,
    or scheduled off by configuration at any lower tier.
    """
    REGULATORY_FLOOR = 1   # Tier 1 — immutable legal/regulatory minimum
    ORG_POLICY = 2         # Tier 2 — client policy, stricter than Tier 1 only
    OPERATIONAL = 3        # Tier 3 — agent-tunable within Tier 1 and Tier 2


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
    # Optional structured numeric threshold for evaluators that need one (e.g. the
    # AML transaction floor, the PHI minimum-necessary record cap). Read directly
    # so the threshold is unambiguous — never parsed by guesswork from the free-text
    # `description` (a statutory citation like "45 CFR 164.514" must not be mistaken
    # for a threshold).
    threshold: Optional[float] = None
    # Policy Tier (Fix 3). Defaults to operational; Tier-1 regulatory-floor
    # constraints are declared in a signed Applicability Profile, not here.
    tier: PolicyTier = PolicyTier.OPERATIONAL


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
