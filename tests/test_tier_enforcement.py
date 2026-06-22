"""Phase C — Policy Tier enforcement (Fix 3).

The Tier-1 regulatory floor is loaded from a *signed* Applicability Profile and
verified on kernel construction. These tests assert:
  * a valid signed profile's Tier-1 constraint is enforced on every evaluation,
    even when no intent declares it (the floor is always active);
  * an unsigned, tampered, or unknown-key profile is refused (fail closed);
  * the authorization comparator (granted >= required) is rank-based.
"""

from datetime import datetime

import pytest

from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.governance.kernel import (
    GovernanceKernel,
    _max_auth,
    _satisfies_auth,
)
from gap_kernel.governance.profile import (
    ApplicabilityProfile,
    ProfileVerificationError,
    sign_profile,
)
from gap_kernel.models.governance import AuthorizationLevel, GovernanceVerdict
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyTier,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel

KEY_ID = "regulatory_authority_key"


def _proposal(cost: float, proposal_id="prop_tier"):
    return StrategyProposal(
        id=proposal_id,
        intent_id="intent_1",
        attempt_number=1,
        plan_description="Spend money",
        actions=[
            PlannedAction(action_type="update_record", target="acct_1", parameters={}, risk_score=1)
        ],
        estimated_cost=cost,
        rationale="test",
        generated_at=datetime.utcnow(),
    )


def _intent():
    return IntentVector(
        id="intent_1",
        objective="Operate",
        priority=50,
        hard_constraints=[],
        soft_constraints=[],
        created_by="test",
        created_at=datetime.utcnow(),
    )


def _world():
    return WorldModel(entities={}, last_reconciled=datetime.utcnow())


def _floor_profile() -> ApplicabilityProfile:
    """A profile whose Tier-1 floor caps spend at $5.00."""
    return ApplicabilityProfile(
        profile_id="prof_v1",
        tier1_constraints=[
            Constraint(
                name="cost_ceiling",
                type=ConstraintType.HARD,
                description="Regulatory spend ceiling $5.00",
            )
        ],
        issued_at=datetime(2026, 1, 1),
    )


def _signed_kernel():
    private_hex, public_hex = generate_keypair()
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    signed = sign_profile(_floor_profile(), private_hex, KEY_ID)
    kernel = GovernanceKernel(applicability_profile=signed, profile_key_registry=registry)
    return kernel, private_hex, public_hex, registry, signed


# --- Floor is enforced and always active -----------------------------------

def test_tier1_floor_rejects_violation_without_any_intent():
    kernel, *_ = _signed_kernel()
    # Over the $5 floor; no intent declares cost_ceiling — the floor still applies.
    decision = kernel.evaluate_proposal(
        proposal=_proposal(cost=10.0),
        intents=[_intent()],
        world_state=_world(),
        action_type_id="task_execution",  # strict typing is on once a profile is loaded
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "cost_ceiling" in decision.violated_constraints


def test_tier1_floor_allows_compliant_proposal():
    kernel, *_ = _signed_kernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(cost=1.0),
        intents=[_intent()],
        world_state=_world(),
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.APPROVED


def test_loaded_floor_constraint_is_tier1_and_always_active():
    kernel, *_ = _signed_kernel()
    floor = kernel._tier1_floor
    assert len(floor) == 1
    assert floor[0].tier == PolicyTier.REGULATORY_FLOOR
    assert floor[0].activation.always is True


# --- Fail-closed profile verification ---------------------------------------

def test_unsigned_profile_is_refused():
    with pytest.raises(ProfileVerificationError, match="unsigned"):
        GovernanceKernel(
            applicability_profile=_floor_profile(),  # never signed
            profile_key_registry=PublicKeyRegistry(),
        )


def test_tampered_profile_is_refused():
    private_hex, public_hex = generate_keypair()
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    signed = sign_profile(_floor_profile(), private_hex, KEY_ID)
    # Weaken the floor after signing (raise the ceiling).
    signed.tier1_constraints[0].description = "Regulatory spend ceiling $1000000.00"
    with pytest.raises(ProfileVerificationError, match="invalid"):
        GovernanceKernel(applicability_profile=signed, profile_key_registry=registry)


def test_unknown_signing_key_is_refused():
    private_hex, _ = generate_keypair()
    signed = sign_profile(_floor_profile(), private_hex, KEY_ID)
    with pytest.raises(ProfileVerificationError, match="unregistered"):
        GovernanceKernel(
            applicability_profile=signed,
            profile_key_registry=PublicKeyRegistry(),  # empty — key not known
        )


def test_wrong_key_signature_is_refused():
    private_hex, _ = generate_keypair()
    _, other_public_hex = generate_keypair()  # different key
    signed = sign_profile(_floor_profile(), private_hex, KEY_ID)
    registry = PublicKeyRegistry({KEY_ID: other_public_hex})  # wrong pubkey for the id
    with pytest.raises(ProfileVerificationError, match="invalid"):
        GovernanceKernel(applicability_profile=signed, profile_key_registry=registry)


# --- Authorization comparator -----------------------------------------------

def test_authorization_comparator_rank_based():
    assert _satisfies_auth(AuthorizationLevel.L3, AuthorizationLevel.L2) is True
    assert _satisfies_auth(AuthorizationLevel.L2, AuthorizationLevel.L2) is True
    assert _satisfies_auth(AuthorizationLevel.L1, AuthorizationLevel.L2) is False
    assert _max_auth(AuthorizationLevel.L1, AuthorizationLevel.L3) == AuthorizationLevel.L3
    assert _max_auth(AuthorizationLevel.L4, AuthorizationLevel.L0) == AuthorizationLevel.L4


def test_soft_floor_constraint_is_forced_hard():
    """A regulatory-floor constraint declared SOFT must still be enforced (a SOFT
    floor would silently fail to reject)."""
    private_hex, public_hex = generate_keypair()
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    profile = ApplicabilityProfile(
        profile_id="prof_soft",
        tier1_constraints=[
            Constraint(name="cost_ceiling", type=ConstraintType.SOFT, description="Floor $5.00")
        ],
        issued_at=datetime(2026, 1, 1),
    )
    signed = sign_profile(profile, private_hex, KEY_ID)
    kernel = GovernanceKernel(applicability_profile=signed, profile_key_registry=registry)
    assert kernel._tier1_floor[0].type == ConstraintType.HARD
    decision = kernel.evaluate_proposal(
        proposal=_proposal(cost=10.0), intents=[_intent()], world_state=_world(),
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "cost_ceiling" in decision.violated_constraints


def test_constraint_defaults_to_operational_tier():
    c = Constraint(name="x", type=ConstraintType.SOFT, description="d")
    assert c.tier == PolicyTier.OPERATIONAL
