"""Self-Evolution Capability-Gain Monitoring (SA-4).

Detects capability gain through self-modification — burst velocity, upward
privilege accrual, and capability-surface expansion — and HOLDS a self-modification
on the CGA loop when the agent is in a flagged capability-gain pattern.
"""

from datetime import datetime, timedelta

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.self_evolution import SelfEvolutionMonitor
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.strategy.cga_loop import CGALoop

T0 = datetime(2026, 6, 23, 12, 0, 0)


# --- SE-VELOCITY ------------------------------------------------------------

def test_velocity_flags_a_burst_of_self_modifications():
    mon = SelfEvolutionMonitor(velocity_threshold=5)
    for i in range(5):
        mon.observe("skill_modification", "L2", target=f"skill_{i}", timestamp=T0 + timedelta(seconds=i))
    sig = mon.check_velocity()
    assert sig is not None and sig.signal_type == "SE-VELOCITY"
    assert sig.evidence["count"] == 5


def test_velocity_below_threshold_is_quiet():
    mon = SelfEvolutionMonitor(velocity_threshold=5)
    for i in range(4):
        mon.observe("skill_modification", "L2", target=f"skill_{i}", timestamp=T0 + timedelta(seconds=i))
    assert mon.check_velocity() is None


def test_velocity_is_recency_relative_and_clears():
    mon = SelfEvolutionMonitor(velocity_threshold=5, velocity_window_seconds=3600)
    for i in range(5):
        mon.observe("skill_modification", "L2", target=f"s{i}", timestamp=T0 + timedelta(seconds=i))
    assert mon.check_velocity() is not None
    # A modification long after the burst ages it out (no longer a current burst).
    mon.observe("skill_modification", "L2", target="later", timestamp=T0 + timedelta(days=1))
    assert mon.check_velocity() is None


# --- SE-PRIVILEGE (upward authorization drift) ------------------------------

def test_privilege_accrual_flags_upward_drift():
    mon = SelfEvolutionMonitor()
    for _ in range(5):
        mon.observe("skill_modification", "L1")   # baseline: low privilege
    for _ in range(3):
        mon.observe("skill_modification", "L4")   # recent: high privilege
    sig = mon.check_privilege_accrual()
    assert sig is not None
    assert sig.signal_type == "SE-PRIVILEGE"
    assert sig.severity == "critical"
    assert sig.evidence["drift"] >= 1.0


def test_privilege_accrual_quiet_when_stable():
    mon = SelfEvolutionMonitor()
    for _ in range(8):
        mon.observe("skill_modification", "L2")
    assert mon.check_privilege_accrual() is None


def test_privilege_accrual_ignores_downward_drift():
    """Privilege going DOWN is not capability gain (that is GIM-1's concern)."""
    mon = SelfEvolutionMonitor()
    for _ in range(5):
        mon.observe("skill_modification", "L4")
    for _ in range(3):
        mon.observe("skill_modification", "L0")
    assert mon.check_privilege_accrual() is None


# --- SE-SURFACE (capability-surface expansion) ------------------------------

def test_surface_expansion_flags_many_distinct_capabilities():
    mon = SelfEvolutionMonitor(surface_threshold=5)
    for i in range(5):
        mon.observe("skill_modification", "L2", target=f"skill_{i}", timestamp=T0 + timedelta(minutes=i))
    sig = mon.check_surface_expansion()
    assert sig is not None and sig.signal_type == "SE-SURFACE"
    assert len(sig.evidence["distinct_targets"]) == 5


def test_surface_expansion_ignores_repeated_same_target():
    mon = SelfEvolutionMonitor(surface_threshold=5)
    for i in range(8):
        mon.observe("skill_modification", "L2", target="one_skill", timestamp=T0 + timedelta(minutes=i))
    assert mon.check_surface_expansion() is None


# --- ingestion + bounded state ----------------------------------------------

def test_observe_ignores_non_self_modifying_action_types():
    mon = SelfEvolutionMonitor()
    assert mon.observe("task_execution", "L0") is None
    assert mon.observe("query_crm", "L0") is None
    assert mon._mods == []


def test_state_is_bounded():
    mon = SelfEvolutionMonitor(max_history=50)
    for i in range(500):
        mon.observe("skill_modification", "L2", target=f"s{i}", timestamp=T0 + timedelta(seconds=i))
    assert len(mon._mods) <= 50


def test_scan_returns_active_signals():
    mon = SelfEvolutionMonitor(velocity_threshold=5, surface_threshold=5)
    for i in range(5):
        mon.observe("skill_modification", "L2", target=f"s{i}", timestamp=T0 + timedelta(seconds=i))
    types = {s.signal_type for s in mon.scan()}
    assert "SE-VELOCITY" in types and "SE-SURFACE" in types


# --- consequential on the CGA loop ------------------------------------------

class _SkillModGen:
    def generate(self, intent, world_state, drift_event, accumulated_constraints,
                 prior_proposals, attempt_number):
        return StrategyProposal(
            id=f"prop_{attempt_number}", intent_id=intent.id, attempt_number=attempt_number,
            plan_description="modify a skill",
            actions=[PlannedAction(action_type="query_crm", target="skill_x", parameters={}, risk_score=1)],
            estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
        )


def _intent():
    return IntentVector(id="i1", objective="o", priority=50, hard_constraints=[],
                        soft_constraints=[], created_by="t", created_at=datetime.utcnow())


def _world():
    return WorldModel(entities={}, last_reconciled=datetime.utcnow())


def _loop(monitor, *, block):
    kernel = GovernanceKernel()
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    return CGALoop(kernel, fabric, strategy_generator=_SkillModGen(),
                   self_evolution_monitor=monitor, block_on_integrity=block)


def test_loop_holds_when_already_in_a_capability_gain_pattern():
    """Under block_on_integrity, a new skill_modification is HELD when the agent is
    ALREADY in a flagged pattern from PRIOR REALIZED modifications (the hold uses
    history, not the current proposal)."""
    monitor = SelfEvolutionMonitor(velocity_threshold=3)
    for i in range(3):  # three already-realized self-modifications
        monitor.observe("skill_modification", "L2", target=f"prior_{i}")
    assert monitor.is_flagged()
    result = _loop(monitor, block=True).run(
        intent=_intent(), drift_event={}, world_state=_world(),
        action_type_id="skill_modification",
    )
    assert result.final_verdict == "integrity_hold"
    assert result.execution_result is None


def test_unexecuted_self_mods_do_not_inflate_the_monitor():
    """The core correctness property: a stream of approved-but-awaiting-OOB
    skill_modifications (none executed) must NOT count as realized capability gain,
    so they neither inflate the counters nor trip a spurious hold on a later one."""
    monitor = SelfEvolutionMonitor(velocity_threshold=3, surface_threshold=3)
    loop = _loop(monitor, block=True)
    for i in range(6):
        result = loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                          action_type_id="skill_modification")
        # Each L2 self-mod is surfaced for human OOB approval; none executes.
        assert result.final_verdict == "awaiting_approval"
    assert monitor._mods == []          # nothing realized => nothing counted
    assert not monitor.is_flagged()


def test_realized_self_mod_is_counted_on_execution():
    """A self-modification that actually EXECUTES is counted (treating an
    auto-executing L0 action type as self-modifying for the test)."""
    monitor = SelfEvolutionMonitor(self_modifying_action_types={"task_execution"},
                                   velocity_threshold=1)
    result = _loop(monitor, block=False).run(
        intent=_intent(), drift_event={}, world_state=_world(),
        action_type_id="task_execution",   # L0 -> auto-executes
    )
    assert result.final_verdict == "approved"
    assert result.execution_result is not None and result.execution_result.success
    assert len(monitor._mods) == 1       # realized => counted


def test_loop_does_not_hold_non_self_modifying_actions():
    monitor = SelfEvolutionMonitor(velocity_threshold=1)
    monitor.observe("skill_modification", "L2", target="prior")  # flagged pattern
    result = _loop(monitor, block=True).run(
        intent=_intent(), drift_event={}, world_state=_world(),
        action_type_id="task_execution",   # not self-modifying -> not held by SA-4
    )
    assert result.final_verdict == "approved"
