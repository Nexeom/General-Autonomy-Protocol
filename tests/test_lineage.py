"""Tests for the Decision Lineage Store."""

from datetime import datetime

import pytest

from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.lineage import LineageRecord
from gap_kernel.models.strategy import PlannedAction, StrategyProposal


def _make_lineage_record(
    record_id: str = "lin_1",
    cycle_id: str = "cycle_1",
    intent_id: str = "intent_1",
    escalated: bool = False,
) -> LineageRecord:
    now = datetime.utcnow()
    intent = IntentVector(
        id=intent_id,
        objective="Test objective",
        priority=50,
        hard_constraints=[],
        soft_constraints=[],
        created_by="test",
        created_at=now,
    )
    proposal = StrategyProposal(
        id=f"prop_{record_id}",
        intent_id=intent_id,
        attempt_number=1,
        plan_description="Test plan",
        actions=[
            PlannedAction(
                action_type="test_action",
                target="target_1",
                parameters={},
                risk_score=2,
            )
        ],
        estimated_cost=0.5,
        rationale="Test rationale",
        generated_at=now,
    )
    decision = GovernanceDecision(
        id=f"dec_{record_id}",
        proposal_id=proposal.id,
        verdict=GovernanceVerdict.APPROVED if not escalated else GovernanceVerdict.ESCALATE,
        evaluated_at=now,
    )
    return LineageRecord(
        id=record_id,
        cycle_id=cycle_id,
        intent=intent,
        drift_detected="Test drift",
        drift_severity=5,
        world_state_snapshot={"entities": {}},
        proposals=[proposal],
        governance_decisions=[decision],
        total_attempts=1,
        escalated_to_human=escalated,
        execution_success=not escalated,
        resolved_at=now,
    )


class TestLineageStore:
    def setup_method(self):
        self.store = LineageStore(db_path=":memory:")

    def test_append_and_retrieve(self):
        record = _make_lineage_record()
        result = self.store.append(record)

        assert result.signature != ""
        assert result.prior_record_hash is None  # First record

        retrieved = self.store.get_by_id("lin_1")
        assert retrieved is not None
        assert retrieved.id == "lin_1"

    def test_chain_integrity(self):
        """Verify cryptographic chaining works correctly."""
        for i in range(10):
            record = _make_lineage_record(
                record_id=f"lin_{i}",
                cycle_id=f"cycle_{i}",
            )
            self.store.append(record)

        assert self.store.count() == 10
        assert self.store.verify_chain_integrity() is True

    def test_chain_integrity_100_records(self):
        """Verify chain integrity holds after 100+ cycles (validation criterion 3)."""
        for i in range(110):
            record = _make_lineage_record(
                record_id=f"lin_{i}",
                cycle_id=f"cycle_{i}",
            )
            self.store.append(record)

        assert self.store.count() == 110
        assert self.store.verify_chain_integrity() is True

    def test_query_by_intent(self):
        for i in range(5):
            record = _make_lineage_record(
                record_id=f"lin_{i}",
                cycle_id=f"cycle_{i}",
                intent_id="target_intent" if i % 2 == 0 else "other_intent",
            )
            self.store.append(record)

        results = self.store.query_by_intent("target_intent")
        assert len(results) == 3  # indices 0, 2, 4

    def test_query_escalations(self):
        for i in range(6):
            record = _make_lineage_record(
                record_id=f"lin_{i}",
                cycle_id=f"cycle_{i}",
                escalated=(i % 3 == 0),
            )
            self.store.append(record)

        results = self.store.query_escalations()
        assert len(results) == 2  # indices 0 and 3

    def test_query_by_entity(self):
        record = _make_lineage_record()
        self.store.append(record)

        results = self.store.query_by_entity("target_1")
        assert len(results) == 1

    def test_query_recent(self):
        for i in range(20):
            record = _make_lineage_record(
                record_id=f"lin_{i}",
                cycle_id=f"cycle_{i}",
            )
            self.store.append(record)

        results = self.store.query_recent(limit=5)
        assert len(results) == 5

    def test_hash_chaining(self):
        """Each record's prior_record_hash should match the previous record's signature."""
        records = []
        for i in range(5):
            record = _make_lineage_record(
                record_id=f"lin_{i}",
                cycle_id=f"cycle_{i}",
            )
            appended = self.store.append(record)
            records.append(appended)

        for i in range(1, len(records)):
            assert records[i].prior_record_hash == records[i - 1].signature

    def test_get_by_cycle(self):
        record = _make_lineage_record(cycle_id="specific_cycle")
        self.store.append(record)

        results = self.store.get_by_cycle("specific_cycle")
        assert len(results) == 1
        assert results[0].cycle_id == "specific_cycle"
