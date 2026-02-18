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
"""

from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

from croniter import croniter

from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import StrategyProposal
from gap_kernel.models.world import WorldModel


def _is_constraint_active(constraint: Constraint, current_time: datetime) -> bool:
    """Determine if a constraint is active based on temporal authority."""
    activation = constraint.activation

    if activation.always:
        return True

    if activation.schedule:
        try:
            cron = croniter(activation.schedule, current_time)
            prev_fire = cron.get_prev(datetime)
            # If the cron expression fired within the last 60 seconds,
            # we consider this window active. For schedule-based constraints,
            # we check if the current time falls within the scheduled window.
            # The schedule defines when the constraint IS active.
            cron_check = croniter(activation.schedule, current_time)
            next_fire = cron_check.get_next(datetime)
            prev_fire = cron_check.get_prev(datetime)
            # For hour-range schedules like "0 22-23,0-6 * * *",
            # check if current hour matches the pattern
            if croniter.match(activation.schedule, current_time):
                return True
        except (ValueError, KeyError):
            # Invalid cron expression — treat as inactive (fail-safe)
            return False

    return False


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
    # Rule registry — maps constraint names to evaluation functions
    violation_checks: Dict[str, callable] = {
        "gdpr_consent_required": _check_gdpr_consent,
        "no_contact_outside_hours": _check_contact_hours,
        "cost_ceiling": _check_cost_ceiling,
    }

    check_fn = violation_checks.get(constraint.name)
    if check_fn:
        return check_fn(proposal, constraint, world_state)

    # For unknown constraints, perform generic keyword-based checks
    return _generic_constraint_check(proposal, constraint, world_state)


def _check_gdpr_consent(
    proposal: StrategyProposal,
    constraint: Constraint,
    world_state: WorldModel,
) -> bool:
    """Check if proposal involves contacting an entity without GDPR consent."""
    for action in proposal.actions:
        # Direct outreach actions require consent verification
        if action.action_type in ("send_email", "send_sms", "direct_call", "automated_outreach"):
            target_id = action.target
            entity = world_state.entities.get(target_id)
            if entity:
                props = entity.properties
                # Check if entity is in EU jurisdiction
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
                        return True  # Violation: EU entity without consent
            elif action.requires_consent:
                return True  # Action explicitly requires consent but no entity data
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
                        return True  # Violation: outside allowed hours
    return False


def _check_cost_ceiling(
    proposal: StrategyProposal,
    constraint: Constraint,
    world_state: WorldModel,
) -> bool:
    """Check if proposal's estimated cost exceeds the ceiling."""
    # Cost ceiling is extracted from the constraint description or intent
    # For now, parse a numeric value if present in the constraint description
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
    # Generic constraints are not violated by default.
    # This ensures unknown constraints don't block everything.
    return False


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
            # Find the target entities
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


def _determine_auth_tier(max_risk: int) -> str:
    """
    Graduated authorization model:
      risk 1-3:  auto_execute    — low risk, reversible, within policy
      risk 4-6:  notify_proceed  — medium risk, logged, human notified
      risk 7-8:  require_approval — high risk, human must approve before execution
      risk 9-10: escalate        — exceeds system authority
    """
    if max_risk <= 3:
        return "auto_execute"
    elif max_risk <= 6:
        return "notify_proceed"
    elif max_risk <= 8:
        return "require_approval"
    else:
        return "escalate"


def _detect_intent_conflicts(
    proposal: StrategyProposal,
    intents: List[IntentVector],
) -> Optional[List[IntentVector]]:
    """Detect if the proposal creates conflicts between intents."""
    # Find the intent this proposal serves
    serving_intent = None
    conflicting = []

    for intent in intents:
        if intent.id == proposal.intent_id:
            serving_intent = intent
            continue
        if not intent.active:
            continue

        # Check if any action in the proposal might violate constraints
        # from other intents
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


class GovernanceKernel:
    """
    The Governance Kernel — evaluates proposals against active policies.

    Immutable from below. Only human-declared intents define its behavior.
    """

    def evaluate_proposal(
        self,
        proposal: StrategyProposal,
        intents: List[IntentVector],
        world_state: WorldModel,
        current_time: Optional[datetime] = None,
    ) -> GovernanceDecision:
        """
        Evaluate a strategy proposal against all active policies.

        Returns APPROVED, REJECTED (with reason), or ESCALATE.
        """
        if current_time is None:
            current_time = datetime.utcnow()

        decision_id = f"gov_{uuid4().hex[:12]}"

        # 1. Resolve active constraints based on temporal context
        active_constraints = self._get_active_constraints(intents, current_time)

        # 2. Check all hard constraints (any violation = reject)
        hard_violations = []
        for constraint in active_constraints:
            if constraint.type == ConstraintType.HARD:
                if _check_constraint_violation(proposal, constraint, world_state):
                    hard_violations.append(constraint.name)

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
            )

        # 3. Check soft constraints (violations logged but not blocking)
        soft_violations = []
        for constraint in active_constraints:
            if constraint.type == ConstraintType.SOFT:
                if _check_constraint_violation(proposal, constraint, world_state):
                    soft_violations.append(constraint.name)

        # 4. Determine authorization tier based on risk
        max_risk = max(
            (a.risk_score for a in proposal.actions), default=1
        )
        tier = _determine_auth_tier(max_risk)

        # If risk exceeds system authority, escalate
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
                temporal_context=_get_temporal_snapshot(current_time),
                policy_snapshot=_serialize_active_policies(active_constraints),
                evaluated_at=current_time,
            )

        # 5. Check intent conflicts
        conflicts = _detect_intent_conflicts(proposal, intents)
        if conflicts:
            # Try to resolve by priority
            serving = next(
                (i for i in intents if i.id == proposal.intent_id), None
            )
            if serving:
                all_conflicting = [serving] + conflicts
                resolution = resolve_intent_conflict(all_conflicting, proposal)
                if resolution["primary_intent"] != serving.id:
                    # The proposal's intent is not the highest priority — escalate
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
                        temporal_context=_get_temporal_snapshot(current_time),
                        policy_snapshot=_serialize_active_policies(active_constraints),
                        evaluated_at=current_time,
                    )

        # 6. Approved
        return GovernanceDecision(
            id=decision_id,
            proposal_id=proposal.id,
            verdict=GovernanceVerdict.APPROVED,
            violated_constraints=soft_violations,
            authorization_tier=tier,
            temporal_context=_get_temporal_snapshot(current_time),
            policy_snapshot=_serialize_active_policies(active_constraints),
            evaluated_at=current_time,
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
