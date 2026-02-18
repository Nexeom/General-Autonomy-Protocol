"""Governance Decision — output of the Governance Kernel's evaluation."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class GovernanceVerdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATE = "escalate"


class GovernanceDecision(BaseModel):
    """The Governance Kernel's ruling on a Strategy Proposal."""

    id: str
    proposal_id: str
    verdict: GovernanceVerdict
    violated_constraints: List[str] = []
    rejection_reason: Optional[str] = None       # Machine-readable
    rejection_detail: Optional[str] = None       # Human-readable
    authorization_tier: Optional[str] = None     # "auto_execute" | "notify_proceed" | "require_approval"
    policy_snapshot: dict = {}
    temporal_context: dict = {}
    evaluated_at: datetime
    evaluator: str = "governance_kernel"
