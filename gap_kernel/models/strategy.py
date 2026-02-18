"""Strategy Proposal — what the Strategy Layer submits to Governance."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PlannedAction(BaseModel):
    """A single step in a strategy."""

    action_type: str                        # e.g., "send_email", "query_crm"
    target: str                             # e.g., "lead_12345"
    parameters: dict                        # Action-specific config
    requires_consent: bool = False
    reversible: bool = True
    risk_score: int = Field(ge=1, le=10)    # 1 = trivial, 10 = critical


class StrategyProposal(BaseModel):
    """A proposed plan of action, submitted to the Governance Kernel."""

    id: str
    intent_id: str                          # Which intent this serves
    attempt_number: int                     # Which retry (1, 2, 3...)
    plan_description: str                   # Human-readable summary
    actions: List[PlannedAction]            # Ordered list of execution steps
    estimated_cost: float                   # Projected cost
    rationale: str                          # Why this plan was chosen
    prior_rejection_id: Optional[str] = None  # If retry, which rejection prompted this
    generated_at: datetime
