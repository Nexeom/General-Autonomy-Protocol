"""Governed sub-agent execution — the SubAgentRegistry bounds ENFORCED at dispatch.

Proves the registry invariants are enforced on a live path (not merely consulted):
a sub-agent cannot dispatch above its ceiling, a halted sub-agent (or any ancestor)
cannot dispatch, and realized actions feed cross-agent decomposition detection.
"""

from datetime import datetime

import pytest

from gap_kernel.execution.fabric import ExecutionFabric, KillSwitchEngaged
from gap_kernel.execution.sub_agent_executor import SubAgentExecutor
from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.multi_agent import SubAgentRegistry, SubAgentViolation
from gap_kernel.models.governance import AuthorizationLevel as AL
from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel


def _world():
    return WorldModel(
        entities={"acct_9": EntityState(entity_type="acct", entity_id="acct_9",
                                        properties={}, last_updated=datetime.utcnow(),
                                        source="t")},
        last_reconciled=datetime.utcnow(),
    )


def _proposal(pid="p1", target="acct_9"):
    return StrategyProposal(
        id=pid, intent_id="i1", attempt_number=1, plan_description="x",
        actions=[PlannedAction(action_type="query_crm", target=target, parameters={}, risk_score=1)],
        estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
    )


def _decision(level, pid="p1"):
    return GovernanceDecision(
        id="d1", proposal_id=pid, verdict=GovernanceVerdict.APPROVED,
        authorization_tier="auto_execute", authorization_level=level,
        evaluated_at=datetime.utcnow(),
    )


def _exec(registry):
    fabric = ExecutionFabric(_world(), allow_unsigned_decisions=True)
    return SubAgentExecutor(registry, fabric)


def test_action_within_ceiling_executes():
    reg = SubAgentRegistry(root_ceiling=AL.L2)
    reg.register("child", parent_id="root", requested_ceiling=AL.L2)
    result = _exec(reg).execute("child", _proposal(), _decision(AL.L1))
    assert result.success is True


def test_action_above_ceiling_is_refused():
    reg = SubAgentRegistry(root_ceiling=AL.L2)
    reg.register("child", parent_id="root", requested_ceiling=AL.L1)
    with pytest.raises(SubAgentViolation, match="must escalate"):
        _exec(reg).execute("child", _proposal(), _decision(AL.L2))  # L2 > L1 ceiling


def test_halted_sub_agent_is_refused():
    ks = KillSwitch()
    reg = SubAgentRegistry(root_ceiling=AL.L2, kill_switch=ks)
    reg.register("child", parent_id="root")
    ks.engage("child")
    with pytest.raises(KillSwitchEngaged):
        _exec(reg).execute("child", _proposal(), _decision(AL.L1))


def test_ancestor_halt_contains_the_subtree_at_dispatch():
    ks = KillSwitch()
    reg = SubAgentRegistry(root_ceiling=AL.L2, kill_switch=ks)
    reg.register("child", parent_id="root")
    reg.register("grandchild", parent_id="child")
    ks.engage("child")  # halt a mid-tree parent
    with pytest.raises(KillSwitchEngaged):
        _exec(reg).execute("grandchild", _proposal(), _decision(AL.L1))


def test_realized_actions_feed_cross_agent_decomposition():
    reg = SubAgentRegistry(root_ceiling=AL.L0, decomposition_count_threshold=3)
    reg.register("a1", parent_id="root", requested_ceiling=AL.L0)
    reg.register("a2", parent_id="root", requested_ceiling=AL.L0)
    ex = _exec(reg)
    # Three sub-threshold actions on one target, spread across two agents.
    ex.execute("a1", _proposal(target="acct_9"), _decision(AL.L0))
    ex.execute("a2", _proposal(target="acct_9"), _decision(AL.L0))
    ex.execute("a1", _proposal(target="acct_9"), _decision(AL.L0))
    signal = reg.check_cross_agent_decomposition("acct_9")
    assert signal is not None
    assert sorted(signal.evidence["agents"]) == ["a1", "a2"]


def test_decision_without_level_fails_closed():
    """An approved decision carrying no authorization level cannot have its ceiling
    verified, so the sub-agent executor refuses it (fail closed)."""
    reg = SubAgentRegistry(root_ceiling=AL.L2)
    reg.register("child", parent_id="root", requested_ceiling=AL.L2)
    with pytest.raises(SubAgentViolation, match="cannot be verified"):
        _exec(reg).execute("child", _proposal(), _decision(None))


def test_failed_dispatch_is_not_recorded():
    reg = SubAgentRegistry(root_ceiling=AL.L2, decomposition_count_threshold=1)
    reg.register("a1", parent_id="root", requested_ceiling=AL.L2)
    # An unknown action type fails in the fabric -> result.success False -> no record.
    bad = StrategyProposal(
        id="p1", intent_id="i1", attempt_number=1, plan_description="x",
        actions=[PlannedAction(action_type="launch_missiles", target="acct_9",
                               parameters={}, risk_score=1)],
        estimated_cost=0.0, rationale="r", generated_at=datetime.utcnow(),
    )
    result = _exec(reg).execute("a1", bad, _decision(AL.L1))
    assert result.success is False
    assert reg.check_cross_agent_decomposition("acct_9") is None  # nothing recorded


def test_partial_success_records_only_realized_actions():
    """A decoy failing action must not suppress recording of the real action that
    DID execute — recording follows realized (completed) actions, not whole-proposal
    success — so cross-agent decomposition cannot be evaded by padding with a
    failing decoy."""
    reg = SubAgentRegistry(root_ceiling=AL.L0, decomposition_count_threshold=3)
    reg.register("a1", parent_id="root", requested_ceiling=AL.L0)
    reg.register("a2", parent_id="root", requested_ceiling=AL.L0)
    ex = _exec(reg)

    def padded(pid):
        # one real (succeeds) + one decoy unregistered action (fails) on the same target
        return StrategyProposal(
            id=pid, intent_id="i1", attempt_number=1, plan_description="x",
            actions=[
                PlannedAction(action_type="query_crm", target="acct_9", parameters={}, risk_score=1),
                PlannedAction(action_type="decoy_unregistered", target="acct_9", parameters={}, risk_score=1),
            ],
            estimated_cost=0.0, rationale="r", generated_at=datetime.utcnow(),
        )

    for agent, pid in (("a1", "p1"), ("a2", "p2"), ("a1", "p3")):
        r = ex.execute(agent, padded(pid), _decision(AL.L0, pid=pid))
        assert r.success is False                   # decoy fails the whole proposal
        assert any(c["target"] == "acct_9" for c in r.actions_completed)  # real one ran
    # The three realized query_crm actions across two agents are still detected.
    signal = reg.check_cross_agent_decomposition("acct_9")
    assert signal is not None and sorted(signal.evidence["agents"]) == ["a1", "a2"]
