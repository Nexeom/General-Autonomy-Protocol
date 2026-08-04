"""Resilience tests for the Reconciler Loop — GAP's autonomous heartbeat.

The heartbeat is what keeps governance running at all. These tests cover what it
does when something goes wrong: one malformed entity, a failing cycle, a target
that can never be resolved, a hold that must still reach a human, forged contact
evidence, and hostile configuration.
"""

import asyncio
from datetime import timedelta

import pytest
from pydantic import ValidationError

from gap_kernel._time import utcnow
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.learning.engine import LearningEngine
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models import world as world_models
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.reconciler import ReconcilerConfig
from gap_kernel.models.world import EntityState
from gap_kernel.reconciler.loop import DriftWatcher, ReconcilerLoop
from gap_kernel.strategy.cga_loop import CGAResult
from gap_kernel.world_model.store import WorldModelStore


def _sla_intent() -> IntentVector:
    return IntentVector(
        id="lead_response_sla",
        objective="Respond to high-value leads within 10 minutes",
        priority=80,
        hard_constraints=[
            Constraint(
                name="gdpr_consent_required",
                type=ConstraintType.HARD,
                description="Verify GDPR consent before EU outreach",
            ),
        ],
        soft_constraints=[],
        created_by="test",
        created_at=utcnow(),
    )


def _drifting_entity(entity_id: str, **extra_properties) -> EntityState:
    """A lead 8 minutes into a 10-minute SLA — in active drift."""
    properties = {
        "name": "Lead",
        "value": 50000,
        "geo": "US",
        "gdpr_consent": True,
        "local_hour": 14,
        "created_at": (utcnow() - timedelta(minutes=8)).isoformat(),
    }
    properties.update(extra_properties)
    return EntityState(
        entity_type="lead",
        entity_id=entity_id,
        properties=properties,
        last_updated=utcnow(),
        source="crm",
        obligations=["lead_response_sla"],
    )


def _reconciler(world_store, *, monitor=None, block=False, config=None) -> ReconcilerLoop:
    governance = GovernanceKernel()
    fabric = ExecutionFabric(
        world_store.model, kernel_public_key_hex=governance.public_key_hex
    )
    return ReconcilerLoop(
        world_store=world_store,
        governance_kernel=governance,
        execution_fabric=fabric,
        lineage_store=LineageStore(db_path=":memory:"),
        learning_engine=LearningEngine(),
        config=config if config is not None else ReconcilerConfig(cooldown_seconds=0),
        integrity_monitor=monitor,
        block_on_integrity=block,
    )


class _ExplodingWatcher:
    """A drift watcher that fails on one entity and behaves normally otherwise."""

    def __init__(self, real, failing_entity_id: str):
        self._real = real
        self._failing = failing_entity_id

    def check(self, entity, intents, current_time=None):
        if entity.entity_id == self._failing:
            raise ValueError("malformed entity properties")
        return self._real.check(entity, intents, current_time)


class _StubCGALoop:
    """Stands in for the CGA loop with a fixed outcome, so the reconciler's own
    accounting can be tested independently of the strategy layer."""

    verdict = "awaiting_approval"

    def __init__(self, **kwargs):
        pass

    def run(self, intent, drift_event, world_state, intents=None, **kwargs):
        return CGAResult(
            intent=intent,
            drift_event=drift_event,
            proposals=[],
            decisions=[],
            accumulated_constraints=[],
            final_verdict=self.verdict,
            approved_proposal=None,
            execution_result=None,
            total_attempts=1,
            escalated=(self.verdict == "escalated"),
            integrity_signals=[],
        )


# --- C1: one failure must not end the heartbeat -----------------------------

def test_one_malformed_entity_does_not_end_the_cycle():
    """A single entity that blows up must degrade alone — the rest of the world
    is still governed in the same cycle."""
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_bad"))
    world_store.upsert_entity(_drifting_entity("lead_good"))
    reconciler = _reconciler(world_store)
    reconciler.register_intent(_sla_intent())
    reconciler._drift_watcher = _ExplodingWatcher(reconciler._drift_watcher, "lead_bad")

    results = reconciler.reconcile_once()

    assert any(
        r.get("entity_id") == "lead_bad" and r.get("verdict") == "degraded"
        for r in results
    )
    assert any(r.get("drift", {}).get("entity_id") == "lead_good" for r in results)
    assert reconciler.lineage_store.count() >= 1


def test_a_contained_entity_failure_is_recorded_not_swallowed():
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_bad"))
    reconciler = _reconciler(world_store)
    reconciler.register_intent(_sla_intent())
    reconciler._drift_watcher = _ExplodingWatcher(reconciler._drift_watcher, "lead_bad")

    reconciler.reconcile_once()

    failures = [
        i for i in reconciler.incidents
        if i["kind"] == "entity_failure" and i["entity_id"] == "lead_bad"
    ]
    assert len(failures) == 1
    assert "malformed entity properties" in failures[0]["detail"]


def test_a_permanently_failing_entity_stops_being_reprocessed():
    """Containment is not enough on its own: an entity that fails every cycle
    must count as a failure so the breaker eventually stops re-queueing it."""
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_bad"))
    reconciler = _reconciler(world_store)
    reconciler.register_intent(_sla_intent())
    reconciler._drift_watcher = _ExplodingWatcher(reconciler._drift_watcher, "lead_bad")

    threshold = reconciler.config.circuit_breaker_threshold
    for _ in range(threshold + 3):
        reconciler.reconcile_once()

    assert reconciler._dampening["lead_bad"].circuit_broken is True
    entity_failures = [i for i in reconciler.incidents if i["kind"] == "entity_failure"]
    assert len(entity_failures) == threshold  # stopped failing after the trip


async def test_run_async_survives_a_failing_cycle():
    """One exception must not permanently kill the autonomous heartbeat."""
    world_store = WorldModelStore()
    reconciler = _reconciler(
        world_store,
        config=ReconcilerConfig(heartbeat_interval_seconds=1, cooldown_seconds=0),
    )
    stop = asyncio.Event()
    calls = []

    def _cycle(current_time=None):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("cycle blew up")
        stop.set()
        return []

    reconciler.reconcile_once = _cycle

    await asyncio.wait_for(reconciler.run_async(stop_event=stop), timeout=15)

    assert len(calls) >= 2                       # the heartbeat kept beating
    assert reconciler.status == "stopped"
    assert any(i["kind"] == "cycle_failure" for i in reconciler.incidents)


# --- C2: the circuit breaker must be reachable, visible and resettable ------

def test_awaiting_approval_counts_toward_the_circuit_breaker(monkeypatch):
    """An entity held pending human approval never progresses on its own; if it
    resets the failure counter the loop re-plans it forever, appending to the
    lineage chain every cycle."""
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_hold"))
    reconciler = _reconciler(world_store)
    reconciler.register_intent(_sla_intent())
    monkeypatch.setattr("gap_kernel.reconciler.loop.CGALoop", _StubCGALoop)

    threshold = reconciler.config.circuit_breaker_threshold
    for _ in range(threshold + 3):
        reconciler.reconcile_once()

    state = reconciler._dampening["lead_hold"]
    assert state.circuit_broken is True
    assert reconciler.lineage_store.count() == threshold      # not one per cycle
    queued = [e for e in reconciler._escalation_queue if e["entity_id"] == "lead_hold"]
    assert len(queued) == 1


def test_circuit_breaker_is_observable_and_resettable(monkeypatch):
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_hold"))
    reconciler = _reconciler(world_store)
    reconciler.register_intent(_sla_intent())
    monkeypatch.setattr("gap_kernel.reconciler.loop.CGALoop", _StubCGALoop)

    for _ in range(reconciler.config.circuit_breaker_threshold):
        reconciler.reconcile_once()

    assert reconciler.circuit_broken_entities == ["lead_hold"]
    assert any(
        i["kind"] == "circuit_breaker_tripped" and i["entity_id"] == "lead_hold"
        for i in reconciler.incidents
    )

    tripped_at = reconciler.reconcile_once()
    assert tripped_at == []                                   # no longer reconciled

    assert reconciler.reset_circuit_breaker("lead_hold") is True
    assert reconciler.circuit_broken_entities == []
    assert reconciler.reconcile_once()                        # reconciled again
    assert reconciler.reset_circuit_breaker("no_such_entity") is False


def test_a_kill_switch_halt_is_not_counted_as_an_entity_failure():
    """A human halt is a deliberate stop, not the entity failing — it must not
    burn the entity's failure budget and leave it circuit-broken on resume."""
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_halt"))
    reconciler = _reconciler(world_store)
    reconciler.register_intent(_sla_intent())
    reconciler.kill_switch.engage(reason="incident")

    for _ in range(reconciler.config.circuit_breaker_threshold + 2):
        reconciler.reconcile_once()

    assert reconciler.circuit_broken_entities == []
    reconciler.kill_switch.disengage()
    assert any(r["verdict"] != "halted" for r in reconciler.reconcile_once())


# --- C3: escalation dedupe must not swallow a different kind of hold --------

def test_integrity_hold_is_not_swallowed_by_an_open_escalation():
    """An entity with an open escalation can still be flagged by GIM. Deduping on
    entity alone drops the hold — the dead letter the queue exists to prevent."""
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_4821"))
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=1)
    reconciler = _reconciler(world_store, monitor=monitor, block=True)
    reconciler.register_intent(_sla_intent())
    reconciler._escalation_queue.append(
        {"id": "esc_prior", "entity_id": "lead_4821", "status": "pending"}
    )

    reconciler.reconcile_once()

    statuses = [e["status"] for e in reconciler.open_escalations]
    assert "pending" in statuses
    assert "integrity_hold" in statuses


def test_repeated_holds_of_the_same_kind_still_dedupe():
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_4821"))
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=1)
    reconciler = _reconciler(world_store, monitor=monitor, block=True)
    reconciler.register_intent(_sla_intent())

    for _ in range(3):
        reconciler.reconcile_once()

    holds = [e for e in reconciler._escalation_queue if e["status"] == "integrity_hold"]
    assert len(holds) == 1


# --- C4: contact evidence must not be forgeable -----------------------------

def test_an_arbitrary_property_write_cannot_forge_contact_evidence():
    """update_record can write arbitrary properties and no constraint evaluator
    inspects it, so one low-risk action must not be able to silence SLA drift
    detection for an entity forever."""
    world_store = WorldModelStore()
    world_store.upsert_entity(_drifting_entity("lead_forge"))
    watcher = DriftWatcher()
    intent = _sla_intent()
    assert watcher.check(world_store.get_entity("lead_forge"), [intent])

    world_store.update_from_execution(
        "lead_forge",
        {"last_contacted": utcnow().isoformat(), "notes": "arbitrary"},
    )

    entity = world_store.get_entity("lead_forge")
    assert "last_contacted" not in entity.properties
    assert entity.properties["notes"] == "arbitrary"   # ordinary writes still apply
    assert watcher.check(entity, [intent])             # drift is still detected


def test_the_sanctioned_writer_records_contact():
    entity = _drifting_entity("lead_ok")
    entity.record_contact("send_email")

    assert entity.properties["contact_method"] == "send_email"
    assert DriftWatcher().check(entity, [_sla_intent()]) == []


def test_unparseable_contact_evidence_does_not_suppress_drift():
    """Fail closed: a last_contacted value that cannot be evaluated is not proof
    the obligation was served."""
    entity = _drifting_entity("lead_garbage", last_contacted="whenever")

    assert DriftWatcher().check(entity, [_sla_intent()])


# --- C5: bound the config and the world model -------------------------------

@pytest.mark.parametrize("kwargs", [
    {"heartbeat_interval_seconds": 0},
    {"heartbeat_interval_seconds": -1},
    {"cooldown_seconds": -1},
    {"max_retry_budget": 0},
    {"max_retry_budget": 1_000_000},
    {"circuit_breaker_threshold": 0},
    {"drift_threshold": 1.5},
    {"heartbeat_intervl_seconds": 60},          # a typo must not be ignored
])
def test_reconciler_config_rejects_hostile_values(kwargs):
    with pytest.raises(ValidationError):
        ReconcilerConfig(**kwargs)


def test_reconciler_config_still_accepts_operational_values():
    config = ReconcilerConfig(cooldown_seconds=0, max_retry_budget=2)
    assert config.cooldown_seconds == 0
    assert config.max_retry_budget == 2


def test_reconciler_config_validates_assignment():
    config = ReconcilerConfig()
    with pytest.raises(ValidationError):
        config.heartbeat_interval_seconds = 0


def test_drift_events_are_bounded():
    """Every drift event is embedded in every lineage record via the world-state
    snapshot, so an unbounded drift log makes each audit record grow forever."""
    cap = world_models.MAX_DRIFT_EVENTS
    store = WorldModelStore()

    for i in range(cap + 50):
        store.record_drift_event({"n": i})

    events = store.model.drift_events
    assert len(events) == cap
    assert events[0]["n"] == 50                 # oldest dropped
    assert events[-1]["n"] == cap + 49          # newest kept
    assert len(store.get_state_snapshot()["drift_events"]) == cap
