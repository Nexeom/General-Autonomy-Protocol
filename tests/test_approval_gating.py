"""Phase H — L2+ approval gating (Fix 4 supply side).

The CGA loop must not auto-execute L2+ ("Approve Before" and above) decisions.
It surfaces them as ``awaiting_approval``; an operator obtains a human Out-of-Band
approval signature off-channel and calls ``approve_and_execute`` to dispatch.
"""

from datetime import datetime, timedelta

import pytest

from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair, sign
from gap_kernel.execution.fabric import ExecutionFabric, OOBVerificationError
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.strategy.cga_loop import CGALoop


class _FixedRiskGenerator:
    def __init__(self, risk):
        self.risk = risk

    def generate(self, intent, world_state, drift_event, accumulated_constraints,
                 prior_proposals, attempt_number):
        return StrategyProposal(
            id=f"prop_r{self.risk}",
            intent_id=intent.id,
            attempt_number=attempt_number,
            plan_description="op",
            actions=[
                PlannedAction(action_type="query_crm", target="t1", parameters={}, risk_score=self.risk)
            ],
            estimated_cost=0.01,
            rationale="r",
            generated_at=datetime.utcnow(),
        )


def _intent():
    return IntentVector(
        id="i1", objective="o", priority=50, hard_constraints=[], soft_constraints=[],
        created_by="t", created_at=datetime.utcnow(),
    )


def _world():
    return WorldModel(entities={}, last_reconciled=datetime.utcnow())


def test_l0_approval_executes_autonomously():
    loop = CGALoop(GovernanceKernel(), ExecutionFabric(_world(), allow_unsigned_decisions=True),
                   strategy_generator=_FixedRiskGenerator(risk=1))
    result = loop.run(intent=_intent(), drift_event={}, world_state=_world())
    assert result.final_verdict == "approved"
    assert result.execution_result is not None


def test_l2_approval_is_not_auto_executed():
    loop = CGALoop(GovernanceKernel(), ExecutionFabric(_world()),
                   strategy_generator=_FixedRiskGenerator(risk=6))
    result = loop.run(intent=_intent(), drift_event={}, world_state=_world())
    assert result.final_verdict == "awaiting_approval"
    assert result.awaiting_approval is True
    assert result.execution_result is None
    assert result.decisions[-1].authorization_level == AuthorizationLevel.L2


def _l2_loop_with_approver():
    kernel = GovernanceKernel()
    approver_priv, approver_pub = generate_keypair()
    fabric = ExecutionFabric(
        _world(),
        kernel_public_key_hex=kernel.public_key_hex,
        public_key_registry=PublicKeyRegistry({"alice": approver_pub}),
    )
    loop = CGALoop(kernel, fabric, strategy_generator=_FixedRiskGenerator(risk=6))
    result = loop.run(intent=_intent(), drift_event={}, world_state=_world())
    assert result.final_verdict == "awaiting_approval"
    return loop, result, approver_priv


def test_approve_and_execute_completes_l2_with_valid_signature():
    loop, result, approver_priv = _l2_loop_with_approver()
    decision = result.decisions[-1]
    valid_until = datetime.utcnow() + timedelta(minutes=5)
    # Sign the fabric's canonical OOB message (binds decision/proposal/level/approver/expiry).
    decision.human_approver_public_key_id = "alice"
    decision.human_approval_valid_until = valid_until
    signature = sign(approver_priv, ExecutionFabric._oob_signed_message(decision))

    exec_result = loop.approve_and_execute(
        result.approved_proposal,
        decision,
        human_approval_signature=signature,
        approver_key_id="alice",
        valid_until=valid_until,
    )
    assert exec_result.success is True


def test_approve_and_execute_rejects_invalid_signature():
    loop, result, _ = _l2_loop_with_approver()
    decision = result.decisions[-1]
    valid_until = datetime.utcnow() + timedelta(minutes=5)
    with pytest.raises(OOBVerificationError):
        loop.approve_and_execute(
            result.approved_proposal,
            decision,
            human_approval_signature="00" * 64,  # not a valid approval
            approver_key_id="alice",
            valid_until=valid_until,
        )
