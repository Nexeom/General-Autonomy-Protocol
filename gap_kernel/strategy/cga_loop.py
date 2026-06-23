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

from gap_kernel.errors import GovernanceConfigError
from gap_kernel.execution.fabric import _OOB_REQUIRED_LEVELS, ExecutionFabric
from gap_kernel.governance.action_classifier import ActionTypeClassifier
from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.sir import StructuredIntentResolver
from gap_kernel.models.execution import ExecutionResult
from gap_kernel.models.sir import IntentDeclaration, StandingIntentDeclaration
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
)
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
        intent_resolver: Optional[StructuredIntentResolver] = None,
        integrity_monitor: Optional[GovernanceIntegrityMonitor] = None,
        governed: bool = False,
        action_type_classifier: Optional[ActionTypeClassifier] = None,
        block_on_integrity: bool = False,
        kill_switch: Optional[KillSwitch] = None,
    ):
        self.governance = governance_kernel
        self.execution = execution_fabric
        self.strategy_gen = strategy_generator or RuleBasedStrategyGenerator()
        self.max_attempts = max_attempts
        # SIR (intent-transfer governance) and GIM (integrity monitoring) are
        # optional, opt-in hooks. When a resolver is needed it is created lazily.
        self.intent_resolver = intent_resolver
        self.integrity_monitor = integrity_monitor
        # When set, derives a governance action_type_id from each proposal
        # (operational -> governance), overriding any action_type_id passed to run.
        self.action_type_classifier = action_type_classifier
        # Governed mode makes the SIR intent-transfer gate mandatory: run() must
        # be given a resolved intent declaration, or it refuses (fail closed).
        self._governed = governed
        # When set (on by default in governed mode), an approved action is HELD
        # and escalated to a human if GIM flags authorization drift or
        # threshold-avoidance decomposition for it — making integrity signals
        # consequential rather than advisory.
        self._block_on_integrity = block_on_integrity or governed
        # Corrigibility: when this human-controlled kill-switch is engaged, the
        # loop refuses to plan or execute — CGA does NOT route around a halt.
        self.kill_switch = kill_switch

    def run(
        self,
        intent: IntentVector,
        drift_event: dict,
        world_state: WorldModel,
        intents: Optional[List[IntentVector]] = None,
        intent_declaration: Optional[IntentDeclaration] = None,
        standing: Optional[StandingIntentDeclaration] = None,
        action_type_id: Optional[str] = None,
    ) -> CGAResult:
        """
        Run the full CGA loop for a drift event.

        If an ``intent_declaration`` is supplied (SIR), the loop will not engage
        until that intent is resolved — confirmed/corrected for L1+, or backed by
        a valid standing declaration for L0 — surfacing ``awaiting_intent_confirmation``
        otherwise. If an integrity monitor is configured (GIM), every decision is
        observed and any signals are returned on the result.

        Returns a CGAResult containing all proposals, decisions, and the outcome.
        """
        if intents is None:
            intents = [intent]

        # Corrigibility halt (checked first): a halt stops the system. CGA does
        # not plan or execute, and does not negotiate a path around it. The check
        # is scope-aware and symmetric with the Execution Fabric: a global halt,
        # or a per-scope halt covering the entity this run is about to act on,
        # short-circuits BEFORE any planning (the strategy layer is never asked
        # to find a path around the halt — including by retargeting).
        if self.kill_switch is not None and self._is_halted_for(drift_event):
            return CGAResult(
                intent=intent, drift_event=drift_event, proposals=[], decisions=[],
                accumulated_constraints=[], final_verdict="halted",
                approved_proposal=None, execution_result=None, total_attempts=0,
                escalated=False, integrity_signals=[],
            )

        # Governed mode: the SIR intent-transfer gate is mandatory.
        if self._governed and intent_declaration is None:
            raise GovernanceConfigError(
                "A governed CGA loop requires a resolved intent declaration "
                "(SIR) before it will engage; refusing to plan ungoverned."
            )

        # SIR gate: govern the intent-transfer moment before any action is planned.
        if intent_declaration is not None:
            resolver = self.intent_resolver or StructuredIntentResolver()
            if not resolver.is_ready_for_cga(intent_declaration, standing=standing):
                return CGAResult(
                    intent=intent,
                    drift_event=drift_event,
                    proposals=[],
                    decisions=[],
                    accumulated_constraints=[],
                    final_verdict="awaiting_intent_confirmation",
                    approved_proposal=None,
                    execution_result=None,
                    total_attempts=0,
                    escalated=False,
                    integrity_signals=[],
                )

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

            # 2. Submit to governance. Classify the proposal into a governance
            #    action type (operational -> governance) when a classifier is set.
            proposal_action_type_id = (
                self.action_type_classifier.classify(proposal)
                if self.action_type_classifier is not None
                else action_type_id
            )
            decision = self.governance.evaluate_proposal(
                proposal=proposal,
                intents=intents,
                world_state=world_state,
                action_type_id=proposal_action_type_id,
            )
            decisions.append(decision)

            # GIM: feed the decision to the integrity monitor (drift / decomposition).
            if self.integrity_monitor is not None:
                target = proposal.actions[0].target if proposal.actions else None
                self.integrity_monitor.observe_decision(decision, target=target)

            # 3. Route based on verdict
            if decision.verdict == GovernanceVerdict.APPROVED:
                approved_proposal = proposal

                # 3a. Integrity hold (GIM consequential): if the monitor flags
                #     authorization drift or threshold-avoidance decomposition for
                #     this action, do NOT execute — hold and escalate to a human.
                if self._block_on_integrity and self._integrity_hold(decision, proposal):
                    final_verdict = "integrity_hold"
                    break

                # 4. L2+ ("Approve Before" and above) must not auto-execute — a
                #    human Out-of-Band approval is required first. Surface these
                #    as awaiting approval; the operator obtains a signature off
                #    channel and calls approve_and_execute(). Only L0/L1 dispatch
                #    autonomously here.
                if decision.authorization_level in _OOB_REQUIRED_LEVELS:
                    final_verdict = "awaiting_approval"
                    break

                # 4b. Corrigibility re-check immediately before dispatch: a halt
                #     engaged mid-cycle, or a proposal whose action targets a
                #     halted scope, yields a clean "halted" result here rather
                #     than letting the Execution Fabric raise. The loop does NOT
                #     re-plan to a different target to route around the halt.
                if self.kill_switch is not None and (
                    self.kill_switch.is_engaged()
                    or any(self.kill_switch.is_engaged(a.target) for a in proposal.actions)
                ):
                    final_verdict = "halted"
                    approved_proposal = None
                    break

                final_verdict = "approved"
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
            integrity_signals=(
                self.integrity_monitor.scan() if self.integrity_monitor is not None else []
            ),
        )

    def _is_halted_for(self, drift_event: dict) -> bool:
        """True if the kill-switch is engaged globally, or for the scope this run
        would act on (the drift's entity), so the loop can refuse to plan BEFORE
        generating a proposal — symmetric with the Execution Fabric's per-target
        check, and closing the retarget-around-a-halt path.
        """
        if self.kill_switch is None:
            return False
        if self.kill_switch.is_engaged():
            return True
        if isinstance(drift_event, dict):
            for key in ("entity_id", "target", "scope"):
                scope = drift_event.get(key)
                if scope and self.kill_switch.is_engaged(scope):
                    return True
        return False

    def _integrity_hold(self, decision: GovernanceDecision, proposal: StrategyProposal) -> bool:
        """True if GIM flags drift or decomposition for this (already-observed) action."""
        if self.integrity_monitor is None:
            return False
        action_type = decision.action_type_id or "unspecified"
        if self.integrity_monitor.check_authorization_drift(action_type) is not None:
            return True
        target = proposal.actions[0].target if proposal.actions else None
        if target and self.integrity_monitor.check_decomposition(target) is not None:
            return True
        return False

    def approve_and_execute(
        self,
        proposal: StrategyProposal,
        decision: GovernanceDecision,
        *,
        human_approval_signature: str,
        approver_key_id: str,
        valid_until: datetime,
        timestamp: Optional[datetime] = None,
    ) -> ExecutionResult:
        """Supply side of Fix 4 / Phase H.

        Attach a human Out-of-Band approval — a signature obtained off-channel
        over ``"<decision id>:<valid_until ISO>"`` — to an L2+ decision the loop
        surfaced as ``awaiting_approval``, then dispatch it for execution. The
        Execution Fabric verifies the signature; the loop never holds the
        approver's private key.
        """
        decision.human_approval_signature = human_approval_signature
        decision.human_approver_public_key_id = approver_key_id
        decision.human_approval_valid_until = valid_until
        decision.human_approval_timestamp = timestamp or datetime.utcnow()
        return self.execution.execute(proposal, decision)

    def close(self) -> None:
        """Release a governance handle that owns a resource (e.g. an isolated
        deployment's out-of-process kernel subprocess). A no-op for an in-process
        kernel, which has no ``close()``. Lets a governed loop be used as a context
        manager: ``with build_governed_deployment(...) as loop: ...``."""
        closer = getattr(self.governance, "close", None)
        if callable(closer):
            closer()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


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
        integrity_signals: Optional[list] = None,
    ):
        self.intent = intent
        self.drift_event = drift_event
        self.proposals = proposals
        self.decisions = decisions
        self.accumulated_constraints = accumulated_constraints
        self.final_verdict = final_verdict
        self.approved_proposal = approved_proposal
        self.integrity_signals = integrity_signals if integrity_signals is not None else []
        self.execution_result = execution_result
        self.total_attempts = total_attempts
        self.escalated = escalated

    @property
    def awaiting_approval(self) -> bool:
        """True when an L2+ action was approved by governance but is held pending a
        human Out-of-Band approval — it must reach a human, not be silently dropped."""
        return self.final_verdict == "awaiting_approval"

    @property
    def integrity_hold(self) -> bool:
        """True when an approved action was HELD because GIM flagged a governance-
        integrity signal (drift / threshold-avoidance) for it — routed to a human."""
        return self.final_verdict == "integrity_hold"

    @property
    def halted(self) -> bool:
        """True when a human-engaged kill-switch halted the loop. No planning,
        no proposals, no execution — corrigibility takes precedence over the
        loop's disposition to find a path to yes."""
        return self.final_verdict == "halted"

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

        # Aggregate uncertainty from the final governance decision
        final_uncertainty = None
        for decision in reversed(self.decisions):
            if decision.uncertainty:
                final_uncertainty = decision.uncertainty
                break

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
            uncertainty=final_uncertainty,
        )
