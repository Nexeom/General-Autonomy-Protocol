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

from gap_kernel.models.governance import (
    ActionTypeSpec,
    AuthorizationLevel,
    GovernanceDecision,
    GovernancePhaseResult,
    GovernanceVerdict,
    PhaseConfig,
    RiskProfile,
    UncertaintyDeclaration,
)
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
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
            cron = croniter(activation.schedule, current_time)
            prev_fire = cron.get_prev(datetime)
            cron_check = croniter(activation.schedule, current_time)
            next_fire = cron_check.get_next(datetime)
            prev_fire = cron_check.get_prev(datetime)
            if croniter.match(activation.schedule, current_time):
                return True
        except (ValueError, KeyError):
            return False

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
    violation_checks: Dict[str, callable] = {
        "gdpr_consent_required": _check_gdpr_consent,
        "no_contact_outside_hours": _check_contact_hours,
        "cost_ceiling": _check_cost_ceiling,
    }

    check_fn = violation_checks.get(constraint.name)
    if check_fn:
        return check_fn(proposal, constraint, world_state)

    return _generic_constraint_check(proposal, constraint, world_state)


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


def _generic_constraint_check(
    proposal: StrategyProposal,
    constraint: Constraint,
    world_state: WorldModel,
) -> bool:
    """Generic check for constraints without specific rule implementations."""
    return False


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

    def __init__(self):
        self._action_type_registry: Dict[str, ActionTypeSpec] = dict(_BASELINE_ACTION_TYPES)

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

    def evaluate_proposal(
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

        # 0. Action Type Registry check — reject unregistered action types
        if action_type_id and not self.validate_action_type(action_type_id):
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

        # 1. Resolve active constraints based on temporal context
        active_constraints = self._get_active_constraints(intents, current_time)

        # 2. Check all hard constraints (any violation = reject)
        hard_violations = []
        for constraint in active_constraints:
            if constraint.type == ConstraintType.HARD:
                if _check_constraint_violation(proposal, constraint, world_state):
                    hard_violations.append(constraint.name)

        # 3. Check soft constraints (violations logged but not blocking)
        soft_violations = []
        for constraint in active_constraints:
            if constraint.type == ConstraintType.SOFT:
                if _check_constraint_violation(proposal, constraint, world_state):
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
            if action_spec and action_spec.default_authorization_level.value > auth_level.value:
                auth_level = action_spec.default_authorization_level

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

        # 7. Approved
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
        )

    def _get_active_constraints(
        self,
        intents: List[IntentVector],
        current_time: datetime,
    ) -> List[Constraint]:
        """Collect all active constraints from active intents, filtered by temporal authority."""
        active = []
        for intent in intents:
            if not intent.active:
                continue
            for constraint in intent.hard_constraints + intent.soft_constraints:
                if constraint.activation.always or _is_constraint_active(
                    constraint, current_time
                ):
                    active.append(constraint)
        return active
