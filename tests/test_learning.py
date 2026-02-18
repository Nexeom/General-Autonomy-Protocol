"""Tests for the Learning Engine."""

from datetime import datetime

import pytest

from gap_kernel.learning.engine import LearningEngine
from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.lineage import LineageRecord
from gap_kernel.models.strategy import PlannedAction, StrategyProposal


def _make_lineage_with_rejections(
    constraint_name: str = "gdpr_consent_required",
    escalated: bool = False,
    success: bool = True,
    geo: str = "EU",
) -> LineageRecord:
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
    proposals = [
        StrategyProposal(
            id="prop_1",
            intent_id="intent_1",
            attempt_number=1,
            plan_description="Direct email",
            actions=[PlannedAction(
                action_type="send_email",
                target="lead_1",
                parameters={},
                risk_score=3,
            )],
            estimated_cost=0.1,
            rationale="First attempt",
            generated_at=now,
        ),
        StrategyProposal(
            id="prop_2",
            intent_id="intent_1",
            attempt_number=2,
            plan_description="Human handoff",
            actions=[PlannedAction(
                action_type="route_to_human",
                target="lead_1",
                parameters={},
                risk_score=2,
            )],
            estimated_cost=5.0,
            rationale="Adapted",
            generated_at=now,
        ),
    ]
    decisions = [
        GovernanceDecision(
            id="dec_1",
            proposal_id="prop_1",
            verdict=GovernanceVerdict.REJECTED,
            violated_constraints=[constraint_name],
            rejection_reason=constraint_name,
            rejection_detail="Constraint violated",
            evaluated_at=now,
        ),
        GovernanceDecision(
            id="dec_2",
            proposal_id="prop_2",
            verdict=GovernanceVerdict.APPROVED,
            evaluated_at=now,
        ),
    ]
    return LineageRecord(
        id=f"lin_{now.timestamp()}",
        cycle_id="cycle_1",
        intent=intent,
        drift_detected="Test drift",
        drift_severity=5,
        world_state_snapshot={
            "entities": {
                "lead_1": {
                    "properties": {"geo": geo, "gdpr_consent": False}
                }
            }
        },
        proposals=proposals,
        governance_decisions=decisions,
        total_attempts=2,
        escalated_to_human=escalated,
        execution_success=success,
        resolved_at=now,
    )


class TestLearningEngine:
    def setup_method(self):
        self.engine = LearningEngine()

    def test_learn_heuristic_from_rejection(self):
        """Operational learning should extract patterns from rejections."""
        record = _make_lineage_with_rejections()
        heuristic = self.engine.learn_from_lineage(record)

        assert heuristic is not None
        assert "consent" in heuristic.pattern.lower() or "geo" in heuristic.pattern.lower()
        assert heuristic.hit_count == 1

    def test_no_learning_from_single_attempt(self):
        """No heuristic should be learned from a single-attempt success."""
        now = datetime.utcnow()
        record = LineageRecord(
            id="lin_simple",
            cycle_id="cycle_simple",
            intent=IntentVector(
                id="i", objective="t", priority=50,
                hard_constraints=[], soft_constraints=[],
                created_by="t", created_at=now,
            ),
            drift_detected="Test",
            drift_severity=3,
            world_state_snapshot={},
            proposals=[],
            governance_decisions=[],
            total_attempts=1,
            resolved_at=now,
        )
        result = self.engine.learn_from_lineage(record)
        assert result is None

    def test_heuristic_hit_count_increments(self):
        """Repeated patterns should increment hit count."""
        record1 = _make_lineage_with_rejections()
        self.engine.learn_from_lineage(record1)

        record2 = _make_lineage_with_rejections()
        self.engine.learn_from_lineage(record2)

        heuristics = self.engine.get_all_heuristics()
        assert len(heuristics) >= 1
        # At least one heuristic should have hit_count > 1
        assert any(h.hit_count > 1 for h in heuristics)

    def test_propose_policy_change(self):
        """Normative learning should create reviewable proposals."""
        proposal = self.engine.propose_policy_change(
            proposed_change="Relax GDPR constraint for UK post-Brexit",
            rationale="UK is no longer EU",
            supporting_lineage_ids=["lin_1", "lin_2"],
            risk_assessment="May conflict with UK GDPR equivalent",
        )
        assert proposal.status == "pending_review"
        assert proposal.proposed_by == "strategy_layer"

    def test_approve_proposal(self):
        proposal = self.engine.propose_policy_change(
            proposed_change="Test change",
            rationale="Test",
            supporting_lineage_ids=[],
            risk_assessment="Low",
        )
        result = self.engine.approve_proposal(proposal.id, "admin")
        assert result is not None
        assert result.status == "approved"
        assert result.reviewed_by == "admin"

    def test_reject_proposal(self):
        proposal = self.engine.propose_policy_change(
            proposed_change="Risky change",
            rationale="Test",
            supporting_lineage_ids=[],
            risk_assessment="High",
        )
        result = self.engine.reject_proposal(proposal.id, "admin")
        assert result is not None
        assert result.status == "rejected"

    def test_iron_rule_proposals_never_auto_apply(self):
        """The Iron Rule: policy proposals require human approval."""
        proposal = self.engine.propose_policy_change(
            proposed_change="Remove all constraints",
            rationale="System thinks it knows better",
            supporting_lineage_ids=[],
            risk_assessment="Catastrophic",
        )
        # The proposal is created but NOT applied
        assert proposal.status == "pending_review"
        # Only pending — never auto-applied
        pending = self.engine.get_pending_proposals()
        assert len(pending) == 1

    def test_detect_policy_improvement_opportunity(self):
        """System should detect when constraints cause excessive escalations."""
        records = []
        for i in range(10):
            record = _make_lineage_with_rejections(
                escalated=(i < 6),  # 60% escalation rate
                success=(i >= 6),
            )
            record.id = f"lin_{i}"
            records.append(record)

        proposal = self.engine.detect_policy_improvement_opportunity(records)
        assert proposal is not None
        assert "gdpr_consent_required" in proposal.proposed_change
        assert proposal.status == "pending_review"
