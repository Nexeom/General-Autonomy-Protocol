"""Corrigibility — a human-engaged kill-switch halts execution and planning (SA-4).

These tests earn the claim that a halt *stops* the system: the Execution Fabric
refuses to dispatch any action while the switch is engaged (fail closed), and the
CGA loop refuses to plan or execute — it does NOT re-plan a path around a halt.
"""

from datetime import datetime

import pytest

from gap_kernel.execution.fabric import (
    ExecutionError,
    ExecutionFabric,
    KillSwitchEngaged,
)
from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.strategy.cga_loop import CGALoop


# --- fixtures ---------------------------------------------------------------

def _world():
    return WorldModel(
        entities={
            "lead_123": EntityState(
                entity_type="lead", entity_id="lead_123",
                properties={"name": "Test Lead"}, last_updated=datetime.utcnow(),
                source="test",
            )
        },
        last_reconciled=datetime.utcnow(),
    )


def _proposal(pid="prop_1", target="lead_123"):
    return StrategyProposal(
        id=pid, intent_id="intent_1", attempt_number=1, plan_description="Send email",
        actions=[PlannedAction(action_type="send_email", target=target,
                               parameters={"template": "response"}, risk_score=3)],
        estimated_cost=0.10, rationale="Direct approach", generated_at=datetime.utcnow(),
    )


def _approved(pid="prop_1"):
    return GovernanceDecision(
        id="dec_1", proposal_id=pid, verdict=GovernanceVerdict.APPROVED,
        authorization_tier="auto_execute", evaluated_at=datetime.utcnow(),
    )


def _intent():
    return IntentVector(id="i1", objective="o", priority=50, hard_constraints=[],
                        soft_constraints=[], created_by="t", created_at=datetime.utcnow())


class _CountingGen:
    """Strategy generator that records whether it was ever asked to plan."""

    def __init__(self, target="lead_123"):
        self.calls = 0
        self.target = target

    def generate(self, intent, world_state, drift_event, accumulated_constraints,
                 prior_proposals, attempt_number):
        self.calls += 1
        return StrategyProposal(
            id=f"prop_{attempt_number}", intent_id=intent.id, attempt_number=attempt_number,
            plan_description="op",
            actions=[PlannedAction(action_type="query_crm", target=self.target,
                                   parameters={}, risk_score=1)],
            estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
        )


# --- KillSwitch primitive ---------------------------------------------------

def test_killswitch_engage_disengage_and_scopes():
    ks = KillSwitch()
    assert ks.is_engaged() is False

    ks.engage(reason="incident")
    assert ks.is_engaged() is True            # global
    assert ks.is_engaged("any_scope") is True  # global covers every scope

    ks.disengage()
    assert ks.is_engaged() is False

    ks.engage("tenant_a")
    assert ks.is_engaged("tenant_a") is True
    assert ks.is_engaged("tenant_b") is False  # per-scope is isolated
    assert ks.is_engaged() is False            # not globally engaged


def test_killswitch_audit_log_records_actor_and_reason():
    ks = KillSwitch()
    ks.engage(reason="kill it", engaged_by="oncall_human")
    ks.disengage(disengaged_by="oncall_human")
    log = ks.audit_log
    assert [e["event"] for e in log] == ["engage", "disengage"]
    assert log[0]["reason"] == "kill it"
    assert log[0]["by"] == "oncall_human"
    # audit_log returns a copy — external mutation can't corrupt the ledger.
    log.append({"event": "forged"})
    assert len(ks.audit_log) == 2


# --- Execution Fabric halt --------------------------------------------------

def test_fabric_halts_execution_when_globally_engaged():
    ks = KillSwitch()
    fabric = ExecutionFabric(_world(), allow_unsigned_decisions=True, kill_switch=ks)
    ks.engage(reason="halt")
    # The halt signal is KillSwitchEngaged (a subclass of ExecutionError), so a
    # caller can distinguish a deliberate halt from an ordinary execution failure.
    with pytest.raises(KillSwitchEngaged, match="kill-switch is engaged"):
        fabric.execute(_proposal(), _approved())
    assert issubclass(KillSwitchEngaged, ExecutionError)


def test_fabric_resumes_after_disengage():
    ks = KillSwitch()
    fabric = ExecutionFabric(_world(), allow_unsigned_decisions=True, kill_switch=ks)
    ks.engage()
    with pytest.raises(ExecutionError):
        fabric.execute(_proposal(), _approved())
    ks.disengage()
    result = fabric.execute(_proposal(), _approved())
    assert result.success is True


def test_fabric_halts_only_targeted_scope():
    ks = KillSwitch()
    fabric = ExecutionFabric(_world(), allow_unsigned_decisions=True, kill_switch=ks)
    ks.engage("lead_123")  # halt actions touching lead_123 only

    with pytest.raises(ExecutionError):
        fabric.execute(_proposal(target="lead_123"), _approved())

    # An action targeting a different scope is unaffected.
    world = _world()
    world.entities["other"] = EntityState(
        entity_type="lead", entity_id="other", properties={}, last_updated=datetime.utcnow(),
        source="test")
    fabric2 = ExecutionFabric(world, allow_unsigned_decisions=True, kill_switch=ks)
    result = fabric2.execute(_proposal(pid="p2", target="other"), _approved("p2"))
    assert result.success is True


def test_fabric_halt_precedes_all_other_checks():
    """A halt overrides even a malformed/rejected decision — it is checked first,
    so an engaged switch is unconditional."""
    ks = KillSwitch()
    fabric = ExecutionFabric(_world(), allow_unsigned_decisions=True, kill_switch=ks)
    ks.engage()
    # Decision authorizes a DIFFERENT proposal id; without the halt this raises a
    # proposal-binding error. With the halt, the halt message wins (checked first).
    mismatched = _approved("some_other_proposal")
    with pytest.raises(ExecutionError, match="kill-switch is engaged"):
        fabric.execute(_proposal(pid="prop_1"), mismatched)


# --- CGA loop: no planning, no negotiation around a halt --------------------

def _loop(ks, gen):
    kernel = GovernanceKernel()
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex,
                             kill_switch=ks)
    return CGALoop(kernel, fabric, strategy_generator=gen, kill_switch=ks)


def test_cga_loop_halts_without_planning():
    ks = KillSwitch()
    gen = _CountingGen()
    loop = _loop(ks, gen)
    ks.engage(reason="contain")

    result = loop.run(intent=_intent(), drift_event={}, world_state=_world())

    assert result.final_verdict == "halted"
    assert result.halted is True
    assert result.proposals == []          # nothing was planned
    assert result.decisions == []
    assert result.execution_result is None
    assert result.total_attempts == 0
    assert gen.calls == 0                   # the strategy layer was never asked to plan


def test_cga_loop_does_not_negotiate_around_halt():
    """Re-running while engaged stays halted every time — CGA never finds a path
    to yes around a halt, no matter how many cycles run."""
    ks = KillSwitch()
    gen = _CountingGen()
    loop = _loop(ks, gen)
    ks.engage()
    for _ in range(5):
        result = loop.run(intent=_intent(), drift_event={}, world_state=_world())
        assert result.halted is True
    assert gen.calls == 0


def test_cga_loop_resumes_after_disengage():
    ks = KillSwitch()
    gen = _CountingGen()
    loop = _loop(ks, gen)

    ks.engage()
    assert loop.run(intent=_intent(), drift_event={}, world_state=_world()).halted is True

    ks.disengage()
    result = loop.run(intent=_intent(), drift_event={}, world_state=_world())
    assert result.halted is False
    assert result.final_verdict == "approved"
    assert gen.calls >= 1                   # planning resumed


def test_cga_loop_halts_on_per_scope_halt_without_planning():
    """A per-scope halt covering the entity this run would act on stops the loop
    BEFORE planning — symmetric with the fabric — and returns a clean halted
    result, never an uncaught ExecutionError."""
    ks = KillSwitch()
    gen = _CountingGen(target="lead_123")
    loop = _loop(ks, gen)
    ks.engage("lead_123")  # halt just this scope, not globally

    result = loop.run(intent=_intent(), drift_event={"entity_id": "lead_123"},
                      world_state=_world())

    assert result.halted is True
    assert result.final_verdict == "halted"
    assert gen.calls == 0           # the loop refused to plan on a halted scope
    assert result.proposals == []
    assert result.execution_result is None


def test_cga_loop_does_not_retarget_around_per_scope_halt():
    """The anti-corrigible failure mode the primitive must prevent: a per-scope
    halt on the drift entity must not be evaded by the generator re-planning the
    action onto a different, non-halted target. The loop halts before it can."""
    ks = KillSwitch()
    gen = _CountingGen(target="lead_999")  # would retarget away from the halt
    loop = _loop(ks, gen)
    ks.engage("lead_123")  # the entity under drift

    result = loop.run(intent=_intent(), drift_event={"entity_id": "lead_123"},
                      world_state=_world())

    assert result.halted is True
    assert gen.calls == 0           # never got the chance to retarget around it


def test_cga_loop_blocks_dispatch_to_halted_target_chosen_midplan():
    """Defense-in-depth: even when the drift entity is not itself halted, a
    proposal whose action targets a halted scope is stopped at the pre-dispatch
    re-check — a clean halted result, not an uncaught fabric error."""
    ks = KillSwitch()
    gen = _CountingGen(target="lead_123")  # the generator picks a halted target
    loop = _loop(ks, gen)
    ks.engage("lead_123")

    # Drift is about a DIFFERENT, non-halted entity, so the up-front check passes
    # and the loop plans — but the planned action targets the halted scope.
    result = loop.run(intent=_intent(), drift_event={"entity_id": "lead_safe"},
                      world_state=_world())

    assert result.halted is True
    assert gen.calls == 1           # it planned, then the pre-dispatch check halted
    assert result.execution_result is None


def test_agent_does_not_hold_the_switch():
    """Structural corrigibility: the kill-switch is referenced by the fabric and
    the loop, not by the strategy generator (the 'agent'). The agent has no API
    to disengage what contains it."""
    ks = KillSwitch()
    gen = _CountingGen()
    _loop(ks, gen)
    assert not hasattr(gen, "kill_switch")
    assert not hasattr(gen, "_kill_switch")


# --- Governed deployment wiring ---------------------------------------------

def test_governed_deployment_always_has_a_killswitch():
    from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
    from gap_kernel.governance.deployment import build_governed_deployment
    from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile

    priv, pub = generate_keypair()
    registry = PublicKeyRegistry({"regulatory_authority": pub})
    profile = sign_profile(
        ApplicabilityProfile(profile_id="p1", tier1_constraints=[],
                             issued_at=datetime(2026, 1, 1)),
        priv, "regulatory_authority",
    )

    loop = build_governed_deployment(
        applicability_profile=profile, profile_key_registry=registry,
        world_model=_world(), strategy_generator=_CountingGen(), isolated=False,
    )
    # A governed deployment is never without corrigibility.
    assert loop.kill_switch is not None
    # The same switch instance guards both the loop and its fabric.
    assert loop.execution._kill_switch is loop.kill_switch

    # Engaging halts the loop before any planning — even though governed mode
    # would otherwise require a resolved intent declaration, the halt precedes it.
    loop.kill_switch.engage(reason="human halt")
    result = loop.run(intent=_intent(), drift_event={}, world_state=_world())
    assert result.halted is True


# --- Reconciler: GAP's autonomous heartbeat is haltable ---------------------

def _drifting_reconciler():
    """A reconciler with one EU lead in active SLA drift (mirrors test_reconciler)."""
    from datetime import timedelta

    from gap_kernel.learning.engine import LearningEngine
    from gap_kernel.lineage.store import LineageStore
    from gap_kernel.models.intent import Constraint, ConstraintType
    from gap_kernel.models.reconciler import ReconcilerConfig
    from gap_kernel.reconciler.loop import ReconcilerLoop
    from gap_kernel.world_model.store import WorldModelStore

    world_store = WorldModelStore()
    governance = GovernanceKernel()
    fabric = ExecutionFabric(world_store.model,
                             kernel_public_key_hex=governance.public_key_hex)
    reconciler = ReconcilerLoop(
        world_store=world_store, governance_kernel=governance, execution_fabric=fabric,
        lineage_store=LineageStore(db_path=":memory:"), learning_engine=LearningEngine(),
        config=ReconcilerConfig(cooldown_seconds=0),
    )
    intent = IntentVector(
        id="lead_response_sla", objective="Respond to high-value leads within 10 minutes",
        priority=80,
        hard_constraints=[Constraint(name="gdpr_consent_required", type=ConstraintType.HARD,
                                     description="Verify GDPR consent before EU outreach")],
        soft_constraints=[], created_by="test", created_at=datetime.utcnow(),
    )
    reconciler.register_intent(intent)
    world_store.upsert_entity(EntityState(
        entity_type="lead", entity_id="lead_4821",
        properties={"name": "EU Lead", "value": 50000, "geo": "US", "gdpr_consent": True,
                    "local_hour": 14,
                    "created_at": (datetime.utcnow() - timedelta(minutes=8)).isoformat()},
        last_updated=datetime.utcnow(), source="crm", obligations=["lead_response_sla"],
    ))
    return reconciler, world_store


def test_reconciler_has_a_killswitch_by_default():
    """The autonomous heartbeat is never without corrigibility, even in open mode."""
    reconciler, _ = _drifting_reconciler()
    assert reconciler.kill_switch is not None


def test_reconciler_halts_autonomous_dispatch_when_engaged():
    """The review's critical finding: engaging the switch must stop GAP's
    autonomous drift-correction. No action is dispatched, the cycle reports
    'halted', and lineage is still recorded (an orderly stop, not a crash)."""
    reconciler, world_store = _drifting_reconciler()
    reconciler.kill_switch.engage(reason="incident: contain autonomous action")

    results = reconciler.reconcile_once()

    assert len(results) >= 1
    for r in results:
        assert r["verdict"] == "halted"
        assert r["execution_success"] is False
    # No outreach happened — the lead was never contacted.
    assert "last_contacted" not in world_store.model.entities["lead_4821"].properties
    # The halt is auditable as a normal cycle: lineage was still recorded.
    assert reconciler.lineage_store.count() >= 1


def test_reconciler_resumes_after_disengage():
    reconciler, world_store = _drifting_reconciler()
    reconciler.kill_switch.engage()
    assert all(r["verdict"] == "halted" for r in reconciler.reconcile_once())

    reconciler.kill_switch.disengage()
    results = reconciler.reconcile_once()
    assert any(r["verdict"] in ("approved", "escalated") for r in results)


def test_create_app_wires_one_shared_killswitch_through_the_path():
    """create_app must wire ONE switch into both the fabric and the reconciler,
    exposed as app.state.kill_switch for out-of-band human control."""
    from gap_kernel.api.app import create_app

    app = create_app()  # open mode is enough to prove the wiring
    ks = app.state.kill_switch
    assert ks is not None
    assert app.state.execution_fabric._kill_switch is ks
    assert app.state.reconciler.kill_switch is ks
