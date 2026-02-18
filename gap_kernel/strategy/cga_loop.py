"""
CGA Loop — Constraint-Guided Autonomy state machine.

This is the defining behavior of GAP:
  - On governance rejection, parse the reason, reformulate, retry
  - On budget exhaustion, escalate to human with full lineage
  - Strategy → Governance → (approve|reject→retry|escalate)

The CGA loop is implemented as a pure Python state machine for testability.
A LangGraph wrapper is provided separately for production use with
LLM-powered strategy generation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Protocol
from uuid import uuid4

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.execution import ExecutionResult
from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.lineage import LineageRecord
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel


class StrategyGenerator(Protocol):
    """Protocol for strategy generation — pluggable backend."""

    def generate(
        self,
        intent: IntentVector,
        world_state: WorldModel,
        drift_event: dict,
        accumulated_constraints: List[dict],
        prior_proposals: List[StrategyProposal],
        attempt_number: int,
    ) -> StrategyProposal: ...


class RuleBasedStrategyGenerator:
    """
    Rule-based strategy generator for the prototype.
    Generates strategies using a deterministic rule engine rather than LLM.
    """

    def __init__(self):
        self._rules: List[Callable] = []
        self._register_default_rules()

    def _register_default_rules(self) -> None:
        """Register default strategy generation rules."""
        self._rules = [
            self._rule_direct_automated_outreach,
            self._rule_query_then_outreach,
            self._rule_human_handoff,
        ]

    def generate(
        self,
        intent: IntentVector,
        world_state: WorldModel,
        drift_event: dict,
        accumulated_constraints: List[dict],
        prior_proposals: List[StrategyProposal],
        attempt_number: int,
    ) -> StrategyProposal:
        """Generate a strategy proposal using deterministic rules."""
        # Use the attempt number to index into progressively safer strategies
        rule_index = min(attempt_number - 1, len(self._rules) - 1)

        # Check accumulated constraints to skip strategies that will fail
        effective_index = rule_index
        for i in range(rule_index, len(self._rules)):
            if not self._would_violate_accumulated(i, accumulated_constraints):
                effective_index = i
                break
        else:
            effective_index = len(self._rules) - 1  # Fall back to safest

        rule = self._rules[effective_index]
        actions = rule(intent, world_state, drift_event, accumulated_constraints)

        # Compute estimated cost
        cost = self._estimate_cost(actions)

        proposal = StrategyProposal(
            id=f"prop_{uuid4().hex[:12]}",
            intent_id=intent.id,
            attempt_number=attempt_number,
            plan_description=self._describe_plan(actions),
            actions=actions,
            estimated_cost=cost,
            rationale=self._build_rationale(
                attempt_number, accumulated_constraints, actions
            ),
            prior_rejection_id=(
                prior_proposals[-1].id if prior_proposals else None
            ),
            generated_at=datetime.utcnow(),
        )
        return proposal

    def _would_violate_accumulated(
        self, rule_index: int, accumulated_constraints: List[dict]
    ) -> bool:
        """Check if a rule would violate known accumulated constraints."""
        constraint_names = [
            c.get("constraint", "") for c in accumulated_constraints
        ]
        if rule_index == 0:
            # Direct outreach is blocked by GDPR
            return any("gdpr" in c.lower() for c in constraint_names)
        if rule_index == 1:
            # Query-then-outreach is blocked if no consent exists
            return any("no consent" in c.lower() or "no_consent" in c.lower()
                       for c in constraint_names)
        return False

    def _rule_direct_automated_outreach(
        self,
        intent: IntentVector,
        world_state: WorldModel,
        drift_event: dict,
        accumulated_constraints: List[dict],
    ) -> List[PlannedAction]:
        """Strategy 1: Direct automated email outreach."""
        target = drift_event.get("entity_id", "unknown")
        return [
            PlannedAction(
                action_type="send_email",
                target=target,
                parameters={
                    "template": "high_value_lead_response",
                    "personalized": True,
                },
                requires_consent=False,
                reversible=True,
                risk_score=3,
            )
        ]

    def _rule_query_then_outreach(
        self,
        intent: IntentVector,
        world_state: WorldModel,
        drift_event: dict,
        accumulated_constraints: List[dict],
    ) -> List[PlannedAction]:
        """Strategy 2: Query CRM for consent, then send if valid."""
        target = drift_event.get("entity_id", "unknown")
        return [
            PlannedAction(
                action_type="query_crm",
                target=target,
                parameters={"fields": ["gdpr_consent", "contact_preferences"]},
                requires_consent=False,
                reversible=True,
                risk_score=1,
            ),
            PlannedAction(
                action_type="send_email",
                target=target,
                parameters={
                    "template": "high_value_lead_response",
                    "conditional": "if_consent_verified",
                },
                requires_consent=True,
                reversible=True,
                risk_score=3,
            ),
        ]

    def _rule_human_handoff(
        self,
        intent: IntentVector,
        world_state: WorldModel,
        drift_event: dict,
        accumulated_constraints: List[dict],
    ) -> List[PlannedAction]:
        """Strategy 3: Route to human sales rep with context brief."""
        target = drift_event.get("entity_id", "unknown")
        return [
            PlannedAction(
                action_type="route_to_human",
                target=target,
                parameters={
                    "queue": "sales_queue",
                    "context": {
                        "reason": "GDPR compliance requires human-initiated first contact",
                        "consent_capture_form": True,
                        "sla_remaining_minutes": drift_event.get("sla_remaining_minutes", 2),
                    },
                    "priority": "urgent",
                },
                requires_consent=False,
                reversible=True,
                risk_score=2,
            )
        ]

    def _estimate_cost(self, actions: List[PlannedAction]) -> float:
        """Estimate the cost of executing a list of actions."""
        cost_map = {
            "send_email": 0.10,
            "send_sms": 0.15,
            "query_crm": 0.05,
            "route_to_human": 5.00,
            "automated_outreach": 0.20,
            "direct_call": 1.00,
            "update_record": 0.02,
        }
        return sum(cost_map.get(a.action_type, 0.50) for a in actions)

    def _describe_plan(self, actions: List[PlannedAction]) -> str:
        """Create a human-readable plan description."""
        steps = [
            f"{i+1}. {a.action_type} → {a.target}" for i, a in enumerate(actions)
        ]
        return "; ".join(steps)

    def _build_rationale(
        self,
        attempt: int,
        accumulated_constraints: List[dict],
        actions: List[PlannedAction],
    ) -> str:
        """Build a rationale string for the strategy choice."""
        if attempt == 1:
            return "First attempt: direct automated approach for fastest SLA compliance."
        constraint_summary = ", ".join(
            c.get("constraint", "unknown") for c in accumulated_constraints
        )
        action_types = ", ".join(a.action_type for a in actions)
        return (
            f"Attempt {attempt}: adapted strategy to avoid "
            f"[{constraint_summary}]. Using [{action_types}]."
        )


class CGALoop:
    """
    The Constraint-Guided Autonomy loop — the heartbeat of GAP.

    States:
      GENERATE → EVALUATE → (DISPATCH | REFORMULATE | ESCALATE)
    """

    def __init__(
        self,
        governance_kernel: GovernanceKernel,
        execution_fabric: ExecutionFabric,
        strategy_generator: Optional[StrategyGenerator] = None,
        max_attempts: int = 3,
    ):
        self.governance = governance_kernel
        self.execution = execution_fabric
        self.strategy_gen = strategy_generator or RuleBasedStrategyGenerator()
        self.max_attempts = max_attempts

    def run(
        self,
        intent: IntentVector,
        drift_event: dict,
        world_state: WorldModel,
        intents: Optional[List[IntentVector]] = None,
    ) -> CGAResult:
        """
        Run the full CGA loop for a drift event.

        Returns a CGAResult containing all proposals, decisions,
        and the final outcome.
        """
        if intents is None:
            intents = [intent]

        proposals: List[StrategyProposal] = []
        decisions: List[GovernanceDecision] = []
        accumulated_constraints: List[dict] = []
        attempt = 0
        final_verdict = "pending"
        approved_proposal: Optional[StrategyProposal] = None
        execution_result: Optional[ExecutionResult] = None

        while attempt < self.max_attempts:
            attempt += 1

            # 1. Generate strategy
            proposal = self.strategy_gen.generate(
                intent=intent,
                world_state=world_state,
                drift_event=drift_event,
                accumulated_constraints=accumulated_constraints,
                prior_proposals=proposals,
                attempt_number=attempt,
            )
            proposals.append(proposal)

            # 2. Submit to governance
            decision = self.governance.evaluate_proposal(
                proposal=proposal,
                intents=intents,
                world_state=world_state,
            )
            decisions.append(decision)

            # 3. Route based on verdict
            if decision.verdict == GovernanceVerdict.APPROVED:
                final_verdict = "approved"
                approved_proposal = proposal

                # 4. Dispatch to execution
                execution_result = self.execution.execute(proposal, decision)
                break

            elif decision.verdict == GovernanceVerdict.ESCALATE:
                final_verdict = "escalated"
                break

            else:
                # REJECTED — this is where CGA happens
                accumulated_constraints.append({
                    "source": f"governance_rejection_{decision.id}",
                    "constraint": decision.rejection_reason or "",
                    "detail": decision.rejection_detail or "",
                })
                # Loop continues to next attempt

        else:
            # Budget exhausted without approval
            final_verdict = "escalated"

        return CGAResult(
            intent=intent,
            drift_event=drift_event,
            proposals=proposals,
            decisions=decisions,
            accumulated_constraints=accumulated_constraints,
            final_verdict=final_verdict,
            approved_proposal=approved_proposal,
            execution_result=execution_result,
            total_attempts=attempt,
            escalated=(final_verdict == "escalated"),
        )


class CGAResult:
    """Result of a complete CGA loop execution."""

    def __init__(
        self,
        intent: IntentVector,
        drift_event: dict,
        proposals: List[StrategyProposal],
        decisions: List[GovernanceDecision],
        accumulated_constraints: List[dict],
        final_verdict: str,
        approved_proposal: Optional[StrategyProposal],
        execution_result: Optional[ExecutionResult],
        total_attempts: int,
        escalated: bool,
    ):
        self.intent = intent
        self.drift_event = drift_event
        self.proposals = proposals
        self.decisions = decisions
        self.accumulated_constraints = accumulated_constraints
        self.final_verdict = final_verdict
        self.approved_proposal = approved_proposal
        self.execution_result = execution_result
        self.total_attempts = total_attempts
        self.escalated = escalated

    def build_lineage_record(
        self,
        cycle_id: str,
        world_state_snapshot: dict,
    ) -> LineageRecord:
        """Build a complete lineage record from the CGA loop result."""
        now = datetime.utcnow()

        # Detect conflict resolution info
        conflicting = None
        deprioritized = None
        deprioritization_rationale = None
        priority_override = False

        for decision in self.decisions:
            if decision.verdict == GovernanceVerdict.APPROVED:
                # Check soft constraint violations as potential deprioritizations
                if decision.violated_constraints:
                    priority_override = True
                    deprioritized = ", ".join(decision.violated_constraints)
                    deprioritization_rationale = (
                        f"Soft constraints deprioritized to serve intent "
                        f"{self.intent.id} (priority {self.intent.priority})"
                    )

        return LineageRecord(
            id=f"lin_{uuid4().hex[:12]}",
            cycle_id=cycle_id,
            intent=self.intent,
            drift_detected=self.drift_event.get("description", "unknown drift"),
            drift_severity=self.drift_event.get("severity", 5),
            world_state_snapshot=world_state_snapshot,
            proposals=self.proposals,
            governance_decisions=self.decisions,
            final_approved_proposal=(
                self.approved_proposal.id if self.approved_proposal else None
            ),
            execution_result=(
                self.execution_result.model_dump(mode="json")
                if self.execution_result
                else None
            ),
            execution_success=(
                self.execution_result.success if self.execution_result else False
            ),
            total_attempts=self.total_attempts,
            escalated_to_human=self.escalated,
            resolved_at=now,
            priority_override_applied=priority_override,
            deprioritized_intent=deprioritized,
            deprioritization_rationale=deprioritization_rationale,
        )
