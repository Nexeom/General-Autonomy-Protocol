"""Tests for the CGA Loop — the defining behavior of GAP."""

from datetime import datetime

import pytest

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyActivation,
)
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.strategy.cga_loop import CGALoop, RuleBasedStrategyGenerator


def _make_eu_lead_world(consent: bool = False) -> WorldModel:
    return WorldModel(
        entities={
            "lead_4821": EntityState(
                entity_type="lead",
                entity_id="lead_4821",
                properties={
                    "name": "Test Lead",
                    "value": 50000,
                    "geo": "EU",
                    "gdpr_consent": consent,
                    "local_hour": 14,
                    "created_at": (
                        datetime.utcnow().replace(microsecond=0).isoformat()
                    ),
                },
                last_updated=datetime.utcnow(),
                source="crm",
                obligations=["lead_response_sla"],
            )
        },
        last_reconciled=datetime.utcnow(),
    )


def _make_sla_intent() -> IntentVector:
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
        ],
        soft_constraints=[
            Constraint(
                name="prefer_automation",
                type=ConstraintType.SOFT,
                description="Prefer automated responses over human routing",
            ),
        ],
        created_by="test",
        created_at=datetime.utcnow(),
    )


def _make_cost_intent() -> IntentVector:
    return IntentVector(
        id="cost_optimization",
        objective="Keep per-action cost below $2",
        priority=40,
        hard_constraints=[],
        soft_constraints=[
            Constraint(
                name="use_lightweight_models",
                type=ConstraintType.SOFT,
                description="Prefer lightweight models for routine decisions",
            ),
        ],
        created_by="test",
        created_at=datetime.utcnow(),
    )


class TestCGALoop:
    """Tests for the Constraint-Guided Autonomy loop."""

    def test_cga_loop_gdpr_scenario(self):
        """
        Full 3-attempt CGA loop matching Section 8.2:
        1. Direct email → REJECTED (GDPR)
        2. Query CRM + email → REJECTED (no consent)
        3. Route to human → APPROVED
        """
        world = _make_eu_lead_world(consent=False)
        intent = _make_sla_intent()
        governance = GovernanceKernel()
        execution = ExecutionFabric(world)

        cga = CGALoop(
            governance_kernel=governance,
            execution_fabric=execution,
            max_attempts=3,
        )

        drift_event = {
            "entity_id": "lead_4821",
            "description": "Lead #4821 (EU, high-value) untouched for 8 minutes",
            "severity": 8,
            "sla_remaining_minutes": 2,
        }

        result = cga.run(
            intent=intent,
            drift_event=drift_event,
            world_state=world,
            intents=[intent],
        )

        # Verify CGA behavior
        assert result.final_verdict == "approved"
        assert result.total_attempts == 3
        assert not result.escalated

        # Verify rejection history
        assert len(result.decisions) == 3
        assert result.decisions[0].verdict == GovernanceVerdict.REJECTED
        assert result.decisions[1].verdict == GovernanceVerdict.REJECTED
        assert result.decisions[2].verdict == GovernanceVerdict.APPROVED

        # Verify GDPR was the rejection reason
        assert "gdpr_consent_required" in result.decisions[0].violated_constraints
        assert "gdpr_consent_required" in result.decisions[1].violated_constraints

        # Verify the approved proposal is a human handoff
        assert result.approved_proposal is not None
        approved_actions = result.approved_proposal.actions
        assert any(a.action_type == "route_to_human" for a in approved_actions)

        # Verify execution succeeded
        assert result.execution_result is not None
        assert result.execution_result.success is True

    def test_cga_loop_immediate_approval(self):
        """A compliant proposal should be approved on first attempt."""
        world = _make_eu_lead_world(consent=True)
        intent = _make_sla_intent()
        governance = GovernanceKernel()
        execution = ExecutionFabric(world)

        cga = CGALoop(
            governance_kernel=governance,
            execution_fabric=execution,
        )

        drift_event = {
            "entity_id": "lead_4821",
            "description": "Lead untouched",
            "severity": 5,
        }

        result = cga.run(intent=intent, drift_event=drift_event, world_state=world)

        assert result.final_verdict == "approved"
        assert result.total_attempts == 1
        assert not result.escalated

    def test_cga_loop_budget_exhaustion(self):
        """When all attempts are rejected, system should escalate."""
        # Create a world where nothing works - entity always violates
        world = WorldModel(
            entities={
                "lead_blocked": EntityState(
                    entity_type="lead",
                    entity_id="lead_blocked",
                    properties={
                        "geo": "EU",
                        "gdpr_consent": False,
                        "local_hour": 23,  # Outside hours too
                    },
                    last_updated=datetime.utcnow(),
                    source="test",
                    obligations=["lead_response_sla"],
                )
            },
            last_reconciled=datetime.utcnow(),
        )

        # Intent with both GDPR and hours constraints active
        intent = IntentVector(
            id="lead_response_sla",
            objective="Respond within 10 minutes",
            priority=80,
            hard_constraints=[
                Constraint(
                    name="gdpr_consent_required",
                    type=ConstraintType.HARD,
                    description="Must verify GDPR consent",
                ),
                Constraint(
                    name="no_contact_outside_hours",
                    type=ConstraintType.HARD,
                    description="No contact 10PM-7AM local time",
                ),
            ],
            soft_constraints=[],
            created_by="test",
            created_at=datetime.utcnow(),
        )

        governance = GovernanceKernel()
        execution = ExecutionFabric(world)

        # Use max_attempts=2 so the human handoff isn't tried
        # (human handoff is attempt 3 in the default rule generator)
        cga = CGALoop(
            governance_kernel=governance,
            execution_fabric=execution,
            max_attempts=2,
        )

        drift_event = {
            "entity_id": "lead_blocked",
            "description": "Lead blocked by multiple constraints",
            "severity": 8,
        }

        result = cga.run(intent=intent, drift_event=drift_event, world_state=world)

        assert result.final_verdict == "escalated"
        assert result.escalated is True
        assert result.total_attempts == 2
        assert result.approved_proposal is None

    def test_accumulated_constraints_feed_back(self):
        """Rejection reasons should accumulate and influence subsequent proposals."""
        world = _make_eu_lead_world(consent=False)
        intent = _make_sla_intent()
        governance = GovernanceKernel()
        execution = ExecutionFabric(world)

        cga = CGALoop(
            governance_kernel=governance,
            execution_fabric=execution,
        )

        drift_event = {
            "entity_id": "lead_4821",
            "description": "Test drift",
            "severity": 5,
        }

        result = cga.run(intent=intent, drift_event=drift_event, world_state=world)

        # The accumulated constraints should show the GDPR rejections
        assert len(result.accumulated_constraints) >= 1
        assert any(
            "gdpr" in c.get("constraint", "").lower()
            for c in result.accumulated_constraints
        )

        # Each subsequent proposal should be different from the first
        if len(result.proposals) > 1:
            first_actions = {a.action_type for a in result.proposals[0].actions}
            last_actions = {a.action_type for a in result.proposals[-1].actions}
            # The proposals should evolve (not be identical retries)
            assert result.proposals[0].id != result.proposals[-1].id

    def test_lineage_record_from_result(self):
        """CGA result should produce a complete lineage record."""
        world = _make_eu_lead_world(consent=False)
        intent = _make_sla_intent()
        governance = GovernanceKernel()
        execution = ExecutionFabric(world)

        cga = CGALoop(
            governance_kernel=governance,
            execution_fabric=execution,
        )

        drift_event = {
            "entity_id": "lead_4821",
            "description": "Lead untouched for 8 minutes",
            "severity": 8,
        }

        result = cga.run(intent=intent, drift_event=drift_event, world_state=world)

        lineage = result.build_lineage_record(
            cycle_id="test_cycle_1",
            world_state_snapshot=world.model_dump(mode="json"),
        )

        # Verify lineage completeness
        assert lineage.cycle_id == "test_cycle_1"
        assert lineage.intent.id == "lead_response_sla"
        assert lineage.drift_detected == "Lead untouched for 8 minutes"
        assert lineage.drift_severity == 8
        assert len(lineage.proposals) == result.total_attempts
        assert len(lineage.governance_decisions) == result.total_attempts
        assert lineage.total_attempts == result.total_attempts
        assert lineage.resolved_at is not None
