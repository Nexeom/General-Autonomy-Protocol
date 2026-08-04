"""Action Type Registry integrity + kernel-owned evaluation inputs.

The Action Type Registry is governance configuration: it decides which action
categories exist and what authorization gate each one carries. These tests
assert the registry cannot be rewritten from below, that a governed kernel takes
its registry from the SIGNED Applicability Profile, and that the two inputs an
agent authors — the target entity and the evaluation clock — cannot be used to
make a hard constraint evaluate to nothing.
"""

from datetime import datetime, timedelta

import pytest

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.errors import GovernanceConfigError
from gap_kernel.governance.kernel import (
    GovernanceKernel,
    _risk_derived_floor,
    _satisfies_auth,
)
from gap_kernel.governance.profile import (
    ApplicabilityProfile,
    ProfileVerificationError,
    sign_profile,
)
from gap_kernel.models.governance import (
    ActionTypeSpec,
    AuthorizationLevel,
    GovernanceVerdict,
    RiskProfile,
)
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyActivation,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel

KEY_ID = "regulatory_authority_key"


def _intent(hard=None) -> IntentVector:
    return IntentVector(
        id="intent_1",
        objective="Operate",
        priority=50,
        hard_constraints=hard or [],
        soft_constraints=[],
        created_by="test",
        created_at=utcnow(),
    )


def _world() -> WorldModel:
    return WorldModel(entities={}, last_reconciled=utcnow())


def _proposal(action_type="query_crm", target="t1", cost=0.01, pid="prop_r"):
    return StrategyProposal(
        id=pid,
        intent_id="intent_1",
        attempt_number=1,
        plan_description="op",
        actions=[
            PlannedAction(
                action_type=action_type, target=target, parameters={}, risk_score=1
            )
        ],
        estimated_cost=cost,
        rationale="r",
        generated_at=utcnow(),
    )


def _profile(action_types=None, tier1=None) -> ApplicabilityProfile:
    return ApplicabilityProfile(
        profile_id="prof_registry",
        tier1_constraints=tier1 or [],
        action_types=action_types or {},
        issued_at=datetime(2026, 1, 1),
    )


def _sign(profile: ApplicabilityProfile):
    private_hex, public_hex = generate_keypair()
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    return sign_profile(profile, private_hex, KEY_ID), registry


def _governed_kernel(profile=None, **kwargs) -> GovernanceKernel:
    signed, registry = _sign(profile or _profile())
    return GovernanceKernel(
        governed=True,
        applicability_profile=signed,
        profile_key_registry=registry,
        **kwargs,
    )


# --- (a) a governed kernel refuses runtime registration ---------------------

def test_governed_kernel_refuses_runtime_action_type_registration():
    """Registration is a governed change to the policy set. In governed mode it
    happens only through the signed profile, so the runtime call must refuse."""
    kernel = _governed_kernel()
    with pytest.raises(GovernanceConfigError, match="governed"):
        kernel.register_action_type(
            ActionTypeSpec(type_id="new_type", description="d"), "agent"
        )
    assert kernel.get_action_type("new_type") is None


# --- (b) open mode is a monotonic ratchet -----------------------------------

def test_open_mode_refuses_to_overwrite_an_existing_action_type():
    """Re-registering skill_modification at L0 would strip its L2 approval gate."""
    kernel = GovernanceKernel()
    assert kernel.get_action_type("skill_modification").default_authorization_level == (
        AuthorizationLevel.L2
    )
    with pytest.raises(GovernanceConfigError, match="already registered"):
        kernel.register_action_type(
            ActionTypeSpec(
                type_id="skill_modification",
                description="Downgraded",
                default_authorization_level=AuthorizationLevel.L0,
            ),
            "agent",
        )
    assert kernel.get_action_type("skill_modification").default_authorization_level == (
        AuthorizationLevel.L2
    )


def test_open_mode_still_adds_a_new_action_type():
    """The ratchet blocks replacement, not growth."""
    kernel = GovernanceKernel()
    registered = kernel.register_action_type(
        ActionTypeSpec(type_id="custom_analysis", description="d"), "admin_user"
    )
    assert registered.registered_by == "admin_user"
    assert registered.registered_at is not None
    assert kernel.validate_action_type("custom_analysis")


def test_baseline_action_types_satisfy_the_floor_they_enforce():
    """The shipped baseline must clear the same bar new registrations do."""
    for type_id, spec in GovernanceKernel().get_registered_action_types().items():
        floor = _risk_derived_floor(spec.risk_profile)
        assert _satisfies_auth(spec.default_authorization_level, floor), (
            f"baseline '{type_id}' is registered below its own risk floor {floor}"
        )


def test_open_mode_refuses_registration_below_the_risk_derived_floor():
    """An org-wide, irreversible, wide-blast action cannot be filed as L0."""
    kernel = GovernanceKernel()
    with pytest.raises(GovernanceConfigError, match="requires at least"):
        kernel.register_action_type(
            ActionTypeSpec(
                type_id="mass_deletion",
                description="Irreversible org-wide deletion",
                risk_profile=RiskProfile(
                    impact_scope="org",
                    reversibility="irreversible",
                    blast_radius="wide",
                ),
                default_authorization_level=AuthorizationLevel.L0,
            ),
            "agent",
        )
    assert kernel.get_action_type("mass_deletion") is None


# --- (c) profile-carried action types ---------------------------------------

def _disbursement_profile() -> ApplicabilityProfile:
    return _profile(
        action_types={
            "regulated_disbursement": ActionTypeSpec(
                type_id="regulated_disbursement",
                description="Move client funds",
                risk_profile=RiskProfile(
                    impact_scope="external",
                    reversibility="irreversible",
                    blast_radius="wide",
                ),
                default_authorization_level=AuthorizationLevel.L4,
            )
        }
    )


def test_profile_carried_action_type_is_registered_and_enforced():
    kernel = _governed_kernel(_disbursement_profile())
    spec = kernel.get_action_type("regulated_disbursement")
    assert spec is not None
    assert spec.default_authorization_level == AuthorizationLevel.L4
    # The profile's gate is applied: an L4 type escalates however low its risk score.
    decision = kernel.evaluate_proposal(
        proposal=_proposal(),
        intents=[_intent()],
        world_state=_world(),
        action_type_id="regulated_disbursement",
    )
    assert decision.verdict == GovernanceVerdict.ESCALATE
    assert decision.authorization_level == AuthorizationLevel.L4


def test_tampering_with_a_profile_action_type_fails_closed():
    """Downgrading a profile-carried type after signing breaks the signature."""
    signed, registry = _sign(_disbursement_profile())
    signed.action_types["regulated_disbursement"].default_authorization_level = (
        AuthorizationLevel.L0
    )
    with pytest.raises(ProfileVerificationError, match="invalid"):
        GovernanceKernel(
            governed=True,
            applicability_profile=signed,
            profile_key_registry=registry,
        )


# --- (d) an untracked target is a violation, not a pass ---------------------

def _gdpr_intent() -> IntentVector:
    return _intent(hard=[
        Constraint(
            name="gdpr_consent_required",
            type=ConstraintType.HARD,
            description="Verify GDPR consent before direct outreach",
        )
    ])


def test_untracked_target_violates_a_world_model_backed_hard_constraint():
    """The agent authors ``target``. Naming an entity the world model does not
    carry must not make the GDPR gate evaluate nothing and pass."""
    kernel = _governed_kernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(action_type="send_email", target="ghost_lead"),
        intents=[_gdpr_intent()],
        world_state=_world(),  # ghost_lead is not in it
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "gdpr_consent_required" in decision.violated_constraints


def test_untracked_target_violates_the_contact_hours_constraint():
    kernel = _governed_kernel()
    intent = _intent(hard=[
        Constraint(
            name="no_contact_outside_hours",
            type=ConstraintType.HARD,
            description="No automated outreach 10PM-7AM lead local time",
        )
    ])
    decision = kernel.evaluate_proposal(
        proposal=_proposal(action_type="send_sms", target="ghost_lead"),
        intents=[intent],
        world_state=_world(),
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "no_contact_outside_hours" in decision.violated_constraints


def test_open_mode_keeps_the_permissive_untracked_target_behaviour():
    """Open/prototype deployments are unchanged; the flag is opt-in there."""
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(action_type="send_email", target="ghost_lead"),
        intents=[_gdpr_intent()],
        world_state=_world(),
    )
    assert decision.verdict == GovernanceVerdict.APPROVED


def test_untracked_targets_can_be_allowed_explicitly_in_governed_mode():
    kernel = _governed_kernel(allow_untracked_targets=True)
    decision = kernel.evaluate_proposal(
        proposal=_proposal(action_type="send_email", target="ghost_lead"),
        intents=[_gdpr_intent()],
        world_state=_world(),
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.APPROVED


# --- (e) the kernel owns its evaluation clock -------------------------------

def _scheduled_ceiling_intent(now: datetime) -> IntentVector:
    """A HARD $5 ceiling active during the current UTC hour (and the next, so a
    clock rollover mid-test cannot deactivate it)."""
    hours = f"{now.hour},{(now.hour + 1) % 24}"
    return _intent(hard=[
        Constraint(
            name="cost_ceiling",
            type=ConstraintType.HARD,
            description="Regulatory spend ceiling $5.00",
            activation=PolicyActivation(always=False, schedule=f"* {hours} * * *"),
        )
    ])


def test_governed_kernel_ignores_agent_supplied_current_time():
    """An agent that names a time outside a scheduled HARD constraint's window
    must not be able to drop the constraint out of the active set and collect a
    genuinely kernel-signed APPROVED."""
    now = utcnow()
    kernel = _governed_kernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(cost=10.0),
        intents=[_scheduled_ceiling_intent(now)],
        world_state=_world(),
        current_time=now.replace(hour=(now.hour + 6) % 24),  # outside the window
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "cost_ceiling" in decision.violated_constraints


def test_governed_decision_timestamp_is_the_kernels_own_clock():
    kernel = _governed_kernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(),
        intents=[_intent()],
        world_state=_world(),
        current_time=utcnow() - timedelta(days=365),
        action_type_id="task_execution",
    )
    assert decision.evaluated_at > utcnow() - timedelta(minutes=5)


def test_open_mode_still_honours_a_caller_supplied_time():
    """In-process callers and tests that drive the clock keep working."""
    now = utcnow()
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(cost=10.0),
        intents=[_scheduled_ceiling_intent(now)],
        world_state=_world(),
        current_time=now.replace(hour=(now.hour + 6) % 24),
    )
    assert decision.verdict == GovernanceVerdict.APPROVED
