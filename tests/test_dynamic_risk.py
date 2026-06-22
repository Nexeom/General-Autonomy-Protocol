"""
Tests for Dynamic Risk Escalation (Added 2026-02-23).

Test cases:
1. No escalation when behavior is within baseline → returns None
2. Volume anomaly detection triggers escalation
3. Scope expansion detection triggers escalation
4. Cascading action detection triggers escalation
5. Escalation is unidirectional (cannot decrease auth level)
6. Escalation config is not accessible from agent context
7. Escalation details recorded in Decision Record
8. External signal processing triggers appropriate escalation
"""

from datetime import datetime, timedelta

import pytest

from gap_kernel.governance.dynamic_risk import (
    DynamicRiskEngine,
    EscalationConfig,
    EscalationTrigger,
    EscalationTriggerType,
)
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceVerdict,
)
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyActivation,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel


def _make_config(**overrides) -> EscalationConfig:
    defaults = {
        "volume_threshold_multiplier": 10.0,
        "cascade_window_seconds": 300,
        "cascade_action_threshold": 5,
        "enabled": True,
    }
    defaults.update(overrides)
    return EscalationConfig(**defaults)


def _make_engine(**config_overrides) -> DynamicRiskEngine:
    return DynamicRiskEngine(_make_config(**config_overrides))


def _make_proposal(
    action_type="send_email",
    target="lead_001",
    risk_score=3,
) -> StrategyProposal:
    return StrategyProposal(
        id="prop_test_001",
        intent_id="intent_test",
        attempt_number=1,
        plan_description="Test proposal",
        actions=[
            PlannedAction(
                action_type=action_type,
                target=target,
                parameters={},
                risk_score=risk_score,
            )
        ],
        estimated_cost=0.10,
        rationale="Test rationale",
        generated_at=datetime.utcnow(),
    )


def _make_intent() -> IntentVector:
    return IntentVector(
        id="intent_test",
        objective="Test objective",
        priority=50,
        hard_constraints=[],
        soft_constraints=[],
        created_by="test",
        created_at=datetime.utcnow(),
    )


def _make_world_state() -> WorldModel:
    return WorldModel(
        entities={},
        last_reconciled=datetime.utcnow(),
    )


class TestDynamicRiskEscalation:
    """Test suite for the DynamicRiskEngine."""

    def test_no_escalation_within_baseline(self):
        """1. No escalation when behavior is within baseline."""
        engine = _make_engine()
        engine.set_baseline("send_email", count=100, targets={"lead_001"})

        result = engine.evaluate(
            action_type="send_email",
            action_context={
                "current_auth_level": "L0",
                "target": "lead_001",
            },
            current_auth_level="L0",
        )

        assert result is None

    def test_volume_anomaly_triggers_escalation(self):
        """2. Volume anomaly detection triggers escalation."""
        engine = _make_engine(
            volume_threshold_multiplier=2.0,
            cascade_window_seconds=600,
        )
        engine.set_baseline("send_email", count=5)

        # Record many actions to simulate volume anomaly
        for _ in range(15):
            engine.record_action("send_email", {"target": "lead_001"})

        result = engine.evaluate(
            action_type="send_email",
            action_context={
                "current_auth_level": "L0",
                "target": "lead_001",
            },
            current_auth_level="L0",
        )

        assert result is not None
        assert result.trigger_type == EscalationTriggerType.VOLUME_ANOMALY
        assert result.original_level == "L0"
        assert result.escalated_level > result.original_level

    def test_scope_expansion_triggers_escalation(self):
        """3. Scope expansion detection triggers escalation."""
        engine = _make_engine()
        engine.set_baseline(
            "send_email",
            targets={"lead_001", "lead_002", "lead_003"},
        )

        result = engine.evaluate(
            action_type="send_email",
            action_context={
                "current_auth_level": "L0",
                "target": "personnel_database",  # Outside baseline
            },
            current_auth_level="L0",
        )

        assert result is not None
        assert result.trigger_type == EscalationTriggerType.SCOPE_EXPANSION
        assert result.evidence["target"] == "personnel_database"

    def test_cascading_actions_triggers_escalation(self):
        """4. Cascading action detection triggers escalation."""
        engine = _make_engine(
            cascade_window_seconds=600,
            cascade_action_threshold=3,
        )

        # Record enough actions in the window to trigger cascade
        for i in range(5):
            engine.record_action("query_crm", {"target": f"lead_{i}"})

        result = engine.evaluate(
            action_type="query_crm",
            action_context={"current_auth_level": "L0"},
            current_auth_level="L0",
        )

        assert result is not None
        assert result.trigger_type == EscalationTriggerType.CASCADING_ACTIONS

    def test_escalation_is_unidirectional(self):
        """5. Escalation is unidirectional (cannot decrease auth level)."""
        # _escalate_level should only go UP
        assert DynamicRiskEngine._escalate_level("L0") == "L1"
        assert DynamicRiskEngine._escalate_level("L1") == "L2"
        assert DynamicRiskEngine._escalate_level("L2") == "L3"
        assert DynamicRiskEngine._escalate_level("L3") == "L4"
        assert DynamicRiskEngine._escalate_level("L4") == "L4"  # Cannot go higher

        # Multi-step escalation
        assert DynamicRiskEngine._escalate_level("L0", steps=2) == "L2"
        assert DynamicRiskEngine._escalate_level("L3", steps=3) == "L4"  # Capped

    def test_escalation_config_not_accessible_from_agent(self):
        """6. Escalation config is not accessible from agent context."""
        kernel = GovernanceKernel()

        # The _dynamic_risk_engine is private (prefixed with _)
        assert hasattr(kernel, "_dynamic_risk_engine")
        # The engine's config is not exposed through any public API
        assert not hasattr(kernel, "dynamic_risk_engine")
        assert not hasattr(kernel, "escalation_config")

    def test_escalation_details_recorded_in_decision(self):
        """7. Escalation details recorded in Decision Record."""
        config = EscalationConfig(
            cascade_action_threshold=3,
            cascade_window_seconds=600,
        )
        kernel = GovernanceKernel(escalation_config=config)

        # Record enough actions to trigger cascade escalation
        for i in range(5):
            kernel._dynamic_risk_engine.record_action(
                "send_email", {"target": f"lead_{i}"}
            )

        proposal = _make_proposal(risk_score=3)
        intent = _make_intent()
        world = _make_world_state()

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )

        # Escalation should have been triggered and recorded
        assert decision.escalation_triggered is True
        assert decision.escalation_reason is not None
        assert decision.original_authorization_level is not None
        assert decision.escalated_authorization_level is not None
        assert decision.escalation_evidence is not None

    def test_external_signal_triggers_escalation(self):
        """8. External signal processing triggers appropriate escalation."""
        engine = _make_engine(
            external_signal_sources=["soc_alerts", "compliance_feed"]
        )

        # Signal from known source with high severity
        result = engine.receive_external_signal(
            source="soc_alerts",
            signal={
                "severity": "high",
                "current_auth_level": "L0",
                "description": "Active security incident detected",
            },
        )

        assert result is not None
        assert result.trigger_type == EscalationTriggerType.EXTERNAL_SIGNAL
        assert result.escalated_level == "L2"  # High = +2 steps from L0
        assert result.confidence == 0.9

        # Signal from unknown source should be ignored
        result_unknown = engine.receive_external_signal(
            source="unknown_source",
            signal={"severity": "high", "current_auth_level": "L0"},
        )
        assert result_unknown is None

    def test_disabled_engine_returns_none(self):
        """Engine returns None when disabled."""
        engine = _make_engine(enabled=False)
        engine.set_baseline("send_email", count=1)

        # Even with anomaly conditions, disabled engine returns None
        for _ in range(100):
            engine.record_action("send_email", {"target": "lead_001"})

        result = engine.evaluate(
            action_type="send_email",
            action_context={"current_auth_level": "L0"},
            current_auth_level="L0",
        )

        assert result is None

    def test_external_signal_medium_severity(self):
        """Medium severity signal escalates by 1 step."""
        engine = _make_engine(external_signal_sources=["compliance_feed"])

        result = engine.receive_external_signal(
            source="compliance_feed",
            signal={
                "severity": "medium",
                "current_auth_level": "L1",
            },
        )

        assert result is not None
        assert result.escalated_level == "L2"  # +1 from L1

    def test_external_signal_low_severity_no_escalation(self):
        """Low severity signal does not trigger escalation."""
        engine = _make_engine(external_signal_sources=["soc_alerts"])

        result = engine.receive_external_signal(
            source="soc_alerts",
            signal={
                "severity": "low",
                "current_auth_level": "L0",
            },
        )

        assert result is None
