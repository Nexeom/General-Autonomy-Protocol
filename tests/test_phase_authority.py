"""Required phase authority must reach the signed dispatch authorization."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair, sign
from gap_kernel.execution.fabric import ExecutionError, ExecutionFabric, OOBVerificationError
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.models.governance import (
    ActionTypeSpec, AuthorizationLevel, GovernanceVerdict, PhaseConfig,
)
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel


@pytest.fixture
def phase_case():
    """A signed governed profile and a real, harmless in-memory executor."""
    fabrics = []

    def build(phase_level, *, action_level=AuthorizationLevel.L0, escalate=False):
        authority_private, authority_public = generate_keypair()
        approver_private, approver_public = generate_keypair()
        action_type_id = "phase_authority_probe"
        profile = sign_profile(ApplicabilityProfile(
            profile_id="phase_authority_profile", issued_at=utcnow(),
            tier1_constraints=[Constraint(
                name="cost_ceiling", type=ConstraintType.HARD,
                description="Maximum $1.00 per proposed batch",
            )],
            action_types={action_type_id: ActionTypeSpec(
                type_id=action_type_id, description="Required phase authority regression",
                default_authorization_level=action_level,
                phase_config=[
                    PhaseConfig(phase_name="intent", required=True,
                                default_authorization_level=AuthorizationLevel.L0),
                    PhaseConfig(phase_name="review", required=True,
                                default_authorization_level=phase_level,
                                escalation_on_deviation=escalate),
                ],
            )},
        ), authority_private, "authority")
        kernel = GovernanceKernel(
            governed=True, applicability_profile=profile,
            profile_key_registry=PublicKeyRegistry({"authority": authority_public}),
            evidence_issuers=PublicKeyRegistry(),
        )
        world = WorldModel(last_reconciled=utcnow())
        intent = IntentVector(
            id="phase_intent", objective="Exercise a configured required phase",
            priority=50, hard_constraints=[], soft_constraints=[],
            created_by="operator", created_at=utcnow(),
        )
        proposal = StrategyProposal(
            id="phase_proposal", intent_id=intent.id, attempt_number=1,
            plan_description="Record one harmless executor call",
            actions=[PlannedAction(action_type="query_crm", target="record",
                                   parameters={}, risk_score=1)],
            estimated_cost=0.01, rationale="Required phase regression", generated_at=utcnow(),
        )
        decision = kernel.evaluate_proposal(
            proposal, [intent], world, action_type_id=action_type_id,
        )
        fabric = ExecutionFabric(
            world, kernel_public_key_hex=kernel.public_key_hex,
            public_key_registry=PublicKeyRegistry({"approver": approver_public}),
            approver_max_levels={"approver": AuthorizationLevel.L4},
        )
        fabrics.append(fabric)
        effects = []

        def execute(action):
            effects.append(action.target)
            return {"record": action.target}

        fabric.register_executor("query_crm", execute)

        def approve():
            approved = decision.model_copy(update={
                "human_approver_public_key_id": "approver",
                "human_approval_timestamp": utcnow(),
                "human_approval_valid_until": utcnow() + timedelta(minutes=1),
            })
            approved.human_approval_signature = sign(
                approver_private, ExecutionFabric._oob_signed_message(approved),
            )
            return approved

        return SimpleNamespace(decision=decision, proposal=proposal, fabric=fabric,
                               effects=effects, approve=approve)

    yield build
    for fabric in fabrics:
        fabric._execution_ledger.close()
        fabric._oob_ledger._conn.close()


@pytest.mark.parametrize(("phase_level", "escalate", "required_level"), [
    (AuthorizationLevel.L2, False, AuthorizationLevel.L2),
    (AuthorizationLevel.L3, False, AuthorizationLevel.L3),
    (AuthorizationLevel.L1, True, AuthorizationLevel.L2),
])
def test_required_phase_holds_dispatch_until_signed_approval(
    phase_case, phase_level, escalate, required_level,
):
    case = phase_case(phase_level, escalate=escalate)
    assert case.decision.verdict == GovernanceVerdict.APPROVED
    assert case.decision.phase_results[-1].authorization_level == required_level
    assert case.decision.authorization_level == required_level
    assert case.decision.authorization_tier == "require_approval"
    assert case.decision.decision_signature

    with pytest.raises(OOBVerificationError):
        case.fabric.execute(case.proposal, case.decision)
    assert case.effects == []

    assert case.fabric.execute(case.proposal, case.approve()).success
    assert case.effects == ["record"]


def test_required_human_only_phase_never_authorizes_agent_dispatch(phase_case):
    case = phase_case(AuthorizationLevel.L4)
    assert case.decision.verdict == GovernanceVerdict.ESCALATE
    assert case.decision.authorization_level == AuthorizationLevel.L4
    assert case.decision.authorization_tier == "escalate"
    assert case.decision.phase_results[-1].authorization_level == AuthorizationLevel.L4
    assert case.decision.rejection_reason == "phase_requires_human_only"

    # Even an authentic human approval does not turn an escalation into execution.
    with pytest.raises(ExecutionError):
        case.fabric.execute(case.proposal, case.approve())
    assert case.effects == []


def test_lower_phase_cannot_weaken_action_authority(phase_case):
    case = phase_case(AuthorizationLevel.L0, action_level=AuthorizationLevel.L2)
    assert case.decision.authorization_level == AuthorizationLevel.L2
    with pytest.raises(OOBVerificationError):
        case.fabric.execute(case.proposal, case.decision)
    assert case.effects == []
    assert case.fabric.execute(case.proposal, case.approve()).success
    assert case.effects == ["record"]


@pytest.mark.parametrize("phase_level", [AuthorizationLevel.L0, AuthorizationLevel.L1])
def test_autonomous_required_phase_still_dispatches(phase_case, phase_level):
    case = phase_case(phase_level)
    assert case.decision.verdict == GovernanceVerdict.APPROVED
    assert case.decision.authorization_level == phase_level
    assert case.fabric.execute(case.proposal, case.decision).success
    assert case.effects == ["record"]
