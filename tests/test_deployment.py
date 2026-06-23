"""Governed deployment mode — default-safe, fail-closed (re-audit follow-up).

Industry-specific policy (the regulatory floor) is REQUIRED, not defaulted; the
universal safety primitives (signature verification, strict action typing, the
SIR gate) are forced on. A governed deployment refuses to run without its floor
or without a resolved intent.
"""

from datetime import datetime, timedelta

import pytest

from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.errors import GovernanceConfigError
from gap_kernel.governance.deployment import build_governed_deployment
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.governance.sir import StructuredIntentResolver
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel

KID = "regulatory_authority"


class _Gen:
    def __init__(self, risk=1):
        self.risk = risk

    def generate(self, intent, world_state, drift_event, accumulated_constraints,
                 prior_proposals, attempt_number):
        return StrategyProposal(
            id=f"prop_{attempt_number}", intent_id=intent.id, attempt_number=attempt_number,
            plan_description="op",
            actions=[PlannedAction(action_type="query_crm", target="t1", parameters={}, risk_score=self.risk)],
            estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
        )


def _intent():
    return IntentVector(id="i1", objective="o", priority=50, hard_constraints=[],
                        soft_constraints=[], created_by="t", created_at=datetime.utcnow())


def _world():
    return WorldModel(entities={}, last_reconciled=datetime.utcnow())


def _signed_profile():
    priv, pub = generate_keypair()
    registry = PublicKeyRegistry({KID: pub})
    profile = ApplicabilityProfile(
        profile_id="prof",
        tier1_constraints=[
            Constraint(name="cost_ceiling", type=ConstraintType.HARD, description="Floor $100.00")
        ],
        issued_at=datetime(2026, 1, 1),
    )
    return sign_profile(profile, priv, KID), registry


# --- the industry floor is required ----------------------------------------

def test_governed_deployment_requires_a_floor():
    with pytest.raises(GovernanceConfigError, match="Applicability Profile"):
        build_governed_deployment(
            applicability_profile=None, profile_key_registry=PublicKeyRegistry(),
            world_model=_world(),
        )


def test_governed_kernel_requires_a_floor_and_forces_strict():
    with pytest.raises(GovernanceConfigError):
        GovernanceKernel(governed=True)  # no profile
    profile, registry = _signed_profile()
    kernel = GovernanceKernel(governed=True, applicability_profile=profile, profile_key_registry=registry)
    assert kernel._strict_action_typing is True


# --- the SIR gate is mandatory in governed mode ----------------------------

def test_governed_loop_requires_an_intent_declaration():
    profile, registry = _signed_profile()
    loop = build_governed_deployment(
        applicability_profile=profile, profile_key_registry=registry, world_model=_world(),
        strategy_generator=_Gen(risk=1),
    )
    with pytest.raises(GovernanceConfigError, match="intent declaration"):
        loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                 action_type_id="task_execution")


# --- a fully-configured governed deployment runs end to end ----------------

def test_governed_deployment_runs_end_to_end():
    profile, registry = _signed_profile()
    loop = build_governed_deployment(
        applicability_profile=profile, profile_key_registry=registry, world_model=_world(),
        strategy_generator=_Gen(risk=1),
    )
    resolver: StructuredIntentResolver = loop.intent_resolver
    declaration = resolver.confirm(resolver.resolve("process the task", AuthorizationLevel.L1))

    result = loop.run(
        intent=_intent(), drift_event={}, world_state=_world(),
        intent_declaration=declaration, action_type_id="task_execution",
    )
    assert result.final_verdict == "approved"
    assert result.execution_result is not None and result.execution_result.success


def test_governed_deployment_blocks_unconfirmed_intent():
    profile, registry = _signed_profile()
    loop = build_governed_deployment(
        applicability_profile=profile, profile_key_registry=registry, world_model=_world(),
        strategy_generator=_Gen(risk=1),
    )
    resolver: StructuredIntentResolver = loop.intent_resolver
    pending = resolver.resolve("process the task", AuthorizationLevel.L1)  # not confirmed
    result = loop.run(
        intent=_intent(), drift_event={}, world_state=_world(),
        intent_declaration=pending, action_type_id="task_execution",
    )
    assert result.final_verdict == "awaiting_intent_confirmation"
