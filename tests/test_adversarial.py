"""Phase G — adversarial assurance (SA-1 / SA-5).

The audit's headline safety concern about Constraint-Guided Autonomy: because
the system is engineered to "find a path to yes", it must be impossible to
*negotiate around a hard constraint*. CGA may only re-plan within bounds; if no
compliant plan exists it must escalate, never execute.
"""

from datetime import datetime

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.strategy.cga_loop import CGALoop


class _PersistentlyViolatingGenerator:
    """Always proposes contacting an EU lead without consent — a HARD violation,
    however the rejection reasons accumulate."""

    def generate(self, intent, world_state, drift_event, accumulated_constraints,
                 prior_proposals, attempt_number):
        return StrategyProposal(
            id=f"prop_{attempt_number}",
            intent_id=intent.id,
            attempt_number=attempt_number,
            plan_description="email the EU lead",
            actions=[
                PlannedAction(action_type="send_email", target="eu_lead", parameters={}, risk_score=2)
            ],
            estimated_cost=0.01,
            rationale="just send it",
            generated_at=datetime.utcnow(),
        )


def test_cga_cannot_negotiate_around_hard_constraint():
    intent = IntentVector(
        id="i1",
        objective="contact high-value leads",
        priority=80,
        hard_constraints=[
            Constraint(
                name="gdpr_consent_required",
                type=ConstraintType.HARD,
                description="Must verify GDPR consent before contacting EU leads",
            )
        ],
        soft_constraints=[],
        created_by="t",
        created_at=datetime.utcnow(),
    )
    world = WorldModel(
        entities={
            "eu_lead": EntityState(
                entity_type="lead",
                entity_id="eu_lead",
                properties={"geo": "EU", "gdpr_consent": False},
                last_updated=datetime.utcnow(),
                source="t",
            )
        },
        last_reconciled=datetime.utcnow(),
    )
    loop = CGALoop(
        GovernanceKernel(),
        ExecutionFabric(world),
        strategy_generator=_PersistentlyViolatingGenerator(),
        max_attempts=3,
    )
    result = loop.run(intent=intent, drift_event={}, world_state=world, intents=[intent])

    # The loop exhausts its attempts and escalates — it never approves or executes.
    assert result.final_verdict == "escalated"
    assert result.execution_result is None
    assert result.total_attempts == 3
    assert all(d.verdict == GovernanceVerdict.REJECTED for d in result.decisions)
    assert all("gdpr_consent_required" in d.violated_constraints for d in result.decisions)
