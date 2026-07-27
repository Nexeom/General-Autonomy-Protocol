"""Characterization lock on every default posture.

Several of the defects this remediation closed were not missing code — they were
defaults. A registry that could be written because nothing said it could not, a
clock honoured because the parameter was there, an HTTP surface that mutated
because the routes were registered unconditionally. Each was a default nobody
had written down, so nobody noticed when it was wrong.

So this file states each default as a fact and pins it. A test here failing does
not mean something broke; it means a guarantee was given up, and the assertion
message names which one. Where a default is deliberately PERMISSIVE — open /
prototype mode exists so the kernel can be embedded and tested — that is pinned
too, with the reason. The point is that every posture is chosen.
"""

import inspect
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from gap_kernel._time import utcnow
from gap_kernel.api.app import create_app
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.errors import GovernanceConfigError
from gap_kernel.execution.fabric import ExecutionError, ExecutionFabric
from gap_kernel.governance.deployment import build_governed_deployment
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.models.governance import (
    ActionTypeSpec,
    AuthorizationLevel,
    GovernanceVerdict,
    RiskProfile,
)
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.service.kernel_server import TRUST_ROOT_ENV, provision_trust_root

KID = "regulatory_authority"

# Far enough from "now" that no clock skew could confuse an honoured caller time
# with the kernel's own.
CALLER_CLOCK = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)


def _proposal(pid="prop_posture"):
    return StrategyProposal(
        id=pid, intent_id="i1", attempt_number=1, plan_description="p",
        actions=[PlannedAction(action_type="query_crm", target="t1", parameters={}, risk_score=1)],
        estimated_cost=0.01, rationale="r", generated_at=utcnow(),
    )


def _intent():
    return IntentVector(id="i1", objective="o", priority=50, hard_constraints=[],
                        soft_constraints=[], created_by="t", created_at=utcnow())


def _world():
    return WorldModel(entities={}, last_reconciled=utcnow())


def _signed_profile(private_key_hex: str) -> ApplicabilityProfile:
    return sign_profile(
        ApplicabilityProfile(
            profile_id="posture",
            tier1_constraints=[
                Constraint(name="cost_ceiling", type=ConstraintType.HARD,
                           description="Floor $100.00")
            ],
            issued_at=datetime(2026, 1, 1),
        ),
        private_key_hex,
        KID,
    )


@pytest.fixture
def governed_kernel():
    private_key_hex, public_key_hex = generate_keypair()
    return GovernanceKernel(
        governed=True,
        applicability_profile=_signed_profile(private_key_hex),
        profile_key_registry=PublicKeyRegistry({KID: public_key_hex}),
    )


def _defaults(fn) -> dict:
    return {
        name: param.default
        for name, param in inspect.signature(fn).parameters.items()
        if param.default is not inspect.Parameter.empty
    }


# --- GovernanceKernel(): the open / prototype posture -----------------------
#
# Every default below is PERMISSIVE on purpose. The bare constructor is the
# embedding and test path: it has no signed regulatory floor, so it has no
# authority to enforce one, and a kernel that refused everything without a
# profile would just be unusable rather than safe. What makes that acceptable is
# that "no profile" is itself the signal — an open kernel is never silently
# mistaken for a governed one (see the governed section, which mirrors each of
# these), and `create_app()` says so out loud.


def test_open_kernel_is_not_governed():
    assert GovernanceKernel()._governed is False, (
        "A kernel with no Applicability Profile must not report itself governed: "
        "every guarantee below keys off this flag."
    )


def test_open_kernel_does_not_require_a_declared_action_type():
    assert GovernanceKernel()._strict_action_typing is False, (
        "Open mode deliberately evaluates proposals that declare no action type. "
        "Turning this on by default would break embedding; the floor turns it on."
    )


def test_open_kernel_allows_untracked_targets():
    assert GovernanceKernel()._allow_untracked_targets is True, (
        "Open mode deliberately lets a world-model-backed constraint pass when "
        "the target is untracked — a prototype world model is usually empty."
    )


def test_open_kernel_does_not_require_attested_evidence():
    assert GovernanceKernel()._require_attested_evidence is False, (
        "Open mode deliberately rules on unattested world-model facts: a "
        "prototype has no consent-of-record system to attest them."
    )


def test_open_kernel_honours_a_caller_supplied_clock():
    """Deliberate: pinning evaluation time is how a prototype tests scheduled
    constraints. It is safe only because an open kernel governs nothing."""
    decision = GovernanceKernel().evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world(),
        current_time=CALLER_CLOCK,
    )
    assert decision.evaluated_at == CALLER_CLOCK


def test_open_kernel_registry_is_an_add_only_ratchet():
    """Deliberate: runtime registration stays available without a floor. It is
    bounded even here — additions only, never a replacement (test below)."""
    kernel = GovernanceKernel()
    spec = ActionTypeSpec(
        type_id="posture_probe", description="d",
        risk_profile=RiskProfile(impact_scope="local", reversibility="reversible",
                                 blast_radius="narrow"),
        default_authorization_level=AuthorizationLevel.L1,
    )
    assert kernel.register_action_type(spec, registered_by="test").type_id == "posture_probe"


def test_open_kernel_registry_still_refuses_to_replace_a_baseline_type():
    """The one thing that is NOT permissive in open mode. Replacement is how a
    weaker authorization gate overwrites the one in force, and that is a defect
    in any posture — so the prototype path does not get it either."""
    kernel = GovernanceKernel()
    existing = next(iter(kernel.get_registered_action_types()))
    with pytest.raises(GovernanceConfigError):
        kernel.register_action_type(
            ActionTypeSpec(type_id=existing, description="weaker",
                           default_authorization_level=AuthorizationLevel.L0),
            registered_by="test",
        )


def test_single_use_decision_fields_are_not_a_governed_only_feature():
    """Nonce and expiry are carried by EVERY decision, open mode included. Replay
    protection that only switched on under a profile would leave the prototype
    path — the one people actually run first — replayable."""
    decision = GovernanceKernel().evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    assert decision.nonce, "an unbound decision cannot be made single-use"
    assert decision.expires_at is not None, "a decision with no expiry never goes stale"
    assert decision.decision_signature, "an unsigned decision cannot be attributed"


def test_decision_lifetime_is_bounded_by_default():
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    lifetime = decision.expires_at - decision.evaluated_at
    assert timedelta(0) < lifetime <= timedelta(hours=1), (
        f"The default decision lifetime is {lifetime}. An unbounded — or very "
        f"long — default lifetime is a replay window."
    )


def test_kernel_posture_flags_default_to_derived_rather_than_permissive():
    """The three posture flags default to None, meaning 'derive from whether a
    floor is present'. A literal True/False here would freeze one posture into
    both modes."""
    defaults = _defaults(GovernanceKernel.__init__)
    assert defaults["governed"] is False
    assert defaults["allow_untracked_targets"] is None
    assert defaults["require_attested_evidence"] is None


# --- GovernanceKernel(governed=True): the governed posture ------------------
#
# The same five properties, inverted. Each is what the open default gives up.


def test_governed_kernel_requires_a_declared_action_type(governed_kernel):
    assert governed_kernel._strict_action_typing is True, (
        "Strict action typing off in governed mode reopens the bypass where "
        "omitting action_type_id skips the Action Type Registry gate entirely."
    )


def test_governed_kernel_refuses_untracked_targets(governed_kernel):
    assert governed_kernel._allow_untracked_targets is False, (
        "Allowing untracked targets hands the agent the choice of whether the "
        "GDPR and contact-hours gates have anything to evaluate."
    )


def test_governed_kernel_requires_attested_evidence(governed_kernel):
    assert governed_kernel._require_attested_evidence is True, (
        "Without attestation, whatever can write gdpr_consent into the world "
        "model decides the verdict of the constraint that reads it."
    )


def test_governed_kernel_reads_its_own_clock(governed_kernel):
    """The caller of an evaluation is the system being governed, so a
    caller-named time is a caller-chosen policy set."""
    decision = governed_kernel.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world(),
        current_time=CALLER_CLOCK, action_type_id="task_execution",
    )
    assert decision.evaluated_at != CALLER_CLOCK
    assert abs((decision.evaluated_at - utcnow()).total_seconds()) < 60


def test_governed_kernel_refuses_runtime_registration(governed_kernel):
    with pytest.raises(GovernanceConfigError, match="signed Applicability Profile"):
        governed_kernel.register_action_type(
            ActionTypeSpec(type_id="posture_probe", description="d"),
            registered_by="anyone",
        )


def test_a_profile_alone_makes_a_kernel_governed():
    """`governed=True` is not the switch — the signed floor is. A caller that
    loads a profile without naming the flag still gets every guarantee above."""
    private_key_hex, public_key_hex = generate_keypair()
    kernel = GovernanceKernel(
        applicability_profile=_signed_profile(private_key_hex),
        profile_key_registry=PublicKeyRegistry({KID: public_key_hex}),
    )
    assert kernel._governed is True
    assert kernel._strict_action_typing is True
    assert kernel._allow_untracked_targets is False
    assert kernel._require_attested_evidence is True


def test_governed_kernel_without_a_floor_refuses_to_be_constructed():
    with pytest.raises(GovernanceConfigError, match="Applicability Profile"):
        GovernanceKernel(governed=True)


# --- create_app(): the shipped HTTP posture ---------------------------------

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The one write-shaped method on the read surface. It evaluates a proposal and
# returns a decision; it changes no intent, world state, config or registry.
_READ_ONLY_POSTS = frozenset({"/governance/evaluate"})


def _methods(app):
    return {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }


def test_create_app_registers_no_mutating_route_by_default():
    """Deliberately exhaustive rather than a list of known routes: a NEW mutating
    route added outside the enable_mutating_routes block must fail here, not ship
    on an unauthenticated surface."""
    unexpected = {
        (method, path)
        for method, path in _methods(create_app())
        if method in _MUTATING_METHODS and path not in _READ_ONLY_POSTS
    }
    assert unexpected == set(), (
        f"create_app() registered mutating route(s) {sorted(unexpected)} by "
        f"default. GAP carries no authentication, so anything registered here is "
        f"reachable by any unauthenticated caller."
    )


def test_opting_in_is_what_registers_the_mutating_surface():
    """The other half: the default is a choice, not an accident — the routes do
    exist and a deployment behind its own authenticated proxy can have them."""
    assert ("POST", "/world/ingest") in _methods(create_app(enable_mutating_routes=True))


def test_evaluate_is_the_only_write_shaped_route_that_ships():
    routes = _methods(create_app())
    assert ("POST", "/governance/evaluate") in routes
    assert ("GET", "/governance/action-types") in routes


def test_create_app_in_open_mode_says_so(caplog):
    """An open app is a legitimate posture, so it is allowed — but never
    silently. The warning is the only thing distinguishing it from a governed
    deployment at a glance."""
    with caplog.at_level(logging.WARNING, logger="gap_kernel.api"):
        create_app()
    warnings = " ".join(record.getMessage() for record in caplog.records)
    assert "OPEN mode" in warnings
    assert "regulatory floor" in warnings


def test_create_app_in_governed_mode_does_not_warn(caplog):
    """The warning must track the posture. One that fired unconditionally would
    be tuned out, and one that never fired would be worthless."""
    private_key_hex, public_key_hex = generate_keypair()
    with caplog.at_level(logging.WARNING, logger="gap_kernel.api"):
        create_app(
            applicability_profile=_signed_profile(private_key_hex),
            profile_key_registry=PublicKeyRegistry({KID: public_key_hex}),
            isolated=False,
        )
    assert "OPEN mode" not in " ".join(r.getMessage() for r in caplog.records)


def test_the_read_surface_answers_405_not_404_for_a_suppressed_mutation():
    """The path still exists as a reader, so a suppressed writer is a refusal
    rather than a typo — a deployment that meant to opt in can tell."""
    client = TestClient(create_app())
    assert client.put("/intents/i1", json={"objective": "o"}).status_code == 405


def test_create_app_defaults_are_the_safe_ones():
    defaults = _defaults(create_app)
    assert defaults["enable_mutating_routes"] is False
    assert defaults["isolated"] is True, (
        "isolated=False runs the governed kernel — and its signing key — in the "
        "app's own process, where reflection reaches it."
    )


# --- build_governed_deployment(): what a governed deployment cannot skip ----


def test_governed_deployment_refuses_to_start_without_a_trust_root(tmp_path, monkeypatch):
    """A profile verified against a registry the same process supplied is not
    verified. The deployment stops rather than warning and running."""
    monkeypatch.delenv(TRUST_ROOT_ENV, raising=False)
    private_key_hex, public_key_hex = generate_keypair()
    with pytest.raises(GovernanceConfigError, match="trust root"):
        build_governed_deployment(
            applicability_profile=_signed_profile(private_key_hex),
            profile_key_registry=PublicKeyRegistry({KID: public_key_hex}),
            world_model=_world(),
            ledger_dir=str(tmp_path / "ledgers"),
            isolated=False,
        )


def test_governed_deployment_refuses_to_start_without_a_durable_ledger(tmp_path, monkeypatch):
    """Replay protection that dies with the process is not replay protection: a
    restart makes every spent authorization spendable again."""
    private_key_hex, public_key_hex = generate_keypair()
    trust_root = provision_trust_root(str(tmp_path / "trust"), {KID: public_key_hex})
    monkeypatch.setenv(TRUST_ROOT_ENV, trust_root.path)
    with pytest.raises(GovernanceConfigError, match="ledger"):
        build_governed_deployment(
            applicability_profile=_signed_profile(private_key_hex),
            world_model=_world(),
            ledger_dir=None,
            isolated=False,
        )


def test_governed_deployment_defaults_are_the_strict_ones():
    """Both requirements have a named prototype escape hatch. Neither is the
    default, and neither can be taken by omission."""
    defaults = _defaults(build_governed_deployment)
    assert defaults["require_independent_trust_root"] is True
    assert defaults["allow_ephemeral_ledgers"] is False
    assert defaults["isolated"] is True, (
        "An in-process governed kernel is a convenience, not an isolation "
        "boundary: the agent side would hold the signing key."
    )


# --- ExecutionFabric(): the last gate before a side effect ------------------


def test_fabric_refuses_an_unverifiable_decision_by_omission():
    """No kernel key configured means no decision can be attributed. The bare
    constructor treats that as a refusal, not as permission."""
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    assert decision.verdict == GovernanceVerdict.APPROVED
    with pytest.raises(ExecutionError):
        ExecutionFabric(_world()).execute(_proposal(), decision)


def test_unsigned_execution_requires_naming_the_escape_hatch():
    """The prototype path exists — but a deployment reaches it only by writing
    the words, which is what makes it greppable in a review."""
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    fabric = ExecutionFabric(_world(), allow_unsigned_decisions=True)
    assert fabric.execute(_proposal(), decision).success is True


def test_fabric_defaults_verify_and_replay_protect():
    defaults = _defaults(ExecutionFabric.__init__)
    assert defaults["allow_unsigned_decisions"] is False, (
        "Defaulting this to True would let any in-process caller mint an "
        "approval, which is the whole point of signing decisions."
    )
    assert defaults["execution_ledger"] is None
    assert ExecutionFabric(_world())._execution_ledger is not None, (
        "A fabric with no ledger has no record of what it already executed, so "
        "every guard it applies is stateless and every decision is replayable."
    )


def test_fabric_ledger_default_is_ephemeral_and_governed_deployments_refuse_it():
    """Deliberate: an embedded fabric should not write a database file into
    someone's working directory. It is safe only because
    build_governed_deployment refuses `:memory:` outright."""
    assert ExecutionFabric(_world())._execution_ledger.db_path == ":memory:"
