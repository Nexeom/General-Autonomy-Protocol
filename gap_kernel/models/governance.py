"""Governance Decision — output of the Governance Kernel's evaluation."""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class GovernanceVerdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATE = "escalate"


class AuthorizationLevel(str, Enum):
    """Graduated authorization levels (L0-L4) from the GAP spec."""
    L0 = "L0"  # Fully Autonomous — pre-approved routine operations
    L1 = "L1"  # Notify — execute autonomously, notify human after
    L2 = "L2"  # Approve Before — propose action, await human approval
    L3 = "L3"  # Collaborative — joint human-AI decision process
    L4 = "L4"  # Human Only — system provides analysis, human decides


class UncertaintyDeclaration(BaseModel):
    """
    Structured Uncertainty — what the system did NOT know at the time of decision.

    Every Decision Record carries this declaration, documenting assumptions,
    watch conditions, and known unknowns. Over time, the system tracks which
    declarations materialize into actual problems, creating a calibration loop.
    """
    assumptions: List[str] = []
    watch_conditions: List[str] = []
    evidence_basis: List[str] = []
    known_unknowns: List[str] = []
    confidence_level: float = Field(ge=0.0, le=1.0, default=0.8)
    calibration_notes: Optional[str] = None


class RiskProfile(BaseModel):
    """Risk profile for an action type in the Action Type Registry."""
    impact_scope: str = "local"             # "local" | "team" | "org" | "external"
    reversibility: str = "reversible"       # "reversible" | "partially_reversible" | "irreversible"
    blast_radius: str = "narrow"            # "narrow" | "moderate" | "wide"


class PhaseConfig(BaseModel):
    """Phase configuration for multi-phase authorization."""
    phase_name: str
    required: bool = True
    default_authorization_level: AuthorizationLevel = AuthorizationLevel.L1
    escalation_on_deviation: bool = False


class ActionTypeSpec(BaseModel):
    """
    Action Type Registry entry — governance configuration for an action category.

    Each registered action type carries its own governance config: default
    authorization level, applicable policy set, risk profile, escalation rules,
    and phase configuration.
    """
    type_id: str
    description: str
    risk_profile: RiskProfile = RiskProfile()
    default_authorization_level: AuthorizationLevel = AuthorizationLevel.L1
    applicable_policies: List[str] = []
    escalation_config: Dict[str, str] = {}
    phase_config: List[PhaseConfig] = []
    registered_by: str = "system"
    registered_at: Optional[datetime] = None


class GovernancePhaseResult(BaseModel):
    """Result of evaluating a single phase in a multi-phase authorization."""
    phase_name: str
    verdict: GovernanceVerdict
    authorization_level: AuthorizationLevel
    violated_constraints: List[str] = []
    rejection_reason: Optional[str] = None
    rejection_detail: Optional[str] = None
    evaluated_at: datetime


class GovernanceDecision(BaseModel):
    """The Governance Kernel's ruling on a Strategy Proposal."""

    id: str
    proposal_id: str
    verdict: GovernanceVerdict
    violated_constraints: List[str] = []
    rejection_reason: Optional[str] = None       # Machine-readable
    rejection_detail: Optional[str] = None       # Human-readable
    authorization_level: Optional[AuthorizationLevel] = None
    # Legacy field preserved for backward compatibility during migration
    authorization_tier: Optional[str] = None
    policy_snapshot: dict = {}
    temporal_context: dict = {}
    evaluated_at: datetime
    evaluator: str = "governance_kernel"
    uncertainty: Optional[UncertaintyDeclaration] = None
    action_type_id: Optional[str] = None
    phase_results: List[GovernancePhaseResult] = []
