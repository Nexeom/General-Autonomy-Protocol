"""Tests for the operational -> governance ActionTypeClassifier."""

from datetime import datetime

from gap_kernel.governance.action_classifier import ActionTypeClassifier
from gap_kernel.models.strategy import PlannedAction, StrategyProposal


def _proposal(action_types):
    return StrategyProposal(
        id="p", intent_id="i1", attempt_number=1, plan_description="p",
        actions=[
            PlannedAction(action_type=at, target="t", parameters={}, risk_score=1)
            for at in action_types
        ],
        estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
    )


def test_routine_actions_map_to_the_base_category():
    c = ActionTypeClassifier(base_action_type="drift_reconciliation")
    assert c.classify(_proposal(["query_crm", "send_email"])) == "drift_reconciliation"


def test_sensitive_action_escalates_to_more_restrictive_category():
    c = ActionTypeClassifier(base_action_type="drift_reconciliation")
    # A skill change among routine actions escalates the whole proposal.
    assert c.classify(_proposal(["query_crm", "modify_skill"])) == "skill_modification"
    assert c.classify(_proposal(["propose_policy"])) == "policy_proposal"


def test_most_restrictive_wins():
    c = ActionTypeClassifier(base_action_type="task_execution")
    assert c.classify(_proposal(["modify_skill", "propose_policy"])) == "policy_proposal"


def test_custom_overrides_and_base():
    c = ActionTypeClassifier(
        base_action_type="task_execution",
        overrides={"wire_funds": "financial_transaction"},
        rank={"task_execution": 0, "financial_transaction": 3},
    )
    assert c.classify(_proposal(["query_crm"])) == "task_execution"
    assert c.classify(_proposal(["wire_funds"])) == "financial_transaction"
