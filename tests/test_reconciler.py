"""Tests for the Reconciler Loop."""

from datetime import datetime, timedelta

import pytest

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
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
        self.execution = ExecutionFabric(
            self.world_store.model, kernel_public_key_hex=self.governance.public_key_hex
        )
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


# --- GIM wired onto the autonomous heartbeat --------------------------------

def _drifting_world_and_intent():
    """A world with one EU lead in active SLA drift + its intent (mirrors above)."""
    world_store = WorldModelStore()
    intent = _make_sla_intent()
    created_at = datetime.utcnow() - timedelta(minutes=8)
    world_store.upsert_entity(EntityState(
        entity_type="lead", entity_id="lead_4821",
        properties={"name": "EU Lead", "value": 50000, "geo": "US",
                    "gdpr_consent": True, "local_hour": 14,
                    "created_at": created_at.isoformat()},
        last_updated=datetime.utcnow(), source="crm", obligations=["lead_response_sla"],
    ))
    return world_store, intent


def _reconciler_with(world_store, *, monitor=None, block=False):
    governance = GovernanceKernel()
    fabric = ExecutionFabric(world_store.model,
                             kernel_public_key_hex=governance.public_key_hex)
    reconciler = ReconcilerLoop(
        world_store=world_store, governance_kernel=governance, execution_fabric=fabric,
        lineage_store=LineageStore(db_path=":memory:"), learning_engine=LearningEngine(),
        config=ReconcilerConfig(cooldown_seconds=0),
        integrity_monitor=monitor, block_on_integrity=block,
    )
    return reconciler


def test_reconciler_holds_and_escalates_on_integrity_signal():
    """GIM consequential on the autonomous heartbeat: an action GIM flags is HELD
    (not executed) and routed to the human queue as integrity_hold."""
    world_store, intent = _drifting_world_and_intent()
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=1)
    reconciler = _reconciler_with(world_store, monitor=monitor, block=True)
    reconciler.register_intent(intent)

    results = reconciler.reconcile_once()

    held = [r for r in results if r["verdict"] == "integrity_hold"]
    assert held, "expected at least one action to be held by GIM"
    assert all(r["execution_success"] is False for r in held)
    # No outreach happened — the lead was never contacted.
    assert "last_contacted" not in world_store.model.entities["lead_4821"].properties
    # The hold reached a human as an integrity_hold escalation (not silently dropped).
    assert any(e["status"] == "integrity_hold" for e in reconciler._escalation_queue)


def test_reconciler_integrity_hold_is_open_and_resolvable():
    """The held action must reach a human and be resolvable — not a dead letter.
    It is invisible to pending_escalations (pending-only) but surfaced by
    open_escalations and clearable via resolve_escalation."""
    world_store, intent = _drifting_world_and_intent()
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=1)
    reconciler = _reconciler_with(world_store, monitor=monitor, block=True)
    reconciler.register_intent(intent)

    reconciler.reconcile_once()

    assert reconciler.pending_escalations == []                  # not in pending-only
    opens = reconciler.open_escalations
    assert any(e["status"] == "integrity_hold" for e in opens)   # but is surfaced
    esc_id = next(e["id"] for e in opens if e["status"] == "integrity_hold")
    resolved = reconciler.resolve_escalation(esc_id, "reviewed; upheld", "admin")
    assert resolved is not None and resolved["status"] == "resolved"
    assert not any(e["status"] == "integrity_hold" for e in reconciler.open_escalations)


def test_held_target_does_not_pile_up_and_trips_the_breaker():
    """A permanently-held target must not re-queue an escalation every cycle, and
    the circuit breaker must trip (held cycles count as failures) so it stops
    being reprocessed forever."""
    world_store, intent = _drifting_world_and_intent()
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=1)
    reconciler = _reconciler_with(world_store, monitor=monitor, block=True)
    reconciler.register_intent(intent)

    for _ in range(7):
        reconciler.reconcile_once()

    holds = [e for e in reconciler._escalation_queue if e["entity_id"] == "lead_4821"]
    assert len(holds) == 1                                       # deduped, not 7
    assert reconciler._dampening["lead_4821"].circuit_broken is True  # breaker tripped


def _resolve_framed(reconciler, esc_id, *, preferred, order, framed, chosen):
    reconciler._escalation_queue.append({"id": esc_id, "entity_id": "e", "status": "pending"})
    reconciler.attach_escalation_framing(esc_id, preferred_option_id=preferred,
                                         option_order=order, favorably_framed=framed)
    reconciler.resolve_escalation(esc_id, "ok", "admin", chosen_option_id=chosen)


def test_resolving_framed_escalations_feeds_gim4_position_bias():
    """G-4: resolving framed escalations on the shipped path feeds GIM-4, so
    presentation bias (the human almost always picking the first-listed option)
    becomes a detectable signal."""
    world_store, _ = _drifting_world_and_intent()
    monitor = GovernanceIntegrityMonitor(escalation_min_samples=5)
    reconciler = _reconciler_with(world_store, monitor=monitor)
    for i in range(9):
        _resolve_framed(reconciler, f"esc_{i}", preferred="A", order=["A", "B"],
                        framed=["A"], chosen="A")          # always the first-listed
    _resolve_framed(reconciler, "esc_9", preferred="A", order=["A", "B"],
                    framed=["A"], chosen="B")              # one counter-example
    signal = reconciler.escalation_framing_bias()
    assert signal is not None and signal.signal_type == "GIM-4"
    assert signal.evidence["position_bias_rate"] == 0.9


def test_unbiased_framed_resolutions_do_not_flag():
    world_store, _ = _drifting_world_and_intent()
    monitor = GovernanceIntegrityMonitor(escalation_min_samples=5)
    reconciler = _reconciler_with(world_store, monitor=monitor)
    for i in range(10):
        # Human alternates; preferred/first chosen ~half the time.
        chosen = "A" if i % 2 == 0 else "B"
        _resolve_framed(reconciler, f"esc_{i}", preferred="A", order=["A", "B"],
                        framed=["A"], chosen=chosen)
    assert reconciler.escalation_framing_bias() is None


def test_framing_is_not_double_fed_on_reopen():
    """A re-opened-and-re-resolved escalation must not feed GIM-4 twice (one human
    decision is one observation)."""
    world_store, _ = _drifting_world_and_intent()
    monitor = GovernanceIntegrityMonitor(escalation_min_samples=1)
    reconciler = _reconciler_with(world_store, monitor=monitor)
    reconciler._escalation_queue.append({"id": "e0", "entity_id": "e", "status": "pending"})
    reconciler.attach_escalation_framing("e0", preferred_option_id="A",
                                         option_order=["A", "B"], favorably_framed=["A"])
    reconciler.resolve_escalation("e0", "ok", "admin", chosen_option_id="A")
    # Force a re-open and re-resolve.
    reconciler._escalation_queue[0]["status"] = "pending"
    reconciler.resolve_escalation("e0", "ok again", "admin", chosen_option_id="A")
    assert len(monitor._escalations) == 1                  # fed exactly once


def test_malformed_framing_is_not_counted():
    """A framing dict missing required keys is skipped (not counted as an all-False
    sample that would dilute the bias measurement)."""
    world_store, _ = _drifting_world_and_intent()
    monitor = GovernanceIntegrityMonitor(escalation_min_samples=1)
    reconciler = _reconciler_with(world_store, monitor=monitor)
    reconciler._escalation_queue.append(
        {"id": "e0", "entity_id": "e", "status": "pending", "framing": {}})  # malformed
    reconciler.resolve_escalation("e0", "ok", "admin", chosen_option_id="A")
    assert monitor._escalations == []


def test_resolution_without_framing_does_not_feed_gim4():
    world_store, _ = _drifting_world_and_intent()
    monitor = GovernanceIntegrityMonitor(escalation_min_samples=1)
    reconciler = _reconciler_with(world_store, monitor=monitor)
    reconciler._escalation_queue.append({"id": "e0", "entity_id": "e", "status": "pending"})
    reconciler.resolve_escalation("e0", "ok", "admin")     # no framing, no choice
    assert monitor._escalations == []                       # nothing observed
    assert reconciler.escalation_framing_bias() is None


def test_reconciler_without_monitor_executes_normally():
    """Open posture: no monitor wired => no integrity holds (backward compatible)."""
    world_store, intent = _drifting_world_and_intent()
    reconciler = _reconciler_with(world_store)  # no monitor, no block
    reconciler.register_intent(intent)

    results = reconciler.reconcile_once()

    assert results
    assert all(r["verdict"] != "integrity_hold" for r in results)


def test_reconciler_uses_one_persistent_shared_monitor():
    """One monitor watches the whole decision stream: a single reconcile cycle
    over two drifting entities feeds both their decisions into the SAME monitor
    instance, so erosion spanning multiple drift events is detectable."""
    world_store, intent = _drifting_world_and_intent()
    created_at = (datetime.utcnow() - timedelta(minutes=8)).isoformat()
    world_store.upsert_entity(EntityState(
        entity_type="lead", entity_id="lead_4822",
        properties={"name": "EU Lead 2", "value": 40000, "geo": "US",
                    "gdpr_consent": True, "local_hour": 14, "created_at": created_at},
        last_updated=datetime.utcnow(), source="crm", obligations=["lead_response_sla"],
    ))
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=99)  # won't hold
    reconciler = _reconciler_with(world_store, monitor=monitor, block=True)
    reconciler.register_intent(intent)

    reconciler.reconcile_once()

    assert reconciler._integrity_monitor is monitor          # one persistent monitor
    assert monitor._by_target.get("lead_4821")               # saw the first lead
    assert monitor._by_target.get("lead_4822")               # and the second
