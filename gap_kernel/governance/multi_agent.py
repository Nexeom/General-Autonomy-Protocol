"""Multi-agent / sub-agent governance surface (rec #4).

GAP governs a single agent well, but an agent can evade governance by *delegating*:
spawn a sub-agent, hand it the high-stakes action, and if the sub-agent is not
held to the parent's bounds, oversight is escaped. ``SubAgentRegistry`` is the
surface that lets a deployment hold a delegation tree to GAP's guarantees. When
consulted, it checks three invariants:

  1. **Authority is not amplified by delegation.** Each agent has an
     authorization *ceiling* — the maximum level it may exercise autonomously.
     A sub-agent's ceiling is rank-bounded by its parent's (monotone
     non-increasing down the tree); registering a sub-agent above its parent
     fails closed. So an agent that must escalate an action cannot register a
     child that ``authorize()`` would clear to handle it autonomously.

  2. **Constraints propagate downward and only accumulate.** A sub-agent
     inherits the union of its ancestors' constraints; it may add its own but can
     never drop an inherited one (records are frozen). The regulatory floor
     reaches every leaf.

  3. **Corrigibility propagates.** ``is_halted()`` reports an agent as halted when
     it, any ancestor, or the whole system is halted — so a halt contains the
     whole subtree.

It also detects **cross-agent threshold-avoidance decomposition**: a high-tier
action split into sub-threshold pieces spread across *several* sub-agents (≥2
distinct agents on one target within a window).

Honest scoping: this is a registry/detector the deployment **consults** —
``authorize()`` / ``is_halted()`` / ``check_cross_agent_decomposition()`` express
and check the invariants when asked. Routing every live sub-agent action through
these bounds (so a sub-agent that never calls the registry is still contained) is
deployment-side and is NOT wired into this codebase's single-agent CGA loop; the
delegation tree is in-memory and single-process. A distributed sub-agent fabric
is Normative / Planned.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, FrozenSet, Iterable, List, Optional

from pydantic import BaseModel, ConfigDict

from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.integrity_monitor import IntegritySignal
from gap_kernel.models.governance import AuthorizationLevel

_RANK = {
    AuthorizationLevel.L0: 0,
    AuthorizationLevel.L1: 1,
    AuthorizationLevel.L2: 2,
    AuthorizationLevel.L3: 3,
    AuthorizationLevel.L4: 4,
}


class SubAgentViolation(Exception):
    """Raised when a sub-agent registration would violate a governance invariant
    (authority amplification, dropping an inherited constraint, unknown parent)."""


class SubAgentRecord(BaseModel):
    """A registered agent in the delegation tree.

    Frozen after construction: a record handed back by ``register()`` / ``get()``
    is the live registry entry, so its ceiling and constraints must be immutable —
    otherwise a holder could amplify its own ceiling or drop an inherited
    constraint in place, defeating the registry's invariants. ``frozen=True``
    blocks field reassignment and ``inherited_constraints`` is a ``frozenset`` so
    it cannot be mutated in place either.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str
    parent_id: Optional[str]
    authorization_ceiling: AuthorizationLevel
    inherited_constraints: FrozenSet[str]
    depth: int


class _ActionObservation(BaseModel):
    agent_id: str
    target: str
    rank: int
    at: datetime


class SubAgentRegistry:
    """Tracks a delegation tree and enforces its governance invariants."""

    def __init__(
        self,
        *,
        root_id: str = "root",
        root_ceiling: AuthorizationLevel = AuthorizationLevel.L0,
        root_constraints: Optional[Iterable[str]] = None,
        kill_switch: Optional[KillSwitch] = None,
        decomposition_window_seconds: int = 300,
        decomposition_count_threshold: int = 3,
        decomposition_max_level: AuthorizationLevel = AuthorizationLevel.L1,
    ):
        self._agents: Dict[str, SubAgentRecord] = {}
        self._kill_switch = kill_switch
        self._actions_by_target: Dict[str, List[_ActionObservation]] = defaultdict(list)
        self.decomposition_window = timedelta(seconds=decomposition_window_seconds)
        self.decomposition_count_threshold = decomposition_count_threshold
        self.decomposition_max_rank = _RANK[decomposition_max_level]
        # The root is the top-level governed agent; everything descends from it.
        self._agents[root_id] = SubAgentRecord(
            agent_id=root_id,
            parent_id=None,
            authorization_ceiling=root_ceiling,
            inherited_constraints=frozenset(root_constraints or ()),
            depth=0,
        )
        self._root_id = root_id

    # --- registration ------------------------------------------------------

    def register(
        self,
        agent_id: str,
        *,
        parent_id: str,
        requested_ceiling: Optional[AuthorizationLevel] = None,
        added_constraints: Optional[Iterable[str]] = None,
    ) -> SubAgentRecord:
        """Register a sub-agent under ``parent_id``, fail-closed on any violation.

        - The parent must already be registered.
        - The ceiling defaults to the parent's and may not exceed it (delegation
          cannot amplify authority).
        - Inherited constraints are the parent's set plus any added; an inherited
          constraint can never be dropped.
        """
        if agent_id in self._agents:
            raise SubAgentViolation(f"Agent '{agent_id}' is already registered.")
        parent = self._agents.get(parent_id)
        if parent is None:
            raise SubAgentViolation(
                f"Unknown parent '{parent_id}' for sub-agent '{agent_id}'."
            )
        ceiling = requested_ceiling if requested_ceiling is not None else parent.authorization_ceiling
        if _RANK[ceiling] > _RANK[parent.authorization_ceiling]:
            raise SubAgentViolation(
                f"Sub-agent '{agent_id}' requested ceiling {ceiling.value} exceeds "
                f"parent '{parent_id}' ceiling {parent.authorization_ceiling.value}; "
                f"delegation cannot amplify authority."
            )
        inherited = frozenset(parent.inherited_constraints | set(added_constraints or ()))
        record = SubAgentRecord(
            agent_id=agent_id,
            parent_id=parent_id,
            authorization_ceiling=ceiling,
            inherited_constraints=inherited,
            depth=parent.depth + 1,
        )
        self._agents[agent_id] = record
        return record

    # --- queries -----------------------------------------------------------

    def get(self, agent_id: str) -> SubAgentRecord:
        record = self._agents.get(agent_id)
        if record is None:
            raise SubAgentViolation(f"Unknown agent '{agent_id}'.")
        return record

    def ceiling_for(self, agent_id: str) -> AuthorizationLevel:
        return self.get(agent_id).authorization_ceiling

    def constraints_for(self, agent_id: str) -> Set[str]:
        return set(self.get(agent_id).inherited_constraints)

    def authorize(self, agent_id: str, required_level: AuthorizationLevel) -> bool:
        """True if the agent may act autonomously at ``required_level`` (its rank
        is within the agent's ceiling). A higher required level than the ceiling
        means the agent must escalate — and since a sub-agent's ceiling never
        exceeds its parent's, a sub-agent escalates at least as often."""
        return _RANK[required_level] <= _RANK[self.ceiling_for(agent_id)]

    def ancestors(self, agent_id: str) -> List[str]:
        """The chain of ancestor ids from immediate parent up to the root."""
        chain: List[str] = []
        current = self.get(agent_id).parent_id
        while current is not None:
            chain.append(current)
            current = self.get(current).parent_id
        return chain

    # --- corrigibility -----------------------------------------------------

    def is_halted(self, agent_id: str) -> bool:
        """True if a halt applies to this agent: a global halt, a halt scoped to
        the agent itself, or a halt on ANY ancestor (a parent halt contains its
        whole subtree)."""
        if self._kill_switch is None:
            return False
        if self._kill_switch.is_engaged():            # global
            return True
        if self._kill_switch.is_engaged(agent_id):    # this agent's scope
            return True
        return any(self._kill_switch.is_engaged(a) for a in self.ancestors(agent_id))

    # --- cross-agent decomposition ----------------------------------------

    def record_action(
        self,
        agent_id: str,
        target: str,
        authorization_level: AuthorizationLevel,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record a sub-agent action against a target, for cross-agent
        decomposition detection. ``agent_id`` must be registered."""
        self.get(agent_id)  # validate membership (fail closed on unknown agent)
        self._actions_by_target[target].append(
            _ActionObservation(
                agent_id=agent_id,
                target=target,
                rank=_RANK[authorization_level],
                at=timestamp or datetime.utcnow(),
            )
        )

    def check_cross_agent_decomposition(self, target: str) -> Optional[IntegritySignal]:
        """Flag many low-authorization actions against one target, within the
        window, spread across **two or more distinct agents** — a high-tier task
        decomposed across sub-agents to keep each piece sub-threshold.

        Detection is recency-relative: it considers the band of low-authorization
        actions within ``decomposition_window`` of the most recent one, so it
        fires during a burst and CLEARS once the burst ages out (rather than
        latching forever). A later *higher*-rank action cannot suppress an active
        burst — high-rank actions are filtered out, so they do not move the
        recency anchor.
        """
        low = sorted(
            (o for o in self._actions_by_target.get(target, [])
             if o.rank <= self.decomposition_max_rank),
            key=lambda o: o.at,
        )
        if len(low) < self.decomposition_count_threshold:
            return None
        newest = low[-1].at
        band = [o for o in low if newest - o.at <= self.decomposition_window]
        agents = {o.agent_id for o in band}
        if len(band) >= self.decomposition_count_threshold and len(agents) >= 2:
            window_seconds = int(self.decomposition_window.total_seconds())
            return IntegritySignal(
                signal_type="GIM-3-multiagent",
                subject=target,
                severity="warning",
                detail=(
                    f"{len(band)} low-authorization actions against '{target}' "
                    f"within {window_seconds}s, spread across {len(agents)} agents "
                    f"— possible cross-agent threshold-avoidance decomposition."
                ),
                evidence={
                    "count": len(band),
                    "agents": sorted(agents),
                    "max_rank": self.decomposition_max_rank,
                },
            )
        return None


__all__ = ["SubAgentRegistry", "SubAgentRecord", "SubAgentViolation"]
