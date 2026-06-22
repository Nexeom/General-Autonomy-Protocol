"""
Tests for Out-of-Band Authority Verification — cryptographic contract (Fix 4).

L2+ actions require a human approval *signature* over the specific Decision
Record ID, verified against a registered public key, and consumed in a
persistent replay ledger. These tests exercise the happy path plus the
adversarial cases the audit (SA-5) called out: forged signature, expired
approval, unknown approver key, and replay — including replay across a fresh
Execution Fabric that shares the ledger.
"""

from datetime import datetime, timedelta

import pytest

from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair, sign
from gap_kernel.execution.fabric import ExecutionFabric, OOBVerificationError
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.verification.oob_ledger import OOBLedger


KEY_ID = "human_approver_alice"


def _proposal(proposal_id="prop_oob", action_type="send_email", target="lead_001"):
    return StrategyProposal(
        id=proposal_id,
        intent_id="intent_test",
        attempt_number=1,
        plan_description="Test proposal",
        actions=[
            PlannedAction(action_type=action_type, target=target, parameters={}, risk_score=6)
        ],
        estimated_cost=0.10,
        rationale="Test",
        generated_at=datetime.utcnow(),
    )


def _world():
    return WorldModel(entities={}, last_reconciled=datetime.utcnow())


def _signed_decision(
    private_key_hex,
    *,
    decision_id="gov_oob_001",
    proposal_id="prop_oob",
    auth_level=AuthorizationLevel.L2,
    key_id=KEY_ID,
    valid_until=None,
    signature=None,
):
    """Build an L2+ decision carrying a (by default valid) OOB approval signature."""
    valid_until = valid_until or (datetime.utcnow() + timedelta(minutes=5))
    message = f"{decision_id}:{valid_until.isoformat()}"
    sig = signature if signature is not None else sign(private_key_hex, message)
    return GovernanceDecision(
        id=decision_id,
        proposal_id=proposal_id,
        verdict=GovernanceVerdict.APPROVED,
        authorization_level=auth_level,
        temporal_context={},
        policy_snapshot={},
        evaluated_at=datetime.utcnow(),
        human_approval_signature=sig,
        human_approver_public_key_id=key_id,
        human_approval_timestamp=datetime.utcnow(),
        human_approval_valid_until=valid_until,
    )


@pytest.fixture
def keypair():
    return generate_keypair()  # (private_hex, public_hex)


@pytest.fixture
def fabric_with_key(keypair):
    """An ExecutionFabric whose registry trusts the test approver key."""
    _, public_hex = keypair
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    return ExecutionFabric(_world(), public_key_registry=registry)


# --- No OOB required below L2 ----------------------------------------------

@pytest.mark.parametrize("level", [AuthorizationLevel.L0, AuthorizationLevel.L1])
def test_below_l2_requires_no_oob(level):
    fabric = ExecutionFabric(_world())
    decision = GovernanceDecision(
        id="gov_low",
        proposal_id="prop_oob",
        verdict=GovernanceVerdict.APPROVED,
        authorization_level=level,
        temporal_context={},
        policy_snapshot={},
        evaluated_at=datetime.utcnow(),
    )
    result = fabric.execute(_proposal(), decision)
    assert result.success is True


# --- Happy path -------------------------------------------------------------

@pytest.mark.parametrize(
    "level", [AuthorizationLevel.L2, AuthorizationLevel.L3, AuthorizationLevel.L4]
)
def test_valid_signature_executes(keypair, level):
    private_hex, public_hex = keypair
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    fabric = ExecutionFabric(_world(), public_key_registry=registry)
    decision = _signed_decision(private_hex, auth_level=level)
    result = fabric.execute(_proposal(), decision)
    assert result.success is True


# --- Fail-closed adversarial cases -----------------------------------------

def test_missing_signature_rejected(fabric_with_key):
    decision = GovernanceDecision(
        id="gov_nosig",
        proposal_id="prop_oob",
        verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L2,
        temporal_context={},
        policy_snapshot={},
        evaluated_at=datetime.utcnow(),
    )
    with pytest.raises(OOBVerificationError, match="signature"):
        fabric_with_key.execute(_proposal(), decision)


def test_forged_signature_rejected(fabric_with_key, keypair):
    private_hex, _ = keypair
    # A well-formed but wrong 64-byte signature.
    forged = "00" * 64
    decision = _signed_decision(private_hex, signature=forged)
    with pytest.raises(OOBVerificationError, match="signature is invalid"):
        fabric_with_key.execute(_proposal(), decision)


def test_signature_from_unknown_key_rejected(keypair):
    # Approver signs with a key the fabric's registry does NOT trust.
    private_hex, _ = keypair
    fabric = ExecutionFabric(_world(), public_key_registry=PublicKeyRegistry())
    decision = _signed_decision(private_hex)
    with pytest.raises(OOBVerificationError, match="not registered"):
        fabric.execute(_proposal(), decision)


def test_expired_approval_rejected(fabric_with_key, keypair):
    private_hex, _ = keypair
    past = datetime.utcnow() - timedelta(minutes=1)
    decision = _signed_decision(private_hex, valid_until=past)
    with pytest.raises(OOBVerificationError, match="expired"):
        fabric_with_key.execute(_proposal(), decision)


def test_tampered_expiry_rejected(fabric_with_key, keypair):
    """Extending the validity window after signing must invalidate the signature
    (the expiry is bound into the signed message)."""
    private_hex, _ = keypair
    decision = _signed_decision(private_hex)
    # Push the expiry out without re-signing.
    decision.human_approval_valid_until = datetime.utcnow() + timedelta(days=365)
    with pytest.raises(OOBVerificationError, match="signature is invalid"):
        fabric_with_key.execute(_proposal(), decision)


# --- Replay protection ------------------------------------------------------

def test_replay_rejected_within_fabric(keypair):
    private_hex, public_hex = keypair
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    fabric = ExecutionFabric(_world(), public_key_registry=registry)
    decision = _signed_decision(private_hex)

    assert fabric.execute(_proposal("prop_a"), decision).success is True
    with pytest.raises(OOBVerificationError, match="already been used"):
        fabric.execute(_proposal("prop_b"), decision)


def test_replay_rejected_across_fresh_fabric(keypair):
    """A restarted fabric sharing the persistent ledger still rejects the replay."""
    private_hex, public_hex = keypair
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    ledger = OOBLedger()  # shared, persistent boundary

    fabric_a = ExecutionFabric(_world(), oob_ledger=ledger, public_key_registry=registry)
    fabric_b = ExecutionFabric(_world(), oob_ledger=ledger, public_key_registry=registry)
    decision = _signed_decision(private_hex)

    assert fabric_a.execute(_proposal("prop_a"), decision).success is True
    with pytest.raises(OOBVerificationError, match="already been used"):
        fabric_b.execute(_proposal("prop_b"), decision)


def test_distinct_decisions_each_execute(keypair):
    private_hex, public_hex = keypair
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    fabric = ExecutionFabric(_world(), public_key_registry=registry)

    d1 = _signed_decision(private_hex, decision_id="gov_x1")
    d2 = _signed_decision(private_hex, decision_id="gov_x2")
    assert fabric.execute(_proposal("prop_a"), d1).success is True
    assert fabric.execute(_proposal("prop_b"), d2).success is True


# --- Model ------------------------------------------------------------------

def test_model_has_crypto_oob_fields():
    decision = GovernanceDecision(
        id="gov_fields",
        proposal_id="prop",
        verdict=GovernanceVerdict.APPROVED,
        temporal_context={},
        policy_snapshot={},
        evaluated_at=datetime.utcnow(),
        human_approval_signature="deadbeef",
        human_approver_public_key_id=KEY_ID,
        human_approval_timestamp=datetime.utcnow(),
        human_approval_valid_until=datetime.utcnow() + timedelta(minutes=5),
    )
    assert decision.human_approval_signature == "deadbeef"
    assert decision.human_approver_public_key_id == KEY_ID
    assert decision.human_approval_valid_until is not None
