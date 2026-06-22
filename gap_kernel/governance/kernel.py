"""
Governance Kernel — Layer 1 of the GAP architecture.

Evaluates Strategy Proposals against active policies. Returns structured
approve/reject/escalate decisions. Immutable from below.

Behavioral Contract:
- Accepts a StrategyProposal and the current WorldModel
- Evaluates against all active Constraints (filtered by temporal authority)
- Returns a GovernanceDecision with machine-readable rejection reasons
- Never modifies its own policies
- Never accepts modification requests from the Strategy Layer or Execution Fabric
- Maintains the Action Type Registry — actions with unregistered types are rejected
- Supports Multi-Phase Authorization for complex action lifecycles
- Generates Structured Uncertainty declarations for every decision
- Enforces Separation of Creation and Validation for governed outputs
"""

from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

from croniter import croniter

from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair, sign
from gap_kernel.governance.dynamic_risk import (
    DynamicRiskEngine,
    EscalationConfig,
)
from gap_kernel.governance.profile import ApplicabilityProfile, verify_profile
from gap_kernel.models.governance import (
    ActionTypeSpec,
    AuthorizationLevel,
    GovernanceDecision,
    GovernancePhaseResult,
    GovernanceVerdict,
    PhaseConfig,
    RiskProfile,
    UncertaintyDeclaration,
    canonical_decision_payload,
)
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyActivation,
    PolicyTier,
)
from gap_kernel.models.strategy import StrategyProposal
from gap_kernel.models.world import WorldModel


# ---------------------------------------------------------------------------
# Baseline Action Type Definitions (from spec)
# ---------------------------------------------------------------------------

_BASELINE_ACTION_TYPES: Dict[str, ActionTypeSpec] = {
    "task_execution": ActionTypeSpec(
        type_id="task_execution",
        description="Executing an operational task within an existing capability",
        risk_profile=RiskProfile(impact_scope="local", reversibility="reversible", blast_radius="narrow"),
        default_authorization_level=AuthorizationLevel.L0,
    ),
    "skill_modification": ActionTypeSpec(
        type_id="skill_modification",
        description="Modifying the instructions, criteria, or parameters of an existing capability",
        risk_profile=RiskProfile(impact_scope="team", reversibility="partially_reversible", blast_radius="moderate"),
        default_authorization_level=AuthorizationLevel.L2,
    ),
    "drift_reconciliation": ActionTypeSpec(
        type_id="drift_reconciliation",
        description="Autonomous corrective action when world state diverges from declared intent",
        risk_profile=RiskProfile(impact_scope="local", reversibility="reversible", blast_radius="narrow"),
        default_authorization_level=AuthorizationLevel.L1,
    ),
    "escalation": ActionTypeSpec(
        type_id="escalation",
        description="Routing a decision to human authority at the system's authorized boundary",
        risk_profile=RiskProfile(impact_scope="local", reversibility="reversible", blast_radius="narrow"),
        default_authorization_level=AuthorizationLevel.L0,
    ),
    "policy_proposal": ActionTypeSpec(
        type_id="policy_proposal",
        description="Proposing a change to governance policy (human decides)",
        risk_profile=RiskProfile(impact_scope="org", reversibility="reversible", blast_radius="wide"),
        default_authorization_level=AuthorizationLevel.L4,
    ),
}


# ---------------------------------------------------------------------------
# Temporal Authority
# ---------------------------------------------------------------------------

def _is_constraint_active(constraint: Constraint, current_time: datetime) -> bool:
    """Determine if a constraint is active based on temporal authority."""
    activation = constraint.activation

    if activation.always:
        return True

    if activation.schedule:
        try:
            return croniter.match(activation.schedule, current_time)
        except (ValueError, KeyError):
            # Fail closed (Fix 1): a malformed schedule must not silently disable
            # a constraint. If the active window is unknowable, treat the
            # constraint as active so it is still evaluated — a broken schedule
            # on a HARD rule should be loud, not a silent bypass.
            return True

    return False


# ---------------------------------------------------------------------------
# Constraint Violation Checks
# ---------------------------------------------------------------------------

def _check_constraint_violation(
    proposal: StrategyProposal,
    constraint: Constraint,
    world_state: WorldModel,
) -> bool:
    """
    Check if a proposal violates a constraint.

    Uses a rule-based evaluation engine that maps constraint names to
    concrete checks. This is the extensible policy evaluation core.
    """
    check_fn = _CONSTRAINT_EVALUATORS.get(constraint.name)
    if check_fn:
        return check_fn(proposal, constraint, world_state)

    # Fail-closed (SA-2 / Fix 1): a constraint with no registered evaluator
    # cannot be certified as satisfied, so it is treated as a violation.
    # (Previously this fell through to a generic check that returned False,
    # silently passing every unrecognized constraint.) Callers gate this per
    # type — an unevaluable HARD constraint rejects; an unevaluable SOFT
    # constraint is a preference the kernel simply cannot score (see
    # evaluate_proposal).
    return True


def _check_gdpr_consent(
    proposal: StrategyProposal,
    constraint: Constraint,
    world_state: WorldModel,
) -> bool:
    """Check if proposal involves contacting an entity without GDPR consent."""
    for action in proposal.actions:
        if action.action_type in ("send_email", "send_sms", "direct_call", "automated_outreach"):
            target_id = action.target
            entity = world_state.entities.get(target_id)
            if entity:
                props = entity.properties
                geo = props.get("geo", props.get("jurisdiction", ""))
                is_eu = geo.upper() in (
                    "EU", "EEA", "DE", "FR", "IT", "ES", "NL", "BE", "AT",
                    "SE", "DK", "FI", "IE", "PT", "GR", "PL", "CZ", "RO",
                    "HU", "BG", "HR", "SK", "SI", "LT", "LV", "EE", "CY",
                    "MT", "LU",
                )
                if is_eu:
                    consent = props.get("gdpr_consent", False)
                    if not consent:
                        return True
            elif action.requires_consent:
                return True
    return False


def _check_contact_hours(
    proposal: StrategyProposal,
    constraint: Constraint,
    world_state: WorldModel,
) -> bool:
    """Check if proposal involves contacting an entity outside allowed hours."""
    for action in proposal.actions:
        if action.action_type in ("send_email", "send_sms", "direct_call", "automated_outreach"):
            target_id = action.target
            entity = world_state.entities.get(target_id)
            if entity:
                local_hour = entity.properties.get("local_hour")
                if local_hour is not None:
                    if local_hour >= 22 or local_hour < 7:
                        return True
    return False


def _check_cost_ceiling(
    proposal: StrategyProposal,
    constraint: Constraint,
    world_state: WorldModel,
) -> bool:
    """Check if proposal's estimated cost exceeds the ceiling."""
    import re
    match = re.search(r'\$(\d+(?:\.\d+)?)', constraint.description)
    if match:
        ceiling = float(match.group(1))
        if proposal.estimated_cost > ceiling:
            return True
    return False


# Registry of constraint-name -> concrete evaluator. A constraint whose name is
# absent here has no concrete check; the kernel cannot prove it satisfied and so
# fails closed (see _check_constraint_violation and evaluate_proposal).
_CONSTRAINT_EVALUATORS: Dict[str, callable] = {
    "gdpr_consent_required": _check_gdpr_consent,
    "no_contact_outside_hours": _check_contact_hours,
    "cost_ceiling": _check_cost_ceiling,
}


def _constraint_has_evaluator(constraint: Constraint) -> bool:
    """True if a concrete evaluator is registered for this constraint name."""
    return constraint.name in _CONSTRAINT_EVALUATORS


# ---------------------------------------------------------------------------
# Formatting Helpers
# ---------------------------------------------------------------------------

def _format_structured_reason(violations: List[str]) -> str:
    """Create a machine-readable rejection reason from violated constraint names."""
    return "|".join(violations)


def _format_human_reason(
    violations: List[str],
    proposal: StrategyProposal,
    world_state: WorldModel,
) -> str:
    """Create a human-readable rejection explanation."""
    parts = []
    for v in violations:
        if v == "gdpr_consent_required":
            targets = [a.target for a in proposal.actions
                       if a.action_type in ("send_email", "send_sms", "direct_call", "automated_outreach")]
            for target in targets:
                entity = world_state.entities.get(target)
                if entity:
                    geo = entity.properties.get("geo", entity.properties.get("jurisdiction", "unknown"))
                    parts.append(
                        f"Entity {target} is {geo} jurisdiction. "
                        f"No GDPR consent on file. "
                        f"Direct outreach prohibited without verified consent."
                    )
        elif v == "no_contact_outside_hours":
            parts.append("Automated outreach is restricted during this time window.")
        else:
            parts.append(f"Constraint '{v}' was violated.")
    return " ".join(parts) if parts else "One or more constraints were violated."


def _serialize_active_policies(constraints: List[Constraint]) -> dict:
    """Serialize the active policy set for snapshot inclusion in decisions."""
    return {
        "active_constraints": [
            {"name": c.name, "type": c.type.value, "description": c.description}
            for c in constraints
        ],
        "count": len(constraints),
    }


def _get_temporal_snapshot(current_time: datetime) -> dict:
    """Capture temporal context at the moment of evaluation."""
    return {
        "evaluated_at": current_time.isoformat(),
        "hour": current_time.hour,
        "weekday": current_time.strftime("%A"),
        "is_business_hours": 9 <= current_time.hour < 18,
    }


# ---------------------------------------------------------------------------
# Authorization Level Mapping (L0-L4)
# ---------------------------------------------------------------------------

_AUTH_RANK = {
    AuthorizationLevel.L0: 0,
    AuthorizationLevel.L1: 1,
    AuthorizationLevel.L2: 2,
    AuthorizationLevel.L3: 3,
    AuthorizationLevel.L4: 4,
}


def _satisfies_auth(granted: AuthorizationLevel, required: AuthorizationLevel) -> bool:
    """True if a granted authorization level meets or exceeds a required one."""
    return _AUTH_RANK[granted] >= _AUTH_RANK[required]


def _max_auth(a: AuthorizationLevel, b: AuthorizationLevel) -> AuthorizationLevel:
    """Return the higher (more restrictive) of two authorization levels."""
    return a if _AUTH_RANK[a] >= _AUTH_RANK[b] else b


def _determine_auth_level(max_risk: int) -> AuthorizationLevel:
    """
    Graduated authorization model (L0-L4):
      risk 1-3:  L0 (Fully Autonomous)  — pre-approved routine operations
      risk 4-5:  L1 (Notify)            — execute autonomously, notify human after
      risk 6-7:  L2 (Approve Before)    — propose action, await human approval
      risk 8:    L3 (Collaborative)      — joint human-AI decision process
      risk 9-10: L4 (Human Only)         — system provides analysis, human decides
    """
    if max_risk <= 3:
        return AuthorizationLevel.L0
    elif max_risk <= 5:
        return AuthorizationLevel.L1
    elif max_risk <= 7:
        return AuthorizationLevel.L2
    elif max_risk == 8:
        return AuthorizationLevel.L3
    else:
        return AuthorizationLevel.L4


def _determine_auth_tier(max_risk: int) -> str:
    """
    Legacy graduated authorization model (string-based).
    Preserved for backward compatibility with existing tests.
    """
    if max_risk <= 3:
        return "auto_execute"
    elif max_risk <= 6:
        return "notify_proceed"
    elif max_risk <= 8:
        return "require_approval"
    else:
        return "escalate"


# ---------------------------------------------------------------------------
# Structured Uncertainty Generation
# ---------------------------------------------------------------------------

def _build_uncertainty_declaration(
    proposal: StrategyProposal,
    world_state: WorldModel,
    active_constraints: List[Constraint],
    hard_violations: List[str],
    soft_violations: List[str],
) -> UncertaintyDeclaration:
    """
    Build a Structured Uncertainty Declaration for a governance decision.

    Documents what the system did NOT know at the time of evaluation:
    assumptions made, conditions that could invalidate the decision,
    evidence basis, and identified gaps.
    """
    assumptions = []
    watch_conditions = []
    evidence_basis = []
    known_unknowns = []

    for action in proposal.actions:
        entity = world_state.entities.get(action.target)
        if entity:
            if entity.confidence < 1.0:
                assumptions.append(
                    f"Entity {action.target} data confidence is "
                    f"{entity.confidence:.0%} (not fully verified)"
                )
                watch_conditions.append(
                    f"Entity {action.target} data may be stale or inaccurate"
                )
            evidence_basis.append(
                f"Entity {action.target}: source={entity.source}, "
                f"last_updated={entity.last_updated.isoformat()}"
                if hasattr(entity.last_updated, 'isoformat')
                else f"Entity {action.target}: source={entity.source}"
            )
        else:
            known_unknowns.append(
                f"No world model data for target entity {action.target}"
            )

    if not active_constraints:
        known_unknowns.append("No active constraints evaluated — policy may be incomplete")

    if soft_violations:
        watch_conditions.append(
            f"Soft constraints were violated: {', '.join(soft_violations)}. "
            f"These may indicate risk the hard constraint set does not cover."
        )

    entity_confidences = []
    for action in proposal.actions:
        entity = world_state.entities.get(action.target)
        if entity:
            entity_confidences.append(entity.confidence)

    if entity_confidences:
        avg_confidence = sum(entity_confidences) / len(entity_confidences)
    else:
        avg_confidence = 0.5

    confidence = avg_confidence
    if soft_violations:
        confidence *= 0.9
    if known_unknowns:
        confidence *= 0.8

    return UncertaintyDeclaration(
        assumptions=assumptions,
        watch_conditions=watch_conditions,
        evidence_basis=evidence_basis,
        known_unknowns=known_unknowns,
        confidence_level=round(min(1.0, max(0.0, confidence)), 2),
    )


# ---------------------------------------------------------------------------
# Intent Conflict Detection
# ---------------------------------------------------------------------------

def _detect_intent_conflicts(
    proposal: StrategyProposal,
    intents: List[IntentVector],
) -> Optional[List[IntentVector]]:
    """Detect if the proposal creates conflicts between intents."""
    serving_intent = None
    conflicting = []

    for intent in intents:
        if intent.id == proposal.intent_id:
            serving_intent = intent
            continue
        if not intent.active:
            continue

        for constraint in intent.hard_constraints:
            if _check_constraint_violation(proposal, constraint, WorldModel(
                entities={}, last_reconciled=datetime.utcnow()
            )):
                conflicting.append(intent)
                break

    return conflicting if conflicting else None


def resolve_intent_conflict(
    conflicting_intents: List[IntentVector],
    proposal: StrategyProposal,
) -> dict:
    """
    When intents conflict:
    1. Hard constraints are never violated (from any intent).
    2. Among valid solutions, optimize for highest priority intent.
    3. Record the tradeoff in lineage.
    """
    sorted_intents = sorted(
        conflicting_intents, key=lambda i: i.priority, reverse=True
    )
    primary = sorted_intents[0]
    deprioritized = sorted_intents[1:]

    return {
        "primary_intent": primary.id,
        "deprioritized": [i.id for i in deprioritized],
        "rationale": (
            f"Priority differential: {primary.priority} vs "
            f"{[i.priority for i in deprioritized]}"
        ),
        "hard_constraints_preserved": True,
    }


# ---------------------------------------------------------------------------
# Governance Kernel
# ---------------------------------------------------------------------------

class GovernanceKernel:
    """
    The Governance Kernel — evaluates proposals against active policies.

    Immutable from below. Only human-declared intents define its behavior.

    Maintains:
    - Action Type Registry: categories of autonomous action with governance config
    - Multi-Phase Authorization: multiple governance gates per action lifecycle
    - Structured Uncertainty: epistemic state at moment of authorization
    - Separation of Creation and Validation enforcement
    """

    def __init__(
        self,
        escalation_config: Optional[EscalationConfig] = None,
        strict_action_typing: bool = False,
        applicability_profile: Optional[ApplicabilityProfile] = None,
        profile_key_registry: Optional[PublicKeyRegistry] = None,
        signing_key_hex: Optional[str] = None,
        public_key_hex: Optional[str] = None,
        kernel_key_id: str = "governance_kernel",
    ):
        self._action_type_registry: Dict[str, ActionTypeSpec] = dict(_BASELINE_ACTION_TYPES)
        self._dynamic_risk_engine = DynamicRiskEngine(
            escalation_config or EscalationConfig()
        )
        # Kernel signing key (Fix 2). Every decision is signed so the Execution
        # Fabric can verify it was produced by the kernel and not forged by an
        # in-process agent. Generated per-kernel by default; production injects a
        # managed key and shares only the public key with the execution layer.
        if signing_key_hex and public_key_hex:
            self._signing_key_hex = signing_key_hex
            self._public_key_hex = public_key_hex
        else:
            self._signing_key_hex, self._public_key_hex = generate_keypair()
        self._kernel_key_id = kernel_key_id
        # Fail-closed action typing (SA-2 / Fix 1). When True, every proposal
        # must declare a registered action_type_id or it is rejected — closing
        # the bypass where omitting the field skipped the Action Type Registry
        # gate entirely. Defaults False to preserve open-deployment behavior; a
        # loaded Applicability Profile flips this on (the floor is now declared).
        self._strict_action_typing = strict_action_typing or applicability_profile is not None

        # Tier-1 regulatory floor (Fix 3). Loaded from a SIGNED Applicability
        # Profile and verified here; an unsigned/tampered/unknown-key profile is
        # refused (fail closed). Floor constraints are normalized to Tier 1 and
        # always-active, so no lower-tier configuration can weaken or suspend
        # them — embodying the Tier 3 <= Tier 2 <= Tier 1 guarantee.
        self._tier1_floor: List[Constraint] = []
        if applicability_profile is not None:
            verify_profile(
                applicability_profile, profile_key_registry or PublicKeyRegistry()
            )
            self._tier1_floor = [
                c.model_copy(
                    update={
                        "tier": PolicyTier.REGULATORY_FLOOR,
                        "activation": PolicyActivation(always=True),
                    }
                )
                for c in applicability_profile.tier1_constraints
            ]

    # --- Action Type Registry ---

    def get_action_type(self, type_id: str) -> Optional[ActionTypeSpec]:
        """Look up an action type in the registry."""
        return self._action_type_registry.get(type_id)

    def get_registered_action_types(self) -> Dict[str, ActionTypeSpec]:
        """Get all registered action types."""
        return dict(self._action_type_registry)

    def register_action_type(
        self,
        spec: ActionTypeSpec,
        registered_by: str,
    ) -> ActionTypeSpec:
        """
        Register a new action type. This is a governed action requiring
        human authorization — autonomous systems cannot register new types.
        """
        spec.registered_by = registered_by
        spec.registered_at = datetime.utcnow()
        self._action_type_registry[spec.type_id] = spec
        return spec

    def validate_action_type(self, action_type: str) -> bool:
        """Check if an action type is registered. Unregistered types are rejected."""
        return action_type in self._action_type_registry

    # --- Multi-Phase Authorization ---

    def evaluate_phase(
        self,
        phase: PhaseConfig,
        proposal: StrategyProposal,
        intents: List[IntentVector],
        world_state: WorldModel,
        current_time: Optional[datetime] = None,
        prior_phase_results: Optional[List[GovernancePhaseResult]] = None,
    ) -> GovernancePhaseResult:
        """
        Evaluate a single phase in a multi-phase authorization lifecycle.

        Each phase evaluates against different information. Authorization at
        one phase does not automatically satisfy subsequent phases.
        """
        if current_time is None:
            current_time = datetime.utcnow()

        active_constraints = self._get_active_constraints(intents, current_time)

        hard_violations = []
        for constraint in active_constraints:
            if constraint.type == ConstraintType.HARD:
                if _check_constraint_violation(proposal, constraint, world_state):
                    hard_violations.append(constraint.name)

        if hard_violations:
            return GovernancePhaseResult(
                phase_name=phase.phase_name,
                verdict=GovernanceVerdict.REJECTED,
                authorization_level=phase.default_authorization_level,
                violated_constraints=hard_violations,
                rejection_reason=_format_structured_reason(hard_violations),
                rejection_detail=_format_human_reason(hard_violations, proposal, world_state),
                evaluated_at=current_time,
            )

        # Phase-conditional escalation
        auth_level = phase.default_authorization_level
        if phase.escalation_on_deviation and prior_phase_results:
            for prior in prior_phase_results:
                if prior.verdict == GovernanceVerdict.APPROVED:
                    if auth_level.value < AuthorizationLevel.L2.value:
                        auth_level = AuthorizationLevel.L2

        return GovernancePhaseResult(
            phase_name=phase.phase_name,
            verdict=GovernanceVerdict.APPROVED,
            authorization_level=auth_level,
            violated_constraints=[],
            evaluated_at=current_time,
        )

    def evaluate_multi_phase(
        self,
        phases: List[PhaseConfig],
        proposal: StrategyProposal,
        intents: List[IntentVector],
        world_state: WorldModel,
        current_time: Optional[datetime] = None,
    ) -> List[GovernancePhaseResult]:
        """
        Evaluate all phases in a multi-phase authorization lifecycle.

        Returns results for each phase. All phases are linked in
        Decision Lineage as one governed process.
        """
        results = []
        for phase in phases:
            result = self.evaluate_phase(
                phase=phase,
                proposal=proposal,
                intents=intents,
                world_state=world_state,
                current_time=current_time,
                prior_phase_results=results,
            )
            results.append(result)
            if phase.required and result.verdict != GovernanceVerdict.APPROVED:
                break
        return results

    # --- Core Evaluation ---

    @property
    def public_key_hex(self) -> str:
        """The kernel's public key — the Execution Fabric verifies decisions with it."""
        return self._public_key_hex

    def _sign_decision(self, decision: GovernanceDecision) -> GovernanceDecision:
        """Sign a decision with the kernel's private key (Fix 2)."""
        decision.kernel_public_key_id = self._kernel_key_id
        decision.decision_signature = sign(
            self._signing_key_hex, canonical_decision_payload(decision)
        )
        return decision

    def evaluate_proposal(
        self,
        proposal: StrategyProposal,
        intents: List[IntentVector],
        world_state: WorldModel,
        current_time: Optional[datetime] = None,
        action_type_id: Optional[str] = None,
    ) -> GovernanceDecision:
        """Evaluate a proposal and cryptographically sign the resulting decision.

        The signature lets the Execution Fabric confirm the decision came from
        this kernel and was not forged or altered downstream.
        """
        decision = self._evaluate(
            proposal, intents, world_state, current_time, action_type_id
        )
        return self._sign_decision(decision)

    def _evaluate(
        self,
        proposal: StrategyProposal,
        intents: List[IntentVector],
        world_state: WorldModel,
        current_time: Optional[datetime] = None,
        action_type_id: Optional[str] = None,
    ) -> GovernanceDecision:
        """
        Evaluate a strategy proposal against all active policies.

        Returns APPROVED, REJECTED (with reason), or ESCALATE.
        Includes Structured Uncertainty declaration on every decision.
        """
        if current_time is None:
            current_time = datetime.utcnow()

        decision_id = f"gov_{uuid4().hex[:12]}"

        # 0. Action Type Registry gate.
        #    - A declared-but-unregistered action type is always rejected.
        #    - Under strict action typing (fail-closed, SA-2 / Fix 1) a missing
        #      action_type_id is also rejected, so the gate cannot be bypassed by
        #      simply omitting the field.
        if action_type_id is not None:
            if not self.validate_action_type(action_type_id):
                return GovernanceDecision(
                    id=decision_id,
                    proposal_id=proposal.id,
                    verdict=GovernanceVerdict.REJECTED,
                    violated_constraints=[],
                    rejection_reason="unregistered_action_type",
                    rejection_detail=(
                        f"Action type '{action_type_id}' is not registered in the "
                        f"Action Type Registry. The system cannot take actions "
                        f"outside its registered governance configuration."
                    ),
                    action_type_id=action_type_id,
                    temporal_context=_get_temporal_snapshot(current_time),
                    policy_snapshot={},
                    evaluated_at=current_time,
                )
        elif self._strict_action_typing:
            return GovernanceDecision(
                id=decision_id,
                proposal_id=proposal.id,
                verdict=GovernanceVerdict.REJECTED,
                violated_constraints=[],
                rejection_reason="action_type_required",
                rejection_detail=(
                    "Strict action typing is enabled but this proposal declared "
                    "no action_type_id. Every action must declare a registered "
                    "action type; the Action Type Registry gate cannot be "
                    "bypassed by omitting the field."
                ),
                action_type_id=None,
                temporal_context=_get_temporal_snapshot(current_time),
                policy_snapshot={},
                evaluated_at=current_time,
            )

        # 1. Resolve active constraints based on temporal context
        active_constraints = self._get_active_constraints(intents, current_time)

        # 2. Check all hard constraints (any violation = reject). Fail-closed:
        #    a HARD constraint with no registered evaluator cannot be certified
        #    satisfied, so _check_constraint_violation treats it as a violation.
        hard_violations = []
        for constraint in active_constraints:
            if constraint.type == ConstraintType.HARD:
                if _check_constraint_violation(proposal, constraint, world_state):
                    hard_violations.append(constraint.name)

        # 3. Check soft constraints (violations logged but not blocking). A SOFT
        #    constraint is a preference; one with no registered evaluator cannot
        #    be scored, so it is skipped rather than flagged as violated.
        soft_violations = []
        for constraint in active_constraints:
            if constraint.type == ConstraintType.SOFT:
                if _constraint_has_evaluator(constraint) and _check_constraint_violation(
                    proposal, constraint, world_state
                ):
                    soft_violations.append(constraint.name)

        # Build uncertainty declaration for every decision
        uncertainty = _build_uncertainty_declaration(
            proposal, world_state, active_constraints,
            hard_violations, soft_violations,
        )

        if hard_violations:
            return GovernanceDecision(
                id=decision_id,
                proposal_id=proposal.id,
                verdict=GovernanceVerdict.REJECTED,
                violated_constraints=hard_violations,
                rejection_reason=_format_structured_reason(hard_violations),
                rejection_detail=_format_human_reason(
                    hard_violations, proposal, world_state
                ),
                temporal_context=_get_temporal_snapshot(current_time),
                policy_snapshot=_serialize_active_policies(active_constraints),
                evaluated_at=current_time,
                uncertainty=uncertainty,
                action_type_id=action_type_id,
            )

        # 4. Determine authorization level based on risk (L0-L4)
        max_risk = max(
            (a.risk_score for a in proposal.actions), default=1
        )
        auth_level = _determine_auth_level(max_risk)
        tier = _determine_auth_tier(max_risk)  # Legacy compat

        # Override with action type's default if specified and higher
        if action_type_id:
            action_spec = self._action_type_registry.get(action_type_id)
            if action_spec:
                auth_level = _max_auth(
                    auth_level, action_spec.default_authorization_level
                )

        # 4a. Dynamic Risk Escalation — check for runtime behavioral signals
        escalation_triggered = False
        escalation_reason = None
        original_auth_level_str = None
        escalated_auth_level_str = None
        escalation_evidence = None

        escalation_trigger = self._dynamic_risk_engine.evaluate(
            action_type=action_type_id or proposal.actions[0].action_type if proposal.actions else "unknown",
            action_context={
                "current_auth_level": auth_level.value,
                "target": proposal.actions[0].target if proposal.actions else "",
                "proposal_id": proposal.id,
            },
            current_auth_level=auth_level.value,
        )

        if escalation_trigger is not None:
            escalation_triggered = True
            escalation_reason = escalation_trigger.description
            original_auth_level_str = escalation_trigger.original_level
            escalated_auth_level_str = escalation_trigger.escalated_level
            escalation_evidence = escalation_trigger.evidence

            # Apply escalation: override auth_level if escalated is higher
            level_order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
            escalated_idx = level_order.get(escalation_trigger.escalated_level, 0)
            current_idx = level_order.get(auth_level.value, 0)
            if escalated_idx > current_idx:
                auth_level = AuthorizationLevel(escalation_trigger.escalated_level)
                # Update legacy tier if escalation pushes to escalate range
                if escalated_idx >= 4:
                    tier = "escalate"
                elif escalated_idx >= 3:
                    tier = "require_approval"
                elif escalated_idx >= 2:
                    tier = "require_approval"

        # If risk exceeds system authority (L4), escalate
        if tier == "escalate":
            return GovernanceDecision(
                id=decision_id,
                proposal_id=proposal.id,
                verdict=GovernanceVerdict.ESCALATE,
                violated_constraints=[],
                rejection_reason="risk_exceeds_system_authority",
                rejection_detail=(
                    f"Maximum risk score {max_risk} exceeds system authority threshold."
                ),
                authorization_level=auth_level,
                authorization_tier=tier,
                temporal_context=_get_temporal_snapshot(current_time),
                policy_snapshot=_serialize_active_policies(active_constraints),
                evaluated_at=current_time,
                uncertainty=uncertainty,
                action_type_id=action_type_id,
                escalation_triggered=escalation_triggered,
                escalation_reason=escalation_reason,
                original_authorization_level=original_auth_level_str,
                escalated_authorization_level=escalated_auth_level_str,
                escalation_evidence=escalation_evidence,
            )

        # 5. Check intent conflicts
        conflicts = _detect_intent_conflicts(proposal, intents)
        if conflicts:
            serving = next(
                (i for i in intents if i.id == proposal.intent_id), None
            )
            if serving:
                all_conflicting = [serving] + conflicts
                resolution = resolve_intent_conflict(all_conflicting, proposal)
                if resolution["primary_intent"] != serving.id:
                    return GovernanceDecision(
                        id=decision_id,
                        proposal_id=proposal.id,
                        verdict=GovernanceVerdict.ESCALATE,
                        violated_constraints=[],
                        rejection_reason="unresolvable_intent_conflict",
                        rejection_detail=(
                            f"Intent conflict between {[i.id for i in all_conflicting]}. "
                            f"Serving intent {serving.id} is not highest priority."
                        ),
                        authorization_level=auth_level,
                        authorization_tier=tier,
                        temporal_context=_get_temporal_snapshot(current_time),
                        policy_snapshot=_serialize_active_policies(active_constraints),
                        evaluated_at=current_time,
                        uncertainty=uncertainty,
                        action_type_id=action_type_id,
                    )

        # 6. Multi-Phase Authorization: if action type has phases, evaluate them
        phase_results = []
        if action_type_id:
            action_spec = self._action_type_registry.get(action_type_id)
            if action_spec and action_spec.phase_config:
                phase_results = self.evaluate_multi_phase(
                    phases=action_spec.phase_config,
                    proposal=proposal,
                    intents=intents,
                    world_state=world_state,
                    current_time=current_time,
                )
                for pr in phase_results:
                    if pr.verdict != GovernanceVerdict.APPROVED:
                        return GovernanceDecision(
                            id=decision_id,
                            proposal_id=proposal.id,
                            verdict=pr.verdict,
                            violated_constraints=pr.violated_constraints,
                            rejection_reason=pr.rejection_reason,
                            rejection_detail=pr.rejection_detail,
                            authorization_level=auth_level,
                            authorization_tier=tier,
                            temporal_context=_get_temporal_snapshot(current_time),
                            policy_snapshot=_serialize_active_policies(active_constraints),
                            evaluated_at=current_time,
                            uncertainty=uncertainty,
                            action_type_id=action_type_id,
                            phase_results=phase_results,
                        )

        # 7. Approved — record escalation details if triggered
        return GovernanceDecision(
            id=decision_id,
            proposal_id=proposal.id,
            verdict=GovernanceVerdict.APPROVED,
            violated_constraints=soft_violations,
            authorization_level=auth_level,
            authorization_tier=tier,
            temporal_context=_get_temporal_snapshot(current_time),
            policy_snapshot=_serialize_active_policies(active_constraints),
            evaluated_at=current_time,
            uncertainty=uncertainty,
            action_type_id=action_type_id,
            phase_results=phase_results,
            escalation_triggered=escalation_triggered,
            escalation_reason=escalation_reason,
            original_authorization_level=original_auth_level_str,
            escalated_authorization_level=escalated_auth_level_str,
            escalation_evidence=escalation_evidence,
        )

    def _get_active_constraints(
        self,
        intents: List[IntentVector],
        current_time: datetime,
    ) -> List[Constraint]:
        """Collect all active constraints: the Tier-1 floor (always active) plus
        active intents' constraints filtered by temporal authority."""
        # Tier-1 regulatory floor is always active and cannot be suspended,
        # narrowed, or scheduled off by any intent or lower-tier configuration.
        active: List[Constraint] = list(self._tier1_floor)
        for intent in intents:
            if not intent.active:
                continue
            for constraint in intent.hard_constraints + intent.soft_constraints:
                if constraint.activation.always or _is_constraint_active(
                    constraint, current_time
                ):
                    active.append(constraint)
        return active
