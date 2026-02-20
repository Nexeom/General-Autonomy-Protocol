"""Decision Lineage Record — the complete audit chain for one reconciliation cycle."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from gap_kernel.models.governance import GovernanceDecision, UncertaintyDeclaration
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import StrategyProposal


class ArtifactProvenance(BaseModel):
    """
    Output Artifact Provenance — tracks governance-relevant metadata for durable outputs.

    Required when a governed action produces a durable output that persists
    beyond the action itself. Actions that modify state without producing
    a discrete artifact carry standard Decision Records without provenance.
    """
    artifact_id: str
    artifact_type: str                     # e.g., "report", "recommendation", "component"
    integrity_hash: str                    # Cryptographic verification of artifact content
    validation_evidence: dict = {}         # What validation was performed, by whom, results
    validation_independent: bool = False   # Whether validating entity is independent of producer
    validating_entity: Optional[str] = None
    quality_uncertainty: Optional[UncertaintyDeclaration] = None


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

    # STRUCTURED UNCERTAINTY
    uncertainty: Optional[UncertaintyDeclaration] = None

    # OUTPUT ARTIFACT PROVENANCE
    artifact_provenance: Optional[ArtifactProvenance] = None

    # INTEGRITY
    signature: str = ""
    prior_record_hash: Optional[str] = None
