"""
Tests for Out-of-Band Authority Verification (Added 2026-02-23).

Test cases:
1. L0 and L1 actions do not require OOB verification
2. L2 actions require verification_method field
3. L3 and L4 actions require verification_method field
4. Authorization without verification at L2+ is rejected
5. Verification details recorded in Decision Record
6. Replay attempt (same verification for different Decision Record) is rejected
"""

from datetime import datetime

import pytest

from gap_kernel.execution.fabric import (
    ExecutionFabric,
    ExecutionError,
    OOBVerificationError,
)
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel


def _make_proposal(
    proposal_id="prop_oob_test",
    action_type="send_email",
    target="lead_001",
    risk_score=3,
) -> StrategyProposal:
    return StrategyProposal(
        id=proposal_id,
        intent_id="intent_test",
        attempt_number=1,
        plan_description="Test proposal",
        actions=[
            PlannedAction(
                action_type=action_type,
                target=target,
                parameters={},
                risk_score=risk_score,
            )
        ],
        estimated_cost=0.10,
        rationale="Test rationale",
        generated_at=datetime.utcnow(),
    )


def _make_decision(
    decision_id="gov_oob_test_001",
    proposal_id="prop_oob_test",
    auth_level=AuthorizationLevel.L0,
    verification_method=None,
    verification_channel=None,
    verified_at=None,
) -> GovernanceDecision:
    return GovernanceDecision(
        id=decision_id,
        proposal_id=proposal_id,
        verdict=GovernanceVerdict.APPROVED,
        authorization_level=auth_level,
        temporal_context={},
        policy_snapshot={},
        evaluated_at=datetime.utcnow(),
        authority_verification_method=verification_method,
        authority_verification_channel=verification_channel,
        authority_verified_at=verified_at,
    )


def _make_world_model() -> WorldModel:
    return WorldModel(
        entities={},
        last_reconciled=datetime.utcnow(),
    )


def _make_fabric() -> ExecutionFabric:
    return ExecutionFabric(_make_world_model())


class TestOOBAuthorityVerification:
    """Test suite for Out-of-Band Authority Verification."""

    def test_l0_no_oob_required(self):
        """1. L0 actions do not require OOB verification."""
        fabric = _make_fabric()
        proposal = _make_proposal()
        decision = _make_decision(auth_level=AuthorizationLevel.L0)

        # Should execute without verification fields
        result = fabric.execute(proposal, decision)
        assert result.success is True

    def test_l1_no_oob_required(self):
        """1. L1 actions do not require OOB verification."""
        fabric = _make_fabric()
        proposal = _make_proposal()
        decision = _make_decision(auth_level=AuthorizationLevel.L1)

        # Should execute without verification fields
        result = fabric.execute(proposal, decision)
        assert result.success is True

    def test_l2_requires_verification_method(self):
        """2. L2 actions require verification_method field."""
        fabric = _make_fabric()
        proposal = _make_proposal()
        decision = _make_decision(auth_level=AuthorizationLevel.L2)

        with pytest.raises(OOBVerificationError, match="verification_method"):
            fabric.execute(proposal, decision)

    def test_l3_requires_verification_method(self):
        """3. L3 actions require verification_method field."""
        fabric = _make_fabric()
        proposal = _make_proposal()
        decision = _make_decision(auth_level=AuthorizationLevel.L3)

        with pytest.raises(OOBVerificationError, match="verification_method"):
            fabric.execute(proposal, decision)

    def test_l4_requires_verification_method(self):
        """3. L4 actions require verification_method field."""
        fabric = _make_fabric()
        proposal = _make_proposal()
        decision = _make_decision(auth_level=AuthorizationLevel.L4)

        with pytest.raises(OOBVerificationError, match="verification_method"):
            fabric.execute(proposal, decision)

    def test_l2_without_channel_rejected(self):
        """4. Authorization without verification channel at L2+ is rejected."""
        fabric = _make_fabric()
        proposal = _make_proposal()
        decision = _make_decision(
            auth_level=AuthorizationLevel.L2,
            verification_method="hardware_key",
            # Missing verification_channel
        )

        with pytest.raises(OOBVerificationError, match="verification_channel"):
            fabric.execute(proposal, decision)

    def test_l2_with_full_verification_succeeds(self):
        """5. Verification details recorded — L2 with full verification executes."""
        fabric = _make_fabric()
        proposal = _make_proposal()
        decision = _make_decision(
            auth_level=AuthorizationLevel.L2,
            verification_method="hardware_key",
            verification_channel="independent_mfa",
            verified_at=datetime.utcnow(),
        )

        result = fabric.execute(proposal, decision)
        assert result.success is True

    def test_l3_with_full_verification_succeeds(self):
        """5. L3 with full verification executes successfully."""
        fabric = _make_fabric()
        proposal = _make_proposal()
        decision = _make_decision(
            decision_id="gov_l3_test_001",
            auth_level=AuthorizationLevel.L3,
            verification_method="biometric",
            verification_channel="physical_token",
            verified_at=datetime.utcnow(),
        )

        result = fabric.execute(proposal, decision)
        assert result.success is True

    def test_replay_attempt_rejected(self):
        """6. Replay attempt (same verification for different Decision Record) is rejected."""
        fabric = _make_fabric()

        # First execution succeeds
        proposal1 = _make_proposal(proposal_id="prop_first")
        decision1 = _make_decision(
            decision_id="gov_replay_test_001",
            proposal_id="prop_first",
            auth_level=AuthorizationLevel.L2,
            verification_method="hardware_key",
            verification_channel="independent_mfa",
            verified_at=datetime.utcnow(),
        )

        result1 = fabric.execute(proposal1, decision1)
        assert result1.success is True

        # Second execution with same decision ID (replay) should fail
        proposal2 = _make_proposal(proposal_id="prop_second")
        decision_replay = _make_decision(
            decision_id="gov_replay_test_001",  # Same ID = replay
            proposal_id="prop_second",
            auth_level=AuthorizationLevel.L2,
            verification_method="hardware_key",
            verification_channel="independent_mfa",
            verified_at=datetime.utcnow(),
        )

        with pytest.raises(OOBVerificationError, match="non-replayable"):
            fabric.execute(proposal2, decision_replay)

    def test_different_decisions_with_same_method_allowed(self):
        """Different decisions can use the same verification method."""
        fabric = _make_fabric()

        proposal1 = _make_proposal(proposal_id="prop_a")
        decision1 = _make_decision(
            decision_id="gov_unique_001",
            proposal_id="prop_a",
            auth_level=AuthorizationLevel.L2,
            verification_method="hardware_key",
            verification_channel="independent_mfa",
        )
        result1 = fabric.execute(proposal1, decision1)
        assert result1.success is True

        proposal2 = _make_proposal(proposal_id="prop_b")
        decision2 = _make_decision(
            decision_id="gov_unique_002",  # Different decision ID
            proposal_id="prop_b",
            auth_level=AuthorizationLevel.L2,
            verification_method="hardware_key",  # Same method is fine
            verification_channel="independent_mfa",
        )
        result2 = fabric.execute(proposal2, decision2)
        assert result2.success is True

    def test_governance_decision_has_oob_fields(self):
        """5. GovernanceDecision model has OOB verification fields."""
        decision = GovernanceDecision(
            id="gov_oob_fields_test",
            proposal_id="prop_test",
            verdict=GovernanceVerdict.APPROVED,
            temporal_context={},
            policy_snapshot={},
            evaluated_at=datetime.utcnow(),
            authority_verification_method="oob_code",
            authority_verification_channel="sms_independent",
            authority_verified_at=datetime.utcnow(),
        )

        assert decision.authority_verification_method == "oob_code"
        assert decision.authority_verification_channel == "sms_independent"
        assert decision.authority_verified_at is not None
