"""Tests for the Execution Fabric."""

from datetime import datetime

import pytest

from gap_kernel.execution.fabric import ExecutionError, ExecutionFabric
from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel


def _make_world() -> WorldModel:
    return WorldModel(
        entities={
            "lead_123": EntityState(
                entity_type="lead",
                entity_id="lead_123",
                properties={"name": "Test Lead"},
                last_updated=datetime.utcnow(),
                source="test",
            )
        },
        last_reconciled=datetime.utcnow(),
    )


def _make_approved_decision(proposal_id: str) -> GovernanceDecision:
    return GovernanceDecision(
        id="dec_1",
        proposal_id=proposal_id,
        verdict=GovernanceVerdict.APPROVED,
        authorization_tier="auto_execute",
        evaluated_at=datetime.utcnow(),
    )


def _make_rejected_decision(proposal_id: str) -> GovernanceDecision:
    return GovernanceDecision(
        id="dec_2",
        proposal_id=proposal_id,
        verdict=GovernanceVerdict.REJECTED,
        rejected_constraints=["test"],
        rejection_reason="test_violation",
        evaluated_at=datetime.utcnow(),
    )


class TestExecutionFabric:
    def test_execute_approved_proposal(self):
        world = _make_world()
        fabric = ExecutionFabric(world)

        proposal = StrategyProposal(
            id="prop_1",
            intent_id="intent_1",
            attempt_number=1,
            plan_description="Send email",
            actions=[
                PlannedAction(
                    action_type="send_email",
                    target="lead_123",
                    parameters={"template": "response"},
                    risk_score=3,
                )
            ],
            estimated_cost=0.10,
            rationale="Direct approach",
            generated_at=datetime.utcnow(),
        )

        decision = _make_approved_decision("prop_1")
        result = fabric.execute(proposal, decision)

        assert result.success is True
        assert len(result.actions_completed) == 1
        assert len(result.actions_failed) == 0
        assert result.proposal_id == "prop_1"

    def test_reject_unapproved_execution(self):
        """The fabric must never execute without governance approval."""
        world = _make_world()
        fabric = ExecutionFabric(world)

        proposal = StrategyProposal(
            id="prop_2",
            intent_id="intent_1",
            attempt_number=1,
            plan_description="Sneaky execution",
            actions=[
                PlannedAction(
                    action_type="send_email",
                    target="lead_123",
                    parameters={},
                    risk_score=3,
                )
            ],
            estimated_cost=0.10,
            rationale="Bypass attempt",
            generated_at=datetime.utcnow(),
        )

        decision = _make_rejected_decision("prop_2")

        with pytest.raises(ExecutionError):
            fabric.execute(proposal, decision)

    def test_execute_multiple_actions(self):
        world = _make_world()
        fabric = ExecutionFabric(world)

        proposal = StrategyProposal(
            id="prop_3",
            intent_id="intent_1",
            attempt_number=1,
            plan_description="Multi-step plan",
            actions=[
                PlannedAction(
                    action_type="query_crm",
                    target="lead_123",
                    parameters={},
                    risk_score=1,
                ),
                PlannedAction(
                    action_type="route_to_human",
                    target="lead_123",
                    parameters={"queue": "sales"},
                    risk_score=2,
                ),
            ],
            estimated_cost=5.05,
            rationale="Query then route",
            generated_at=datetime.utcnow(),
        )

        decision = _make_approved_decision("prop_3")
        result = fabric.execute(proposal, decision)

        assert result.success is True
        assert len(result.actions_completed) == 2

    def test_unknown_action_type_fails(self):
        world = _make_world()
        fabric = ExecutionFabric(world)

        proposal = StrategyProposal(
            id="prop_4",
            intent_id="intent_1",
            attempt_number=1,
            plan_description="Unknown action",
            actions=[
                PlannedAction(
                    action_type="launch_missiles",
                    target="everywhere",
                    parameters={},
                    risk_score=10,
                )
            ],
            estimated_cost=0.0,
            rationale="Bad idea",
            generated_at=datetime.utcnow(),
        )

        decision = _make_approved_decision("prop_4")
        result = fabric.execute(proposal, decision)

        assert result.success is False
        assert len(result.actions_failed) == 1

    def test_world_state_updated_after_execution(self):
        world = _make_world()
        fabric = ExecutionFabric(world)

        proposal = StrategyProposal(
            id="prop_5",
            intent_id="intent_1",
            attempt_number=1,
            plan_description="Contact lead",
            actions=[
                PlannedAction(
                    action_type="send_email",
                    target="lead_123",
                    parameters={},
                    risk_score=3,
                )
            ],
            estimated_cost=0.10,
            rationale="Direct contact",
            generated_at=datetime.utcnow(),
        )

        decision = _make_approved_decision("prop_5")
        result = fabric.execute(proposal, decision)

        # World state should be updated
        entity = world.entities["lead_123"]
        assert "last_contacted" in entity.properties
        assert len(result.world_state_changes) > 0
