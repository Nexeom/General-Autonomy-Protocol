"""Tests for the Governance Kernel."""

from datetime import datetime

import pytest

from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyActivation,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel


def _make_eu_lead_world(entity_id: str = "lead_4821", consent: bool = False) -> WorldModel:
    """Create a world model with an EU lead entity."""
    return WorldModel(
        entities={
            entity_id: EntityState(
                entity_type="lead",
                entity_id=entity_id,
                properties={
                    "name": "Test Lead",
                    "value": 50000,
                    "geo": "EU",
                    "gdpr_consent": consent,
                    "local_hour": 14,
                },
                last_updated=datetime.utcnow(),
                source="crm",
                obligations=["lead_response_sla"],
            )
        },
        last_reconciled=datetime.utcnow(),
    )


def _make_sla_intent() -> IntentVector:
    """Create the lead response SLA intent from the spec."""
    return IntentVector(
        id="lead_response_sla",
        objective="Respond to high-value leads within 10 minutes",
        priority=80,
        hard_constraints=[
            Constraint(
                name="gdpr_consent_required",
                type=ConstraintType.HARD,
                description="Must verify GDPR consent before any direct outreach to EU leads",
            ),
            Constraint(
                name="no_contact_outside_hours",
                type=ConstraintType.HARD,
                description="No automated outreach between 10PM-7AM lead local time",
                activation=PolicyActivation(
                    always=False,
                    schedule="0 22-23,0-6 * * *",
                ),
            ),
        ],
        soft_constraints=[
            Constraint(
                name="prefer_automation",
                type=ConstraintType.SOFT,
                description="Prefer automated responses over human routing when possible",
            ),
        ],
        created_by="test",
        created_at=datetime.utcnow(),
    )


class TestGovernanceKernel:
    def setup_method(self):
        self.kernel = GovernanceKernel()

    def test_approve_compliant_proposal(self):
        """A proposal that doesn't violate any constraints should be approved."""
        intent = IntentVector(
            id="simple_intent",
            objective="Process tasks",
            priority=50,
            hard_constraints=[],
            soft_constraints=[],
            created_by="test",
            created_at=datetime.utcnow(),
        )
        proposal = StrategyProposal(
            id="prop_1",
            intent_id="simple_intent",
            attempt_number=1,
            plan_description="Query CRM for status",
            actions=[
                PlannedAction(
                    action_type="query_crm",
                    target="lead_123",
                    parameters={},
                    risk_score=1,
                )
            ],
            estimated_cost=0.05,
            rationale="Low-risk query",
            generated_at=datetime.utcnow(),
        )
        world = WorldModel(entities={}, last_reconciled=datetime.utcnow())

        decision = self.kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        assert decision.authorization_tier == "auto_execute"

    def test_reject_gdpr_violation(self):
        """A proposal sending email to an EU lead without consent should be rejected."""
        intent = _make_sla_intent()
        world = _make_eu_lead_world(consent=False)

        proposal = StrategyProposal(
            id="prop_gdpr",
            intent_id="lead_response_sla",
            attempt_number=1,
            plan_description="Send email to EU lead",
            actions=[
                PlannedAction(
                    action_type="send_email",
                    target="lead_4821",
                    parameters={"template": "response"},
                    risk_score=3,
                )
            ],
            estimated_cost=0.10,
            rationale="Direct approach",
            generated_at=datetime.utcnow(),
        )

        decision = self.kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert decision.verdict == GovernanceVerdict.REJECTED
        assert "gdpr_consent_required" in decision.violated_constraints
        assert decision.rejection_reason is not None
        assert "gdpr" in decision.rejection_reason.lower()

    def test_approve_with_consent(self):
        """A proposal to an EU lead WITH consent should be approved."""
        intent = _make_sla_intent()
        world = _make_eu_lead_world(consent=True)

        proposal = StrategyProposal(
            id="prop_consent",
            intent_id="lead_response_sla",
            attempt_number=1,
            plan_description="Send email to consented EU lead",
            actions=[
                PlannedAction(
                    action_type="send_email",
                    target="lead_4821",
                    parameters={"template": "response"},
                    risk_score=3,
                )
            ],
            estimated_cost=0.10,
            rationale="Consent verified",
            generated_at=datetime.utcnow(),
        )

        decision = self.kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert decision.verdict == GovernanceVerdict.APPROVED

    def test_approve_human_handoff(self):
        """Routing to a human should not violate GDPR constraint."""
        intent = _make_sla_intent()
        world = _make_eu_lead_world(consent=False)

        proposal = StrategyProposal(
            id="prop_human",
            intent_id="lead_response_sla",
            attempt_number=3,
            plan_description="Route to human sales rep",
            actions=[
                PlannedAction(
                    action_type="route_to_human",
                    target="lead_4821",
                    parameters={
                        "queue": "sales_queue",
                        "context": {"reason": "GDPR compliance"},
                    },
                    risk_score=2,
                )
            ],
            estimated_cost=5.00,
            rationale="Compliant human handoff",
            generated_at=datetime.utcnow(),
        )

        decision = self.kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        assert decision.authorization_tier == "auto_execute"

    def test_escalate_high_risk(self):
        """A very high risk proposal should trigger escalation."""
        intent = IntentVector(
            id="simple",
            objective="Process",
            priority=50,
            hard_constraints=[],
            soft_constraints=[],
            created_by="test",
            created_at=datetime.utcnow(),
        )
        proposal = StrategyProposal(
            id="prop_risky",
            intent_id="simple",
            attempt_number=1,
            plan_description="Delete all records",
            actions=[
                PlannedAction(
                    action_type="delete_all",
                    target="database",
                    parameters={},
                    risk_score=10,
                    reversible=False,
                )
            ],
            estimated_cost=0.0,
            rationale="Extreme action",
            generated_at=datetime.utcnow(),
        )
        world = WorldModel(entities={}, last_reconciled=datetime.utcnow())

        decision = self.kernel.evaluate_proposal(
            proposal=proposal, intents=[intent], world_state=world,
        )
        assert decision.verdict == GovernanceVerdict.ESCALATE

    def test_authorization_tiers(self):
        """Test graduated authorization based on risk scores."""
        intent = IntentVector(
            id="test",
            objective="Test",
            priority=50,
            hard_constraints=[],
            soft_constraints=[],
            created_by="test",
            created_at=datetime.utcnow(),
        )
        world = WorldModel(entities={}, last_reconciled=datetime.utcnow())

        for risk, expected_tier in [
            (1, "auto_execute"),
            (3, "auto_execute"),
            (4, "notify_proceed"),
            (6, "notify_proceed"),
            (7, "require_approval"),
            (8, "require_approval"),
        ]:
            proposal = StrategyProposal(
                id=f"prop_risk_{risk}",
                intent_id="test",
                attempt_number=1,
                plan_description=f"Risk {risk} action",
                actions=[
                    PlannedAction(
                        action_type="test_action",
                        target="target",
                        parameters={},
                        risk_score=risk,
                    )
                ],
                estimated_cost=0.0,
                rationale="Test",
                generated_at=datetime.utcnow(),
            )
            decision = self.kernel.evaluate_proposal(
                proposal=proposal, intents=[intent], world_state=world,
            )
            assert decision.authorization_tier == expected_tier, (
                f"Risk {risk}: expected {expected_tier}, got {decision.authorization_tier}"
            )

    def test_rejection_reason_is_machine_readable(self):
        """Rejection reasons should be structured and parseable."""
        intent = _make_sla_intent()
        world = _make_eu_lead_world(consent=False)

        proposal = StrategyProposal(
            id="prop_mr",
            intent_id="lead_response_sla",
            attempt_number=1,
            plan_description="Send email",
            actions=[
                PlannedAction(
                    action_type="send_email",
                    target="lead_4821",
                    parameters={},
                    risk_score=3,
                )
            ],
            estimated_cost=0.10,
            rationale="Test",
            generated_at=datetime.utcnow(),
        )

        decision = self.kernel.evaluate_proposal(
            proposal=proposal, intents=[intent], world_state=world,
        )
        assert decision.verdict == GovernanceVerdict.REJECTED
        # Machine-readable: pipe-separated constraint names
        assert "|" in decision.rejection_reason or decision.rejection_reason == "gdpr_consent_required"
        # Human-readable: descriptive text
        assert len(decision.rejection_detail) > 10

    def test_policy_snapshot_included(self):
        """Every decision should include a snapshot of active policies."""
        intent = _make_sla_intent()
        world = _make_eu_lead_world()

        proposal = StrategyProposal(
            id="prop_snap",
            intent_id="lead_response_sla",
            attempt_number=1,
            plan_description="Route to human",
            actions=[
                PlannedAction(
                    action_type="route_to_human",
                    target="lead_4821",
                    parameters={},
                    risk_score=2,
                )
            ],
            estimated_cost=5.0,
            rationale="Test",
            generated_at=datetime.utcnow(),
        )

        decision = self.kernel.evaluate_proposal(
            proposal=proposal, intents=[intent], world_state=world,
        )
        assert "active_constraints" in decision.policy_snapshot
        assert decision.policy_snapshot["count"] >= 1
