"""Safe deployment defaults — the governed posture is the default posture.

Three defaults that a governed deployment cannot silently run without:

  * an **independent trust root** — the key that verifies the signed
    Applicability Profile, and the kernel identity that signs decisions, are
    resolved by the kernel from a deployer-owned config path, not handed down by
    the agent-side parent alongside the profile it is supposed to verify;
  * **durable ledgers** — replay protection that survives a restart, rather than
    evaporating with the process;
  * a **pinned kernel identity** — the same public key across restarts, so an
    external auditor has something stable to verify against.

Each has exactly one explicitly named prototype escape hatch.
"""

import os
import subprocess
from datetime import datetime

import pytest

from gap_kernel._time import utcnow
from gap_kernel.client import governance_client as governance_client_module
from gap_kernel.client.governance_client import (
    GovernanceClientError,
    SubprocessGovernanceClient,
)
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.errors import GovernanceConfigError
from gap_kernel.governance.deployment import build_governed_deployment
from gap_kernel.governance.profile import (
    ApplicabilityProfile,
    ProfileVerificationError,
    sign_profile,
)
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.service.kernel_server import (
    TRUST_ROOT_ENV,
    TrustRootError,
    load_trust_root,
    provision_trust_root,
)
from gap_kernel.verification.execution_ledger import (
    STATUS_COMPLETE,
    ExecutionLedger,
)
from gap_kernel.verification.oob_ledger import OOBLedger

KID = "regulatory_authority"


class _Gen:
    def generate(self, intent, world_state, drift_event, accumulated_constraints,
                 prior_proposals, attempt_number):
        return StrategyProposal(
            id=f"prop_{attempt_number}", intent_id=intent.id, attempt_number=attempt_number,
            plan_description="op",
            actions=[PlannedAction(action_type="query_crm", target="t1", parameters={}, risk_score=1)],
            estimated_cost=0.01, rationale="r", generated_at=utcnow(),
        )


def _intent():
    return IntentVector(id="i1", objective="o", priority=50, hard_constraints=[],
                        soft_constraints=[], created_by="t", created_at=utcnow())


def _world():
    return WorldModel(entities={}, last_reconciled=utcnow())


def _profile_signed_by(private_key_hex: str, profile_id: str = "prof") -> ApplicabilityProfile:
    return sign_profile(
        ApplicabilityProfile(
            profile_id=profile_id,
            tier1_constraints=[
                Constraint(name="cost_ceiling", type=ConstraintType.HARD,
                           description="Floor $100.00")
            ],
            issued_at=datetime(2026, 1, 1),
        ),
        private_key_hex,
        KID,
    )


class _Deployer:
    """What a deployer establishes out of band: a trust root on a path it owns,
    and a directory for the durable ledgers."""

    def __init__(self, tmp_path, monkeypatch):
        self.private_key_hex, self.public_key_hex = generate_keypair()
        self.profile = _profile_signed_by(self.private_key_hex)
        self.trust_root = provision_trust_root(
            str(tmp_path / "trust"), {KID: self.public_key_hex}
        )
        monkeypatch.setenv(TRUST_ROOT_ENV, self.trust_root.path)
        self.ledger_dir = str(tmp_path / "ledgers")

    def build(self, **kwargs):
        kwargs.setdefault("applicability_profile", self.profile)
        kwargs.setdefault("world_model", _world())
        kwargs.setdefault("ledger_dir", self.ledger_dir)
        kwargs.setdefault("strategy_generator", _Gen())
        kwargs.setdefault("isolated", False)
        return build_governed_deployment(**kwargs)


@pytest.fixture
def deployer(tmp_path, monkeypatch):
    return _Deployer(tmp_path, monkeypatch)


def _run(loop):
    resolver = loop.intent_resolver
    decl = resolver.confirm(resolver.resolve("process the task", AuthorizationLevel.L1))
    return loop.run(intent=_intent(), drift_event={}, world_state=_world(),
                    intent_declaration=decl, action_type_id="task_execution")


# --- B1: the trust root is independent of the agent-side parent -------------

def test_governed_deployment_without_a_trust_root_raises(tmp_path, monkeypatch):
    """No independently-deployed trust root means the child would verify the
    parent's profile against the parent's key. That is not a trust root, so a
    governed deployment refuses to start — it does not warn and continue."""
    monkeypatch.delenv(TRUST_ROOT_ENV, raising=False)
    priv, pub = generate_keypair()
    with pytest.raises(GovernanceConfigError, match="trust root"):
        build_governed_deployment(
            applicability_profile=_profile_signed_by(priv),
            profile_key_registry=PublicKeyRegistry({KID: pub}),
            world_model=_world(),
            ledger_dir=str(tmp_path / "ledgers"),
            isolated=False,
        )


def test_a_substituted_profile_and_its_matching_key_are_refused(deployer):
    """The B1 attack: the parent supplies BOTH a profile of its own choosing and
    the key that verifies it. With an independent trust root the substituted key
    is never consulted, so the substituted profile fails verification."""
    attacker_priv, attacker_pub = generate_keypair()
    with pytest.raises(ProfileVerificationError):
        deployer.build(
            applicability_profile=_profile_signed_by(attacker_priv, "attacker_profile"),
            profile_key_registry=PublicKeyRegistry({KID: attacker_pub}),
        )


def test_trust_root_keys_are_used_even_when_the_parent_supplies_none(deployer):
    """The legitimate profile verifies with NO registry from the parent at all —
    proof the verification key came from the trust root, not from the caller."""
    loop = deployer.build(profile_key_registry=None)
    assert _run(loop).final_verdict == "approved"


def test_isolated_deployment_ignores_the_parent_supplied_registry(deployer):
    """Same guarantee across the process boundary: the child re-resolves its own
    trust root rather than trusting the registry that crossed with the profile."""
    attacker_priv, attacker_pub = generate_keypair()
    with pytest.raises(GovernanceClientError):
        deployer.build(
            isolated=True,
            applicability_profile=_profile_signed_by(attacker_priv, "attacker_profile"),
            profile_key_registry=PublicKeyRegistry({KID: attacker_pub}),
        )


def test_prototype_posture_is_explicitly_named(tmp_path, monkeypatch):
    """The escape hatch exists, is named, and must be asked for by name."""
    monkeypatch.delenv(TRUST_ROOT_ENV, raising=False)
    priv, pub = generate_keypair()
    loop = build_governed_deployment(
        applicability_profile=_profile_signed_by(priv),
        profile_key_registry=PublicKeyRegistry({KID: pub}),
        world_model=_world(),
        strategy_generator=_Gen(),
        isolated=False,
        require_independent_trust_root=False,
        allow_ephemeral_ledgers=True,
    )
    assert _run(loop).final_verdict == "approved"


def test_an_unreadable_trust_root_fails_closed(tmp_path, monkeypatch):
    """An unevaluable trust root is a violation, not a fallback to the parent's."""
    bad = tmp_path / "trust_root.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(TRUST_ROOT_ENV, str(bad))
    priv, pub = generate_keypair()
    with pytest.raises(TrustRootError):
        build_governed_deployment(
            applicability_profile=_profile_signed_by(priv),
            profile_key_registry=PublicKeyRegistry({KID: pub}),
            world_model=_world(),
            ledger_dir=str(tmp_path / "ledgers"),
            isolated=False,
        )


# --- B2: durable ledgers ----------------------------------------------------

def test_governed_deployment_without_a_ledger_directory_raises(deployer):
    with pytest.raises(GovernanceConfigError, match="ledger"):
        deployer.build(ledger_dir=None)


def test_injected_in_memory_ledger_is_refused(deployer):
    """`:memory:` is `:memory:` however it arrives — passing the ledger in does
    not launder it past the durability requirement."""
    with pytest.raises(GovernanceConfigError, match="ledger"):
        deployer.build(oob_ledger=OOBLedger(":memory:"))
    with pytest.raises(GovernanceConfigError, match="ledger"):
        deployer.build(execution_ledger=ExecutionLedger(":memory:"))


def test_replay_protection_survives_a_restart(deployer):
    """The whole point of a durable ledger: a fresh process opening the same
    directory still knows the authorization was spent."""
    loop = deployer.build()
    result = _run(loop)
    assert result.final_verdict == "approved"
    nonce = result.decisions[-1].nonce
    assert nonce

    reopened = ExecutionLedger(os.path.join(deployer.ledger_dir, "execution_ledger.db"))
    assert reopened.status(nonce) == STATUS_COMPLETE


def test_ephemeral_ledgers_only_through_the_named_flag(deployer):
    loop = deployer.build(ledger_dir=None, allow_ephemeral_ledgers=True)
    assert _run(loop).final_verdict == "approved"


# --- B3: persistent, pinned kernel identity ---------------------------------

def test_kernel_identity_persists_across_restarts(deployer):
    """Two separate builds against the same trust root sign with the same key,
    so an external auditor can pin the kernel's identity."""
    first = deployer.build()
    second = deployer.build()
    assert first.governance.public_key_hex == deployer.trust_root.kernel_public_key_hex
    assert second.governance.public_key_hex == first.governance.public_key_hex


def test_provisioning_is_idempotent(tmp_path):
    """Re-provisioning an existing trust root keeps the identity it already has —
    otherwise every deploy would silently rotate the kernel's key."""
    _, pub = generate_keypair()
    first = provision_trust_root(str(tmp_path / "trust"), {KID: pub})
    second = provision_trust_root(str(tmp_path / "trust"), {KID: pub})
    assert second.kernel_public_key_hex == first.kernel_public_key_hex


def test_isolated_client_pins_the_kernel_identity_from_the_trust_root(deployer):
    with deployer.build(isolated=True) as loop:
        assert loop.governance.public_key_hex == deployer.trust_root.kernel_public_key_hex


def test_client_refuses_a_child_that_is_not_the_pinned_kernel(tmp_path, monkeypatch):
    """Child substitution: the deployer's pin says one identity, the process that
    actually answers holds another. The handshake must fail closed rather than
    accept whatever key the child minted."""
    _, pub = generate_keypair()
    deployed = provision_trust_root(str(tmp_path / "deployed"), {KID: pub})
    substituted = provision_trust_root(str(tmp_path / "substituted"), {KID: pub})
    assert substituted.kernel_public_key_hex != deployed.kernel_public_key_hex

    real_popen = subprocess.Popen

    def _substituted_child(argv, **kwargs):
        env = dict(os.environ)
        env[TRUST_ROOT_ENV] = substituted.path
        return real_popen(argv, env=env, **kwargs)

    monkeypatch.setenv(TRUST_ROOT_ENV, deployed.path)
    monkeypatch.setattr(governance_client_module.subprocess, "Popen", _substituted_child)
    with pytest.raises(Exception, match="pinned"):
        SubprocessGovernanceClient(require_trust_root=True)


def test_identity_file_that_does_not_match_the_pin_fails_closed(tmp_path, monkeypatch):
    """A pin the private key cannot satisfy is unevaluable, so it is a violation."""
    _, pub = generate_keypair()
    root = provision_trust_root(str(tmp_path / "trust"), {KID: pub})
    other = provision_trust_root(str(tmp_path / "other"), {KID: pub})
    with open(root.kernel_identity_path, "w", encoding="utf-8") as fh:
        fh.write(open(other.kernel_identity_path, encoding="utf-8").read())

    monkeypatch.setenv(TRUST_ROOT_ENV, root.path)
    with pytest.raises(TrustRootError):
        load_trust_root().load_kernel_identity()
