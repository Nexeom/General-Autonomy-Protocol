"""Integration — SIR readiness gate and GIM observation wired into the CGA loop."""

from datetime import datetime, timedelta

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.sir import StructuredIntentResolver
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.sir import StandingIntentDeclaration
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.strategy.cga_loop import CGALoop


class _Gen:
    def __init__(self, risk=1, target="t1"):
        self.risk, self.target = risk, target

    def generate(self, intent, world_state, drift_event, accumulated_constraints,
                 prior_proposals, attempt_number):
        return StrategyProposal(
            id=f"prop_{attempt_number}", intent_id=intent.id, attempt_number=attempt_number,
            plan_description="op",
            actions=[PlannedAction(action_type="query_crm", target=self.target, parameters={}, risk_score=self.risk)],
            estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
        )


def _intent():
    return IntentVector(id="i1", objective="o", priority=50, hard_constraints=[],
                        soft_constraints=[], created_by="t", created_at=datetime.utcnow())


def _world():
    return WorldModel(entities={}, last_reconciled=datetime.utcnow())


def _loop(**kw):
    kernel = GovernanceKernel()
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    return CGALoop(kernel, fabric, strategy_generator=_Gen(risk=1), **kw)


def test_loop_without_hooks_runs_normally():
    result = _loop().run(intent=_intent(), drift_event={}, world_state=_world())
    assert result.final_verdict == "approved"
    assert result.integrity_signals == []


# --- SIR gate ---------------------------------------------------------------

def test_sir_gate_blocks_until_confirmed():
    resolver = StructuredIntentResolver()
    decl = resolver.resolve("do the thing", AuthorizationLevel.L1)  # PENDING
    loop = _loop(intent_resolver=resolver)

    blocked = loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                       intent_declaration=decl)
    assert blocked.final_verdict == "awaiting_intent_confirmation"
    assert blocked.execution_result is None
    assert blocked.proposals == []

    proceeded = loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                         intent_declaration=resolver.confirm(decl))
    assert proceeded.final_verdict == "approved"


def test_sir_gate_l0_requires_valid_standing():
    resolver = StructuredIntentResolver()
    decl = resolver.resolve("routine sync", AuthorizationLevel.L0, intent_class="routine")
    loop = _loop(intent_resolver=resolver)

    blocked = loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                       intent_declaration=decl)  # no standing
    assert blocked.final_verdict == "awaiting_intent_confirmation"

    # A valid standing covering the same intent class (its own intent confirmed).
    standing_decl = resolver.confirm(
        resolver.resolve("routine sync", AuthorizationLevel.L0, intent_class="routine")
    )
    standing = StandingIntentDeclaration(
        standing_id="s1", intent_class="routine", declaration=standing_decl,
        authored_by="ops_lead", expires_at=datetime.utcnow() + timedelta(days=1),
    )
    ok = loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                  intent_declaration=decl, standing=standing)
    assert ok.final_verdict == "approved"


# --- GIM observation --------------------------------------------------------

def test_gim_observes_decisions_and_surfaces_signals():
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=1)
    result = _loop(integrity_monitor=monitor).run(
        intent=_intent(), drift_event={}, world_state=_world()
    )
    assert any(s.signal_type == "GIM-3" for s in result.integrity_signals)
