"""Phase E — decision integrity / structural boundary (Fix 2).

Every GovernanceDecision is Ed25519-signed by the kernel. When the Execution
Fabric is configured with the kernel's public key it refuses any decision that
is unsigned, forged, or tampered — so an in-process agent cannot mint or alter
an approval without the kernel's private key, which it does not hold.
"""

from datetime import datetime, timedelta

import pytest

from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair, sign, verify
from gap_kernel.execution.fabric import ExecutionError, ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
    canonical_decision_payload,
)
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel


def _proposal(risk=1, pid="prop_e"):
    return StrategyProposal(
        id=pid,
        intent_id="i1",
        attempt_number=1,
        plan_description="p",
        actions=[PlannedAction(action_type="query_crm", target="t1", parameters={}, risk_score=risk)],
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


def test_kernel_signs_every_decision():
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    assert decision.decision_signature
    assert verify(
        kernel.public_key_hex,
        canonical_decision_payload(decision),
        decision.decision_signature,
    )


def test_fabric_executes_kernel_signed_decision():
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    assert decision.verdict == GovernanceVerdict.APPROVED
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    assert fabric.execute(_proposal(), decision).success is True


def test_fabric_rejects_unsigned_decision():
    kernel = GovernanceKernel()
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    forged = GovernanceDecision(
        id="gov_forge", proposal_id="prop_e", verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L0, temporal_context={},
        policy_snapshot={}, evaluated_at=datetime.utcnow(),
    )
    with pytest.raises(ExecutionError, match="unsigned"):
        fabric.execute(_proposal(), forged)


def test_fabric_rejects_forged_signature():
    kernel = GovernanceKernel()
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    forged = GovernanceDecision(
        id="gov_forge", proposal_id="prop_e", verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L0, temporal_context={},
        policy_snapshot={}, evaluated_at=datetime.utcnow(),
        decision_signature="00" * 64,
    )
    with pytest.raises(ExecutionError, match="invalid|forgery"):
        fabric.execute(_proposal(), forged)


def test_fabric_rejects_tampered_decision():
    """An agent altering a kernel-signed decision (e.g. to retarget it) is caught."""
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    decision.proposal_id = "some_other_proposal"  # tamper after signing
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    with pytest.raises(ExecutionError, match="invalid|forgery"):
        fabric.execute(_proposal(), decision)


def test_decision_from_different_kernel_rejected():
    """A decision signed by some *other* kernel key is not trusted."""
    trusted = GovernanceKernel()
    rogue = GovernanceKernel()
    decision = rogue.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=trusted.public_key_hex)
    with pytest.raises(ExecutionError, match="invalid|forgery"):
        fabric.execute(_proposal(), decision)


def test_kernel_signature_survives_downstream_oob_approval():
    """The kernel signature must still verify after the human approval fields are
    added downstream (they are excluded from the kernel's signed payload)."""
    kernel_priv, kernel_pub = generate_keypair()
    decision = GovernanceDecision(
        id="gov_l2e", proposal_id="prop_e", verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L2, temporal_context={},
        policy_snapshot={}, evaluated_at=datetime.utcnow(),
    )
    decision.decision_signature = sign(kernel_priv, canonical_decision_payload(decision))

    # Human OOB approval, added AFTER the kernel signed.
    approver_priv, approver_pub = generate_keypair()
    valid_until = datetime.utcnow() + timedelta(minutes=5)
    decision.human_approval_signature = sign(
        approver_priv, f"{decision.id}:{valid_until.isoformat()}"
    )
    decision.human_approver_public_key_id = "alice"
    decision.human_approval_timestamp = datetime.utcnow()
    decision.human_approval_valid_until = valid_until

    fabric = ExecutionFabric(
        _world(),
        kernel_public_key_hex=kernel_pub,
        public_key_registry=PublicKeyRegistry({"alice": approver_pub}),
    )
    assert fabric.execute(_proposal(), decision).success is True
