"""Decision Lineage Record — the complete audit chain for one reconciliation cycle."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from gap_kernel.models.governance import GovernanceDecision
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import StrategyProposal


class LineageRecord(BaseModel):
    """
    The System of Record entry. One per reconciliation cycle.
    Every field answers: what happened, why, under whose authority, and what resulted.
    """

    id: str
    cycle_id: str  # Groups all attempts for one drift event

    # WHAT TRIGGERED THIS
    intent: IntentVector
    drift_detected: str
    drift_severity: int
    world_state_snapshot: dict

    # WHAT WAS PROPOSED
    proposals: List[StrategyProposal]

    # WHAT GOVERNANCE DECIDED
    governance_decisions: List[GovernanceDecision]

    # WHAT WAS EXECUTED
    final_approved_proposal: Optional[str] = None
    execution_result: Optional[dict] = None
    execution_success: bool = False

    # META
    total_attempts: int
    escalated_to_human: bool = False
    human_authorization_token: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_duration_seconds: Optional[float] = None

    # CONFLICT RESOLUTION
    conflicting_intents: Optional[List[str]] = None
    priority_override_applied: bool = False
    deprioritized_intent: Optional[str] = None
    deprioritization_rationale: Optional[str] = None

    # INTEGRITY
    signature: str = ""
    prior_record_hash: Optional[str] = None
