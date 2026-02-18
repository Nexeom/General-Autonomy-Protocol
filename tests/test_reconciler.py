"""Tests for the Reconciler Loop."""

from datetime import datetime, timedelta

import pytest

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.learning.engine import LearningEngine
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
)
from gap_kernel.models.reconciler import ReconcilerConfig
from gap_kernel.models.world import EntityState
from gap_kernel.reconciler.loop import DriftWatcher, ReconcilerLoop
from gap_kernel.world_model.store import WorldModelStore


def _make_sla_intent() -> IntentVector:
    return IntentVector(
        id="lead_response_sla",
        objective="Respond to high-value leads within 10 minutes",
        priority=80,
        hard_constraints=[
            Constraint(
                name="gdpr_consent_required",
                type=ConstraintType.HARD,
                description="Must verify GDPR consent before outreach to EU leads",
            ),
        ],
        soft_constraints=[],
        created_by="test",
        created_at=datetime.utcnow(),
    )


class TestDriftWatcher:
    def test_detect_sla_drift(self):
        """Drift should be detected when an entity approaches SLA breach."""
        watcher = DriftWatcher()
        intent = _make_sla_intent()

        # Entity created 8 minutes ago (70% of 10-minute SLA)
        created_at = datetime.utcnow() - timedelta(minutes=8)
        entity = EntityState(
            entity_type="lead",
            entity_id="lead_123",
            properties={
                "created_at": created_at.isoformat(),
                "value": 50000,
            },
            last_updated=datetime.utcnow(),
            source="crm",
            obligations=["lead_response_sla"],
        )

        events = watcher.check(entity, [intent])
        assert len(events) >= 1
        assert events[0].severity >= 8

    def test_no_drift_when_contacted(self):
        """No drift if entity has already been contacted."""
        watcher = DriftWatcher()
        intent = _make_sla_intent()

        entity = EntityState(
            entity_type="lead",
            entity_id="lead_123",
            properties={
                "created_at": (datetime.utcnow() - timedelta(minutes=8)).isoformat(),
                "last_contacted": datetime.utcnow().isoformat(),
            },
            last_updated=datetime.utcnow(),
            source="crm",
            obligations=["lead_response_sla"],
        )

        events = watcher.check(entity, [intent])
        assert len(events) == 0

    def test_no_drift_within_threshold(self):
        """No drift if entity is within acceptable SLA window."""
        watcher = DriftWatcher()
        intent = _make_sla_intent()

        # Only 2 minutes old — well within 10-minute SLA
        entity = EntityState(
            entity_type="lead",
            entity_id="lead_123",
            properties={
                "created_at": (datetime.utcnow() - timedelta(minutes=2)).isoformat(),
            },
            last_updated=datetime.utcnow(),
            source="crm",
            obligations=["lead_response_sla"],
        )

        events = watcher.check(entity, [intent])
        assert len(events) == 0


class TestReconcilerLoop:
    def setup_method(self):
        self.world_store = WorldModelStore()
        self.governance = GovernanceKernel()
        self.lineage_store = LineageStore(db_path=":memory:")
        self.learning = LearningEngine()
        self.execution = ExecutionFabric(self.world_store.model)
        self.config = ReconcilerConfig(
            cooldown_seconds=0,  # Disable cooldown for tests
        )
        self.reconciler = ReconcilerLoop(
            world_store=self.world_store,
            governance_kernel=self.governance,
            execution_fabric=self.execution,
            lineage_store=self.lineage_store,
            learning_engine=self.learning,
            config=self.config,
        )

    def test_full_reconciliation_cycle(self):
        """
        End-to-end: lead goes untouched → drift detected → CGA loop fires.
        Validation criterion: this must work.
        """
        intent = _make_sla_intent()
        self.reconciler.register_intent(intent)

        # Add an EU lead that's been waiting 8 minutes
        created_at = datetime.utcnow() - timedelta(minutes=8)
        entity = EntityState(
            entity_type="lead",
            entity_id="lead_4821",
            properties={
                "name": "EU Lead",
                "value": 50000,
                "geo": "EU",
                "gdpr_consent": False,
                "local_hour": 14,
                "created_at": created_at.isoformat(),
            },
            last_updated=datetime.utcnow(),
            source="crm",
            obligations=["lead_response_sla"],
        )
        self.world_store.upsert_entity(entity)

        # Trigger reconciliation
        results = self.reconciler.reconcile_once()

        assert len(results) >= 1
        result = results[0]
        assert result["verdict"] in ("approved", "escalated")
        assert result["attempts"] >= 1

        # Verify lineage was recorded
        assert self.lineage_store.count() >= 1

    def test_dampening_prevents_oscillation(self):
        """An entity should not be re-processed during cooldown."""
        self.config.cooldown_seconds = 3600  # 1 hour cooldown
        intent = _make_sla_intent()
        self.reconciler.register_intent(intent)

        created_at = datetime.utcnow() - timedelta(minutes=8)
        entity = EntityState(
            entity_type="lead",
            entity_id="lead_damp",
            properties={
                "created_at": created_at.isoformat(),
                "geo": "US",
                "gdpr_consent": True,
            },
            last_updated=datetime.utcnow(),
            source="crm",
            obligations=["lead_response_sla"],
        )
        self.world_store.upsert_entity(entity)

        # First reconciliation
        results1 = self.reconciler.reconcile_once()
        assert len(results1) >= 1

        # Second reconciliation — should be dampened
        results2 = self.reconciler.reconcile_once()
        assert len(results2) == 0  # Dampened

    def test_escalation_queue(self):
        """Escalated drift events should appear in the escalation queue."""
        intent = IntentVector(
            id="impossible_intent",
            objective="Respond within 10 minutes",
            priority=80,
            hard_constraints=[
                Constraint(
                    name="gdpr_consent_required",
                    type=ConstraintType.HARD,
                    description="Must verify GDPR consent",
                ),
            ],
            soft_constraints=[],
            created_by="test",
            created_at=datetime.utcnow(),
        )
        # Override config with low retry budget
        self.config.max_retry_budget = 2
        self.reconciler.config = self.config
        self.reconciler.register_intent(intent)

        created_at = datetime.utcnow() - timedelta(minutes=8)
        entity = EntityState(
            entity_type="lead",
            entity_id="lead_esc",
            properties={
                "created_at": created_at.isoformat(),
                "geo": "EU",
                "gdpr_consent": False,
                "local_hour": 14,
            },
            last_updated=datetime.utcnow(),
            source="crm",
            obligations=["impossible_intent"],
        )
        self.world_store.upsert_entity(entity)

        self.reconciler.reconcile_once()

        # Check escalation queue
        pending = self.reconciler.pending_escalations
        assert len(pending) >= 1

    def test_resolve_escalation(self):
        """Escalations should be resolvable by humans."""
        # First create an escalation
        self.test_escalation_queue()

        pending = self.reconciler.pending_escalations
        esc_id = pending[0]["id"]

        result = self.reconciler.resolve_escalation(
            esc_id, "Manual override: contact approved", "admin"
        )
        assert result is not None
        assert result["status"] == "resolved"
        assert result["resolved_by"] == "admin"

        # Should no longer be pending
        assert len(self.reconciler.pending_escalations) == 0
