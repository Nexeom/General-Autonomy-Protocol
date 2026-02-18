"""Tests for core data models."""

from datetime import datetime

import pytest

from gap_kernel.models import (
    Constraint,
    ConstraintType,
    DampeningState,
    EntityState,
    ExecutionResult,
    GovernanceDecision,
    GovernanceVerdict,
    IntentVector,
    LineageRecord,
    OperationalHeuristic,
    PlannedAction,
    PolicyActivation,
    PolicyProposal,
    ReconcilerConfig,
    StrategyProposal,
    WorldModel,
)


class TestIntentVector:
    def test_create_basic_intent(self):
        intent = IntentVector(
            id="test_intent_1",
            objective="Respond to leads within 10 minutes",
            priority=80,
            hard_constraints=[
                Constraint(
                    name="gdpr_consent_required",
                    type=ConstraintType.HARD,
                    description="Must verify GDPR consent before outreach",
                )
            ],
            soft_constraints=[
                Constraint(
                    name="prefer_automation",
                    type=ConstraintType.SOFT,
                    description="Prefer automated responses",
                )
            ],
            created_by="test_user",
            created_at=datetime.utcnow(),
        )
        assert intent.id == "test_intent_1"
        assert intent.priority == 80
        assert len(intent.hard_constraints) == 1
        assert len(intent.soft_constraints) == 1
        assert intent.active is True

    def test_priority_bounds(self):
        with pytest.raises(Exception):
            IntentVector(
                id="bad",
                objective="test",
                priority=0,  # Below minimum
                hard_constraints=[],
                soft_constraints=[],
                created_by="test",
                created_at=datetime.utcnow(),
            )

        with pytest.raises(Exception):
            IntentVector(
                id="bad",
                objective="test",
                priority=101,  # Above maximum
                hard_constraints=[],
                soft_constraints=[],
                created_by="test",
                created_at=datetime.utcnow(),
            )

    def test_temporal_activation(self):
        activation = PolicyActivation(
            always=False,
            schedule="0 22-23,0-6 * * *",
        )
        assert activation.always is False
        assert activation.schedule is not None


class TestStrategyProposal:
    def test_create_proposal(self):
        proposal = StrategyProposal(
            id="prop_1",
            intent_id="intent_1",
            attempt_number=1,
            plan_description="Send email to lead",
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
        assert proposal.attempt_number == 1
        assert len(proposal.actions) == 1
        assert proposal.actions[0].risk_score == 3

    def test_risk_score_bounds(self):
        with pytest.raises(Exception):
            PlannedAction(
                action_type="test",
                target="test",
                parameters={},
                risk_score=0,  # Below minimum
            )
        with pytest.raises(Exception):
            PlannedAction(
                action_type="test",
                target="test",
                parameters={},
                risk_score=11,  # Above maximum
            )


class TestGovernanceDecision:
    def test_approved_decision(self):
        decision = GovernanceDecision(
            id="dec_1",
            proposal_id="prop_1",
            verdict=GovernanceVerdict.APPROVED,
            authorization_tier="auto_execute",
            evaluated_at=datetime.utcnow(),
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        assert decision.evaluator == "governance_kernel"

    def test_rejected_decision(self):
        decision = GovernanceDecision(
            id="dec_2",
            proposal_id="prop_2",
            verdict=GovernanceVerdict.REJECTED,
            violated_constraints=["gdpr_consent_required"],
            rejection_reason="gdpr_consent_required",
            rejection_detail="No consent on file",
            evaluated_at=datetime.utcnow(),
        )
        assert decision.verdict == GovernanceVerdict.REJECTED
        assert len(decision.violated_constraints) == 1


class TestWorldModel:
    def test_entity_state(self):
        entity = EntityState(
            entity_type="lead",
            entity_id="lead_123",
            properties={"name": "Test Lead", "value": 50000},
            last_updated=datetime.utcnow(),
            source="crm",
            obligations=["intent_1"],
        )
        assert entity.confidence == 1.0
        assert "intent_1" in entity.obligations

    def test_world_model(self):
        entity = EntityState(
            entity_type="lead",
            entity_id="lead_123",
            properties={},
            last_updated=datetime.utcnow(),
            source="test",
        )
        model = WorldModel(
            entities={"lead_123": entity},
            last_reconciled=datetime.utcnow(),
        )
        assert "lead_123" in model.entities


class TestLineageRecord:
    def test_create_lineage_record(self):
        now = datetime.utcnow()
        intent = IntentVector(
            id="intent_1",
            objective="Test",
            priority=50,
            hard_constraints=[],
            soft_constraints=[],
            created_by="test",
            created_at=now,
        )
        record = LineageRecord(
            id="lin_1",
            cycle_id="cycle_1",
            intent=intent,
            drift_detected="Test drift",
            drift_severity=5,
            world_state_snapshot={},
            proposals=[],
            governance_decisions=[],
            total_attempts=1,
        )
        assert record.escalated_to_human is False
        assert record.execution_success is False
        assert record.signature == ""


class TestReconcilerConfig:
    def test_defaults(self):
        config = ReconcilerConfig()
        assert config.heartbeat_interval_seconds == 60
        assert config.drift_threshold == 0.7
        assert config.max_retry_budget == 3
        assert config.cooldown_seconds == 300
        assert config.circuit_breaker_threshold == 5
