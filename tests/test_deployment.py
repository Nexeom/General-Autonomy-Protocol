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
        strategy_generator=_Gen(risk=1), isolated=False,
    )
    with pytest.raises(GovernanceConfigError, match="intent declaration"):
        loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                 action_type_id="task_execution")


# --- a fully-configured governed deployment runs end to end ----------------

def test_governed_deployment_runs_end_to_end():
    profile, registry = _signed_profile()
    loop = build_governed_deployment(
        applicability_profile=profile, profile_key_registry=registry, world_model=_world(),
        strategy_generator=_Gen(risk=1), isolated=False,
    )
    resolver: StructuredIntentResolver = loop.intent_resolver
    declaration = resolver.confirm(resolver.resolve("process the task", AuthorizationLevel.L1))

    result = loop.run(
        intent=_intent(), drift_event={}, world_state=_world(),
        intent_declaration=declaration, action_type_id="task_execution",
    )
    assert result.final_verdict == "approved"
    assert result.execution_result is not None and result.execution_result.success


def test_create_app_governed_mode_loads_floor_and_strict_typing():
    from gap_kernel.api.app import create_app
    profile, registry = _signed_profile()
    app = create_app(applicability_profile=profile, profile_key_registry=registry, isolated=False)
    gk = app.state.governance_kernel
    assert gk._strict_action_typing is True
    assert len(gk._tier1_floor) == 1  # the signed regulatory floor was loaded
    # The governed reconciler classifies its autonomous actions.
    assert app.state.reconciler._classifier is not None
    assert app.state.reconciler._classifier._base == "drift_reconciliation"


def test_create_app_open_mode_is_permissive_by_default():
    from gap_kernel.api.app import create_app
    app = create_app()  # no profile -> open mode (logs a warning)
    assert app.state.governance_kernel._strict_action_typing is False
    assert app.state.reconciler._classifier is None
    # Open mode runs the heartbeat without a monitor (advisory/none).
    assert app.state.integrity_monitor is None
    assert app.state.reconciler._integrity_monitor is None
    assert app.state.reconciler._block_on_integrity is False


def test_create_app_governed_wires_consequential_gim_onto_the_heartbeat():
    """Governed mode wires ONE integrity monitor into the autonomous reconciler
    and turns block_on_integrity on, so GIM holds fire on the shipped path."""
    from gap_kernel.api.app import create_app
    profile, registry = _signed_profile()
    app = create_app(applicability_profile=profile, profile_key_registry=registry, isolated=False)
    assert app.state.integrity_monitor is not None
    assert app.state.reconciler._integrity_monitor is app.state.integrity_monitor
    assert app.state.reconciler._block_on_integrity is True


def test_isolated_governed_deployment_runs_out_of_process():
    """By default the governed kernel runs in a separate OS process: the loop's
    governance handle is a SubprocessGovernanceClient holding no signing key, and
    the deployment still evaluates + executes end to end across the boundary."""
    from gap_kernel.client.governance_client import SubprocessGovernanceClient

    profile, registry = _signed_profile()
    loop = build_governed_deployment(  # isolated defaults to True
        applicability_profile=profile, profile_key_registry=registry, world_model=_world(),
        strategy_generator=_Gen(risk=1),
    )
    try:
        assert isinstance(loop.governance, SubprocessGovernanceClient)
        assert not hasattr(loop.governance, "_signing_key_hex")
        resolver: StructuredIntentResolver = loop.intent_resolver
        decl = resolver.confirm(resolver.resolve("process the task", AuthorizationLevel.L1))
        result = loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                          intent_declaration=decl, action_type_id="task_execution")
        assert result.final_verdict == "approved"
        assert result.execution_result is not None and result.execution_result.success
    finally:
        loop.governance.close()


def test_governed_loop_context_manager_reaps_the_subprocess():
    """The isolated loop is a context manager, so the kernel subprocess is reaped
    deterministically on exit (no orphan if the caller uses ``with``)."""
    profile, registry = _signed_profile()
    with build_governed_deployment(
        applicability_profile=profile, profile_key_registry=registry, world_model=_world(),
        strategy_generator=_Gen(risk=1),
    ) as loop:
        proc = loop.governance._proc
        assert proc.poll() is None       # child running inside the context
    assert proc.poll() is not None       # reaped on exit


def test_create_app_governed_isolates_the_kernel_by_default():
    from gap_kernel.api.app import create_app
    from gap_kernel.client.governance_client import SubprocessGovernanceClient

    profile, registry = _signed_profile()
    app = create_app(applicability_profile=profile, profile_key_registry=registry)  # isolated default
    try:
        gk = app.state.governance_kernel
        assert isinstance(gk, SubprocessGovernanceClient)
        assert app.state.governance_client is gk
        assert not hasattr(gk, "_signing_key_hex")        # signing key is out of process
        # The action-type registry endpoints still work through the boundary.
        assert "task_execution" in gk.get_registered_action_types()
    finally:
        app.state.governance_client.close()


def test_governed_deployment_blocks_unconfirmed_intent():
    profile, registry = _signed_profile()
    loop = build_governed_deployment(
        applicability_profile=profile, profile_key_registry=registry, world_model=_world(),
        strategy_generator=_Gen(risk=1), isolated=False,
    )
    resolver: StructuredIntentResolver = loop.intent_resolver
    pending = resolver.resolve("process the task", AuthorizationLevel.L1)  # not confirmed
    result = loop.run(
        intent=_intent(), drift_event={}, world_state=_world(),
        intent_declaration=pending, action_type_id="task_execution",
    )
    assert result.final_verdict == "awaiting_intent_confirmation"
