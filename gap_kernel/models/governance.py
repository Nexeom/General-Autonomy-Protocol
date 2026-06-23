"""Governance Decision — output of the Governance Kernel's evaluation."""

import json
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

    # Dynamic Risk Escalation fields (Added 2026-02-23)
    escalation_triggered: bool = False
    escalation_reason: Optional[str] = None
    original_authorization_level: Optional[str] = None
    escalated_authorization_level: Optional[str] = None
    escalation_evidence: Optional[dict] = None

    # Out-of-Band Authority Verification fields (Added 2026-02-23).
    # Descriptive metadata about how a human approved (method/channel). Retained
    # for the Decision Record; no longer the enforcement mechanism — see below.
    authority_verification_method: Optional[str] = None  # e.g., "hardware_key", "oob_code", "biometric"
    authority_verification_channel: Optional[str] = None  # e.g., "independent_mfa", "physical_token"
    authority_verified_at: Optional[datetime] = None

    # Cryptographic OOB approval (Fix 4 — Added 2026-06-22). For L2+ actions the
    # human approver signs this specific Decision Record ID (and its expiry) over
    # an agent-independent channel; the Execution Fabric verifies the signature
    # against a registered public key and consumes it in a persistent replay
    # ledger. This is the enforced control, replacing the prior string checks.
    human_approval_signature: Optional[str] = None       # hex Ed25519 signature over id+expiry
    human_approver_public_key_id: Optional[str] = None   # key id resolved via PublicKeyRegistry
    human_approval_timestamp: Optional[datetime] = None
    human_approval_valid_until: Optional[datetime] = None

    # Decision signature (Fix 2 — Added 2026-06-22). The Governance Kernel signs
    # every decision with its private key; the Execution Fabric verifies this
    # before acting. An in-process agent cannot forge an approval, because
    # forging requires the kernel's private key, which the agent does not hold —
    # this is the cryptographic half of the Iron Rule / structural boundary.
    decision_signature: Optional[str] = None
    kernel_public_key_id: Optional[str] = None

    # Content binding (re-audit fix): a digest of the proposal this decision
    # authorizes, so a same-id proposal with mutated actions cannot be executed
    # under it. Set by the kernel, re-checked by the Execution Fabric.
    proposal_digest: Optional[str] = None


def canonical_decision_payload(decision: "GovernanceDecision") -> str:
    """Deterministic serialization the kernel signs and the Execution Fabric verifies.

    Excludes the kernel signature itself and the downstream human-approval
    attestations (OOB fields), which are added *after* the kernel rules and are
    verified separately — so the kernel signature stays stable across the
    approval flow while still binding the verdict, authorization level, violated
    constraints, uncertainty, and the proposal digest. Carries a domain tag so a
    signature over this format cannot be confused with another signed payload.
    """
    data = decision.model_dump(
        mode="json",
        exclude={
            "decision_signature",
            "kernel_public_key_id",
            "authority_verification_method",
            "authority_verification_channel",
            "authority_verified_at",
            "human_approval_signature",
            "human_approver_public_key_id",
            "human_approval_timestamp",
            "human_approval_valid_until",
        },
    )
    data["_domain"] = "gap.governance.decision.v1"
    return json.dumps(data, sort_keys=True, default=str)
