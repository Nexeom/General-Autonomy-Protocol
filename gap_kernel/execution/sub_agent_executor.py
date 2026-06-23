"""Governed sub-agent execution — ENFORCING the SubAgentRegistry bounds (SA-4).

``SubAgentRegistry`` expresses the delegation-tree invariants (authority is not
amplified by delegation, corrigibility propagates, cross-agent decomposition is
detected), but on its own it only *computes* them for a caller to consult. This
executor closes that gap: every sub-agent action is routed through the registry
bounds BEFORE it reaches the Execution Fabric, so the invariants are *enforced*
on a live dispatch path rather than merely available.

For each sub-agent dispatch it:
  1. refuses (fail closed) if the agent — or any ancestor, or globally — is halted
     (corrigibility propagates to the subtree);
  2. refuses if the action's required authorization level exceeds the agent's
     ceiling (delegation cannot amplify authority — the sub-agent must escalate);
  3. on a successful dispatch, records each realized action against its target so
     cross-agent threshold-avoidance decomposition is detectable.
"""

from __future__ import annotations

from gap_kernel.execution.fabric import ExecutionFabric, ExecutionResult, KillSwitchEngaged
from gap_kernel.governance.multi_agent import SubAgentRegistry, SubAgentViolation
from gap_kernel.models.governance import GovernanceDecision
from gap_kernel.models.strategy import StrategyProposal


class SubAgentExecutor:
    """Enforces a sub-agent's registry bounds, then delegates to the fabric."""

    def __init__(self, registry: SubAgentRegistry, fabric: ExecutionFabric):
        self._registry = registry
        self._fabric = fabric

    def execute(
        self,
        agent_id: str,
        proposal: StrategyProposal,
        decision: GovernanceDecision,
    ) -> ExecutionResult:
        # 1. Corrigibility (enforced): a halt on this agent, any ancestor, or the
        #    whole system refuses dispatch — a sub-agent is contained with its tree.
        if self._registry.is_halted(agent_id):
            raise KillSwitchEngaged(
                f"Sub-agent '{agent_id}' is halted (self, an ancestor, or global); "
                f"no action will be dispatched."
            )

        # 2. Authority bound (enforced): the action's required authorization level
        #    must be within the agent's ceiling. A sub-agent's ceiling never exceeds
        #    its parent's, so this is where "delegation cannot amplify authority"
        #    bites at action time — the sub-agent must escalate instead. Fail closed
        #    if the decision carries no level: the ceiling cannot be verified, so the
        #    action is not cleared for autonomous sub-agent dispatch.
        level = decision.authorization_level
        if level is None:
            raise SubAgentViolation(
                f"Sub-agent '{agent_id}' decision has no authorization level; the "
                f"ceiling cannot be verified — refusing (fail closed)."
            )
        if not self._registry.authorize(agent_id, level):
            raise SubAgentViolation(
                f"Sub-agent '{agent_id}' (ceiling "
                f"{self._registry.ceiling_for(agent_id).value}) may not execute a "
                f"{level.value} action; it must escalate to a higher authority."
            )

        result = self._fabric.execute(proposal, decision)

        # 3. Record each REALIZED action (from the result's completed actions, not
        #    the declared proposal) for cross-agent decomposition detection. Gating
        #    on whole-proposal success and iterating proposal.actions would let an
        #    adversary suppress the detector by padding a proposal with a decoy
        #    failing action (flips success to False) while the real action still
        #    executes — and would over-record failed targets on a partial success.
        for completed in result.actions_completed:
            self._registry.record_action(agent_id, completed["target"], level)
        return result


__all__ = ["SubAgentExecutor"]
