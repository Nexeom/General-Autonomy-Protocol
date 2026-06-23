"""Multi-agent / sub-agent governance surface (rec #4).

Earns three properties across a delegation tree: delegation cannot amplify
authority, constraints propagate downward and only accumulate, and corrigibility
(a halt) propagates to the whole subtree — plus cross-agent decomposition
detection.
"""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.multi_agent import (
    SubAgentRegistry,
    SubAgentViolation,
)
from gap_kernel.models.governance import AuthorizationLevel as AL

T0 = datetime(2026, 6, 23, 12, 0, 0)


def _registry(**kw):
    return SubAgentRegistry(
        root_ceiling=AL.L2, root_constraints={"gdpr", "tier1_floor"}, **kw
    )


# --- delegation cannot amplify authority ------------------------------------

def test_subagent_inherits_parent_ceiling_by_default():
    reg = _registry()
    child = reg.register("child", parent_id="root")
    assert child.authorization_ceiling == AL.L2


def test_subagent_may_have_lower_ceiling():
    reg = _registry()
    child = reg.register("child", parent_id="root", requested_ceiling=AL.L0)
    assert child.authorization_ceiling == AL.L0


def test_subagent_cannot_exceed_parent_ceiling():
    reg = _registry()  # root ceiling L2
    with pytest.raises(SubAgentViolation, match="amplify authority"):
        reg.register("child", parent_id="root", requested_ceiling=AL.L3)


def test_grandchild_ceiling_is_bounded_by_the_chain():
    reg = _registry()
    reg.register("child", parent_id="root", requested_ceiling=AL.L1)
    # A grandchild cannot exceed its immediate parent (L1), even though root is L2.
    with pytest.raises(SubAgentViolation):
        reg.register("grandchild", parent_id="child", requested_ceiling=AL.L2)
    gc = reg.register("grandchild", parent_id="child", requested_ceiling=AL.L0)
    assert gc.authorization_ceiling == AL.L0


def test_delegation_cannot_route_around_escalation():
    """The attack: an agent that must escalate an L3 action spawns a child to do
    it autonomously. The child's ceiling cannot exceed the parent, so it must
    escalate too — and registering a higher-ceiling child fails closed."""
    reg = SubAgentRegistry(root_ceiling=AL.L1, root_constraints=set())
    assert reg.authorize("root", AL.L1) is True
    assert reg.authorize("root", AL.L3) is False     # root must escalate L3
    child = reg.register("worker", parent_id="root")
    assert reg.authorize("worker", AL.L3) is False    # child must escalate too
    with pytest.raises(SubAgentViolation):
        reg.register("privileged", parent_id="root", requested_ceiling=AL.L4)


def test_unknown_parent_and_duplicate_registration_fail_closed():
    reg = _registry()
    with pytest.raises(SubAgentViolation, match="Unknown parent"):
        reg.register("orphan", parent_id="ghost")
    reg.register("child", parent_id="root")
    with pytest.raises(SubAgentViolation, match="already registered"):
        reg.register("child", parent_id="root")


# --- constraints propagate downward and accumulate --------------------------

def test_constraints_propagate_and_accumulate():
    reg = _registry()  # root constraints {gdpr, tier1_floor}
    reg.register("child", parent_id="root", added_constraints={"pci"})
    gc = reg.register("grandchild", parent_id="child", added_constraints={"sox"})
    # Every inherited constraint flows down; children add but never drop.
    assert reg.constraints_for("child") == {"gdpr", "tier1_floor", "pci"}
    assert reg.constraints_for("grandchild") == {"gdpr", "tier1_floor", "pci", "sox"}
    assert {"gdpr", "tier1_floor"} <= gc.inherited_constraints


def test_constraints_set_is_copied_out():
    reg = _registry()
    reg.register("child", parent_id="root")
    got = reg.constraints_for("child")
    got.add("forged")
    assert "forged" not in reg.constraints_for("child")


# --- records are immutable: the invariants cannot be mutated in place -------

def test_ceiling_cannot_be_amplified_in_place():
    """The record handed back is the live entry, so it must be frozen — otherwise
    an agent could raise its own ceiling and act at a level it must escalate."""
    reg = SubAgentRegistry(root_ceiling=AL.L1, root_constraints=set())
    child = reg.register("child", parent_id="root", requested_ceiling=AL.L1)
    assert reg.authorize("child", AL.L4) is False
    with pytest.raises(ValidationError):
        child.authorization_ceiling = AL.L4          # frozen — fails closed
    assert reg.ceiling_for("child") == AL.L1
    assert reg.authorize("child", AL.L4) is False
    # And the bound the grandchild is checked against is still the real one.
    with pytest.raises(SubAgentViolation):
        reg.register("gc", parent_id="child", requested_ceiling=AL.L4)


def test_inherited_constraint_cannot_be_dropped_via_record():
    reg = _registry()  # root constraints {gdpr, tier1_floor}
    child = reg.register("child", parent_id="root", added_constraints={"pci"})
    assert isinstance(child.inherited_constraints, frozenset)
    with pytest.raises(AttributeError):
        child.inherited_constraints.discard("gdpr")  # frozenset has no discard
    # Reassigning the field is also blocked (frozen record).
    with pytest.raises(ValidationError):
        reg.get("child").inherited_constraints = frozenset()
    assert "gdpr" in reg.constraints_for("child")
    # A grandchild registered afterward still inherits the intact floor.
    gc = reg.register("gc", parent_id="child")
    assert {"gdpr", "tier1_floor", "pci"} <= gc.inherited_constraints


# --- corrigibility propagates through the tree ------------------------------

def test_halt_without_killswitch_is_never_halted():
    reg = _registry()  # no kill_switch
    reg.register("child", parent_id="root")
    assert reg.is_halted("child") is False


def test_global_halt_halts_every_agent():
    ks = KillSwitch()
    reg = _registry(kill_switch=ks)
    reg.register("child", parent_id="root")
    ks.engage()
    assert reg.is_halted("root") is True
    assert reg.is_halted("child") is True


def test_agent_scoped_halt_is_isolated():
    ks = KillSwitch()
    reg = _registry(kill_switch=ks)
    reg.register("child_a", parent_id="root")
    reg.register("child_b", parent_id="root")
    ks.engage("child_a")
    assert reg.is_halted("child_a") is True
    assert reg.is_halted("child_b") is False
    assert reg.is_halted("root") is False


def test_ancestor_halt_contains_the_subtree():
    ks = KillSwitch()
    reg = _registry(kill_switch=ks)
    reg.register("child", parent_id="root")
    reg.register("grandchild", parent_id="child")
    reg.register("sibling", parent_id="root")
    ks.engage("child")  # halt a mid-tree parent
    assert reg.is_halted("child") is True
    assert reg.is_halted("grandchild") is True   # subtree is contained
    assert reg.is_halted("sibling") is False     # a different branch is not


# --- cross-agent threshold-avoidance decomposition --------------------------

def test_cross_agent_decomposition_is_flagged():
    reg = _registry()
    reg.register("a1", parent_id="root", requested_ceiling=AL.L0)
    reg.register("a2", parent_id="root", requested_ceiling=AL.L0)
    # Three low-level actions on one target, spread across two agents.
    reg.record_action("a1", "acct_9", AL.L0, timestamp=T0)
    reg.record_action("a2", "acct_9", AL.L0, timestamp=T0 + timedelta(seconds=10))
    reg.record_action("a1", "acct_9", AL.L0, timestamp=T0 + timedelta(seconds=20))
    signal = reg.check_cross_agent_decomposition("acct_9")
    assert signal is not None
    assert signal.signal_type == "GIM-3-multiagent"
    assert signal.evidence["agents"] == ["a1", "a2"]
    assert signal.evidence["count"] == 3


def test_single_agent_decomposition_is_not_cross_agent():
    """One agent doing the splitting is GIM-3's job (target-keyed); the multi-agent
    detector only fires when the pattern spans >= 2 agents."""
    reg = _registry()
    reg.register("solo", parent_id="root", requested_ceiling=AL.L0)
    for i in range(3):
        reg.record_action("solo", "acct_9", AL.L0, timestamp=T0 + timedelta(seconds=i))
    assert reg.check_cross_agent_decomposition("acct_9") is None


def test_cross_agent_decomposition_respects_threshold_and_level():
    reg = _registry()
    reg.register("a1", parent_id="root", requested_ceiling=AL.L0)
    reg.register("a2", parent_id="root", requested_ceiling=AL.L2)
    # Below count threshold (2 < 3).
    reg.record_action("a1", "acct_9", AL.L0, timestamp=T0)
    reg.record_action("a2", "acct_9", AL.L0, timestamp=T0 + timedelta(seconds=5))
    assert reg.check_cross_agent_decomposition("acct_9") is None
    # High-authorization actions are not decomposition (they hit a real gate).
    reg.record_action("a2", "acct_9", AL.L2, timestamp=T0 + timedelta(seconds=6))
    assert reg.check_cross_agent_decomposition("acct_9") is None


def test_cross_agent_decomposition_respects_window():
    reg = _registry(decomposition_window_seconds=300)
    reg.register("a1", parent_id="root", requested_ceiling=AL.L0)
    reg.register("a2", parent_id="root", requested_ceiling=AL.L0)
    reg.record_action("a1", "acct_9", AL.L0, timestamp=T0)
    reg.record_action("a2", "acct_9", AL.L0, timestamp=T0 + timedelta(hours=1))
    reg.record_action("a1", "acct_9", AL.L0, timestamp=T0 + timedelta(hours=2))
    assert reg.check_cross_agent_decomposition("acct_9") is None


def test_cross_agent_decomposition_survives_later_traffic():
    """Monotonic detection: once a cross-agent decomposition qualifies, a later
    same-target action (of any rank) must not move the window anchor and evict
    the cluster — otherwise the agents could suppress the signal at will."""
    reg = _registry()
    reg.register("a1", parent_id="root", requested_ceiling=AL.L0)
    reg.register("a2", parent_id="root", requested_ceiling=AL.L2)
    reg.record_action("a1", "acct_9", AL.L0, timestamp=T0)
    reg.record_action("a2", "acct_9", AL.L0, timestamp=T0 + timedelta(seconds=10))
    reg.record_action("a1", "acct_9", AL.L0, timestamp=T0 + timedelta(seconds=20))
    assert reg.check_cross_agent_decomposition("acct_9") is not None
    # A much-later action on the same target must not suppress the earlier cluster.
    reg.record_action("a2", "acct_9", AL.L2, timestamp=T0 + timedelta(seconds=600))
    assert reg.check_cross_agent_decomposition("acct_9") is not None


def test_record_action_unknown_agent_fails_closed():
    reg = _registry()
    with pytest.raises(SubAgentViolation):
        reg.record_action("ghost", "acct_9", AL.L0, timestamp=T0)
