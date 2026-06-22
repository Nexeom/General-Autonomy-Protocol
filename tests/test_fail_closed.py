"""Phase A — fail-closed defaults (Fix 1 / SA-2).

These tests assert the kernel denies by default rather than silently passing:
  * a HARD constraint with no registered evaluator is treated as a violation;
  * a SOFT constraint with no evaluator is a preference the kernel cannot score
    (non-blocking, not flagged);
  * under strict action typing, a missing or unregistered ``action_type_id`` is
    rejected so the Action Type Registry gate cannot be bypassed.
"""

from datetime import datetime

from gap_kernel.governance.kernel import GovernanceKernel, _is_constraint_active
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyActivation,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel


def _low_risk_proposal(intent_id: str = "intent_1") -> StrategyProposal:
    return StrategyProposal(
        id="prop_1",
        intent_id=intent_id,
        attempt_number=1,
        plan_description="Low-risk internal query",
        actions=[
            PlannedAction(
                action_type="query_crm",
                target="lead_1",
                parameters={},
                risk_score=1,
            )
        ],
        estimated_cost=0.01,
        rationale="Routine",
        generated_at=datetime.utcnow(),
    )


def _intent(hard=None, soft=None) -> IntentVector:
    return IntentVector(
        id="intent_1",
        objective="Process tasks",
        priority=50,
        hard_constraints=hard or [],
        soft_constraints=soft or [],
        created_by="test",
        created_at=datetime.utcnow(),
    )


def _empty_world() -> WorldModel:
    return WorldModel(entities={}, last_reconciled=datetime.utcnow())


# --- Constraint fail-closed ------------------------------------------------

def test_unevaluable_hard_constraint_is_rejected():
    """A HARD constraint with no registered evaluator must fail closed."""
    kernel = GovernanceKernel()
    intent = _intent(hard=[
        Constraint(
            name="bespoke_policy_with_no_evaluator",
            type=ConstraintType.HARD,
            description="A custom rule the kernel has no concrete check for",
        )
    ])
    decision = kernel.evaluate_proposal(
        proposal=_low_risk_proposal(),
        intents=[intent],
        world_state=_empty_world(),
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "bespoke_policy_with_no_evaluator" in decision.violated_constraints


def test_unevaluable_soft_constraint_is_not_blocking():
    """A SOFT constraint with no evaluator is a preference; it must not block
    or be reported as a violation (the kernel simply cannot score it)."""
    kernel = GovernanceKernel()
    intent = _intent(soft=[
        Constraint(
            name="prefer_something_unscored",
            type=ConstraintType.SOFT,
            description="A preference with no concrete evaluator",
        )
    ])
    decision = kernel.evaluate_proposal(
        proposal=_low_risk_proposal(),
        intents=[intent],
        world_state=_empty_world(),
    )
    assert decision.verdict == GovernanceVerdict.APPROVED
    assert "prefer_something_unscored" not in decision.violated_constraints


# --- Temporal authority fail-closed ----------------------------------------

def test_malformed_schedule_fails_closed_active():
    """A constraint with a malformed cron schedule must be treated as active
    (still evaluated), not silently disabled."""
    constraint = Constraint(
        name="time_scoped_rule",
        type=ConstraintType.HARD,
        description="A time-scoped rule with a broken schedule",
        activation=PolicyActivation(always=False, schedule="not-a-valid-cron"),
    )
    assert _is_constraint_active(constraint, datetime.utcnow()) is True


def test_valid_schedule_outside_window_is_inactive():
    """A well-formed schedule that does not match the current time is inactive
    (the fail-closed change must not make every scheduled constraint always-on)."""
    # Fires only at 03:00; evaluate at a fixed non-matching minute.
    constraint = Constraint(
        name="nightly_rule",
        type=ConstraintType.HARD,
        description="Active only at 03:00",
        activation=PolicyActivation(always=False, schedule="0 3 * * *"),
    )
    noon = datetime(2026, 6, 22, 12, 0, 0)
    assert _is_constraint_active(constraint, noon) is False


# --- Strict action typing --------------------------------------------------

def test_strict_rejects_missing_action_type():
    kernel = GovernanceKernel(strict_action_typing=True)
    decision = kernel.evaluate_proposal(
        proposal=_low_risk_proposal(),
        intents=[_intent()],
        world_state=_empty_world(),
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert decision.rejection_reason == "action_type_required"


def test_strict_rejects_unregistered_action_type():
    kernel = GovernanceKernel(strict_action_typing=True)
    decision = kernel.evaluate_proposal(
        proposal=_low_risk_proposal(),
        intents=[_intent()],
        world_state=_empty_world(),
        action_type_id="not_a_real_type",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert decision.rejection_reason == "unregistered_action_type"


def test_strict_allows_registered_action_type():
    kernel = GovernanceKernel(strict_action_typing=True)
    decision = kernel.evaluate_proposal(
        proposal=_low_risk_proposal(),
        intents=[_intent()],
        world_state=_empty_world(),
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.APPROVED


def test_non_strict_allows_missing_action_type_by_default():
    """Default (non-strict) deployments preserve the prior contract: a proposal
    without an action_type_id is still evaluated rather than rejected."""
    kernel = GovernanceKernel()  # strict_action_typing defaults False
    decision = kernel.evaluate_proposal(
        proposal=_low_risk_proposal(),
        intents=[_intent()],
        world_state=_empty_world(),
    )
    assert decision.verdict == GovernanceVerdict.APPROVED
