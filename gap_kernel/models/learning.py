"""Learning Model — operational heuristics and policy proposals."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class OperationalHeuristic(BaseModel):
    """A learned search pattern. Does NOT modify policy."""

    id: str
    pattern: str                            # e.g., "geo:EU → prepend consent_verification"
    source_lineage_ids: List[str]           # Which rejection cycles taught this
    hit_count: int = 0
    success_rate: float = 0.0
    status: str = "active"                  # "active" | "deprecated"
    learned_at: datetime


class PolicyProposal(BaseModel):
    """Proposed change to governance rules. Must be human-approved."""

    id: str
    proposed_change: str
    rationale: str
    supporting_lineage_ids: List[str]
    risk_assessment: str
    proposed_by: str = "strategy_layer"
    status: str = "pending_review"          # "pending_review" | "approved" | "rejected"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
