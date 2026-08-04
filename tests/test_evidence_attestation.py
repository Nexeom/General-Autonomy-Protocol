"""Signed Evidence Attestation — the RPC-path tests that a no-op would fail.

The critical these close: governance-relevant world-model properties carried an
attestation stamp that was an unsigned plain dict. ``WorldModelStore`` refused to
let a caller supply one, but a caller that assembles the ``WorldModel`` itself
never passes through the store — and in the isolated posture the agent
legitimately authors the entire ``world_state`` field of the ``evaluate`` RPC
request. Measured against a governed kernel, the identical proposal went from
``rejected ['gdpr_consent_required']`` to ``approved []`` with the forged stamp,
with no code execution anywhere.

Every test here drives ``WorldModel.model_validate`` on agent-authored JSON,
which is the path the store never sees. The ingest-path tests in
``test_world_model_trust.py`` pass whether or not this module's subject works;
these do not.

Scope this suite does NOT claim to cover, because the mechanism does not provide
it: that a signature makes a fact TRUE (a compromised issuer signs falsehoods and
GAP certifies them), that the guarantee survives an issuer key living beside the
agent, that replay or staleness is closed rather than time-bounded, or that the
world model as a whole is trustworthy. Two of the kernel's nine evaluators read
the world model at all; the other seven are untouched by any of this.
"""

import ast
import inspect
import json
import logging
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.governance import kernel as kernel_module
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import (
    EVIDENCE_ATTESTATION_PROPERTY,
    GOVERNANCE_RELEVANT_PROPERTIES,
    WorldModel,
)
from gap_kernel.service.kernel_server import (
    TRUST_ROOT_ENV,
    dump_governed_config,
    kernel_from_governed_config,
    load_trust_root,
    provision_trust_root,
)
from gap_kernel.world_model.attestation import (
    SIGNED_ATTESTATION_FIELDS,
    AttestationVerificationError,
    EvidenceAttestation,
    EvidenceVerifier,
    attestation_payload,
    canonical_value,
    sign_attestation,
    verify_attestation,
)
from tests.conftest import EVIDENCE_AUTHORITY

PROFILE_KEY_ID = "regulatory_authority"


# --- scaffolding ------------------------------------------------------------


def _governed_kernel(evidence_issuers=None, **kwargs) -> GovernanceKernel:
    private_hex, public_hex = generate_keypair()
    profile = sign_profile(
        ApplicabilityProfile(
            profile_id="prof_sea", tier1_constraints=[], issued_at=datetime(2026, 1, 1)
        ),
        private_hex,
        PROFILE_KEY_ID,
    )
    return GovernanceKernel(
        governed=True,
        applicability_profile=profile,
        profile_key_registry=PublicKeyRegistry({PROFILE_KEY_ID: public_hex}),
        evidence_issuers=(
            EVIDENCE_AUTHORITY.registry() if evidence_issuers is None else evidence_issuers
        ),
        **kwargs,
    )


def _gdpr_intent() -> IntentVector:
    return IntentVector(
        id="i1",
        objective="Contact high-value leads",
        priority=80,
        hard_constraints=[
            Constraint(
                name="gdpr_consent_required",
                type=ConstraintType.HARD,
                description="Verify GDPR consent before any direct outreach to EU leads",
            )
        ],
        soft_constraints=[],
        created_by="human",
        created_at=utcnow(),
    )


def _outreach(target="lead_A", action_type="send_email") -> StrategyProposal:
    return StrategyProposal(
        id="prop_sea",
        intent_id="i1",
        attempt_number=1,
        plan_description="Send the follow-up",
        actions=[
            PlannedAction(
                action_type=action_type, target=target, parameters={}, risk_score=1
            )
        ],
        estimated_cost=0.01,
        rationale="lead is waiting",
        generated_at=utcnow(),
    )


def _rpc_world(properties, entity_id="lead_A") -> WorldModel:
    """Exactly what the RPC boundary does with agent-authored JSON.

    ``model_validate`` on a dict the agent wrote. Nothing here passes through
    ``WorldModelStore``, which is why the store was never the control.
    """
    return WorldModel.model_validate({
        "entities": {
            entity_id: {
                "entity_type": "lead",
                "entity_id": entity_id,
                "properties": properties,
                "last_updated": utcnow().isoformat(),
                "source": "agent_supplied",
                "obligations": [],
            }
        },
        "last_reconciled": utcnow().isoformat(),
    })


def _evaluate(world, kernel=None, target="lead_A"):
    return (kernel or _governed_kernel()).evaluate_proposal(
        proposal=_outreach(target=target),
        intents=[_gdpr_intent()],
        world_state=world,
        action_type_id="task_execution",
    )


def _attestation(entity_id="lead_A", properties=None, **overrides) -> EvidenceAttestation:
    """An attestation with a caller-chosen window, signed by the shared issuer."""
    issued_at = overrides.pop("issued_at", utcnow())
    fields = dict(
        attestation_id="att_test",
        entity_id=entity_id,
        properties=dict(properties if properties is not None else
                        {"geo": "DE", "gdpr_consent": True}),
        issued_at=issued_at,
        expires_at=overrides.pop("expires_at", issued_at + timedelta(seconds=300)),
        issuer_key_id=EVIDENCE_AUTHORITY.key_id,
    )
    fields.update(overrides)
    return sign_attestation(
        EvidenceAttestation(**fields),
        EVIDENCE_AUTHORITY.private_key_hex,
        fields["issuer_key_id"],
    )


def _blob(attestation: EvidenceAttestation) -> dict:
    return attestation.model_dump(mode="json")


# --- 1. canonical-JSON value comparison. Written first, on purpose. ---------


def test_canonical_value_separates_true_from_one_and_one_from_one_point_zero():
    """The test the implementation is FOR.

    ``True == 1`` and ``1 == 1.0`` are both True in this interpreter, so a ``==``
    implementation of the value check passes every other test in this file and
    reopens value substitution for exactly the boolean the GDPR gate turns on.
    """
    assert True == 1                                    # noqa: E712 - the point
    assert 1 == 1.0
    assert canonical_value(True) == "true"
    assert canonical_value(1) == "1"
    assert canonical_value(1.0) == "1.0"
    assert canonical_value(True) != canonical_value(1)
    assert canonical_value(1) != canonical_value(1.0)


def test_a_signature_over_integer_one_does_not_certify_boolean_true():
    """Value substitution that survives a genuine signature, if ``==`` is used.

    The issuer signs ``gdpr_consent: 1`` — a plausible thing for a CRM export to
    emit. The agent presents ``gdpr_consent: True``. Under Python equality those
    are the same value and the gate opens; under canonical JSON they are ``1``
    and ``true`` and the fact is unattested.
    """
    attestation = _attestation(properties={"geo": "DE", "gdpr_consent": 1})
    world = _rpc_world({
        "geo": "DE",
        "gdpr_consent": True,
        EVIDENCE_ATTESTATION_PROPERTY: _blob(attestation),
    })
    decision = _evaluate(world)
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "gdpr_consent_required" in decision.violated_constraints

    # And the honest direction: presenting exactly what was signed still works,
    # so this is a trust model and not a blanket denial. ``1`` is truthy, so the
    # gate is satisfied on the merits.
    honest = _rpc_world({
        "geo": "DE",
        "gdpr_consent": 1,
        EVIDENCE_ATTESTATION_PROPERTY: _blob(attestation),
    })
    assert _evaluate(honest).verdict == GovernanceVerdict.APPROVED


def test_value_substitution_on_a_genuine_signature_is_refused():
    """The plain case: a real signature over ``False``, presented as ``True``."""
    attestation = _attestation(properties={"geo": "DE", "gdpr_consent": False})
    world = _rpc_world({
        "geo": "DE",
        "gdpr_consent": True,
        EVIDENCE_ATTESTATION_PROPERTY: _blob(attestation),
    })
    assert _evaluate(world).verdict == GovernanceVerdict.REJECTED


# --- 2. the reproduction ----------------------------------------------------


FORGED_STAMP = {
    "attested": True,
    "governance_properties": ["gdpr_consent", "geo"],
    "channel_id": "totally_a_real_crm",
}


def test_the_exact_forged_stamp_no_longer_buys_an_approval():
    """The reproduction, verbatim, on both reserved keys.

    Before: ``rejected ['gdpr_consent_required']`` unattested, ``approved []``
    with the stamp under ``_evidence_provenance``. The stamp is now inert under
    either key, because no dict a presenter can write is a signature.
    """
    unattested = _evaluate(_rpc_world({"geo": "DE", "gdpr_consent": True}))
    assert unattested.verdict == GovernanceVerdict.REJECTED
    assert "gdpr_consent_required" in unattested.violated_constraints

    for reserved in ("_evidence_provenance", EVIDENCE_ATTESTATION_PROPERTY):
        forged = _evaluate(_rpc_world({
            "geo": "DE", "gdpr_consent": True, reserved: dict(FORGED_STAMP),
        }))
        assert forged.verdict == GovernanceVerdict.REJECTED, reserved
        assert "gdpr_consent_required" in forged.violated_constraints, reserved


def test_a_genuine_attestation_is_the_only_thing_that_works():
    """Same proposal, same values, signed by a registered issuer: approved."""
    world = _rpc_world({
        "geo": "DE",
        "gdpr_consent": True,
        EVIDENCE_ATTESTATION_PROPERTY: EVIDENCE_AUTHORITY.attest(
            "lead_A", {"geo": "DE", "gdpr_consent": True}
        ),
    })
    assert _evaluate(world).verdict == GovernanceVerdict.APPROVED


# --- 3. cross-entity lift ---------------------------------------------------


def test_an_attestation_cannot_be_lifted_onto_another_entity():
    """``entity_id`` is inside the signed payload, so lead_A's consent presented
    on lead_B is a valid signature about somebody else."""
    lifted = _blob(_attestation(entity_id="lead_A"))
    world = _rpc_world(
        {"geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: lifted},
        entity_id="lead_B",
    )
    assert _evaluate(world, target="lead_B").verdict == GovernanceVerdict.REJECTED

    # Rewriting entity_id to match breaks the signature instead.
    rewritten = dict(lifted, entity_id="lead_B")
    world = _rpc_world(
        {"geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: rewritten},
        entity_id="lead_B",
    )
    assert _evaluate(world, target="lead_B").verdict == GovernanceVerdict.REJECTED


# --- 4. expiry, against the kernel's own clock ------------------------------


def test_an_expired_attestation_is_unattested():
    stale = _attestation(
        issued_at=utcnow() - timedelta(hours=2),
        expires_at=utcnow() - timedelta(hours=1),
    )
    world = _rpc_world({
        "geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: _blob(stale),
    })
    assert _evaluate(world).verdict == GovernanceVerdict.REJECTED


def test_the_kernel_ceiling_overrides_a_long_issuer_expiry():
    """A compromised issuer must not be able to mint a ten-year consent.

    The attestation is validly signed and its own ``expires_at`` is a decade out,
    but the kernel enforces ``issued_at + max_age`` on top of it.
    """
    long_lived = _attestation(
        issued_at=utcnow() - timedelta(hours=1),
        expires_at=utcnow() + timedelta(days=3650),
    )
    verifier = EVIDENCE_AUTHORITY.verifier()
    assert verifier.max_age < timedelta(hours=1), (
        "max_age is a compliance parameter: it bounds how long a WITHDRAWN "
        "consent may still be acted on, so it must be short"
    )
    world = _rpc_world({
        "geo": "DE", "gdpr_consent": True,
        EVIDENCE_ATTESTATION_PROPERTY: _blob(long_lived),
    })
    assert _evaluate(world).verdict == GovernanceVerdict.REJECTED


def test_a_forward_dated_attestation_cannot_extend_the_ceiling():
    """Dating ``issued_at`` into the future would push ``issued_at + max_age``
    arbitrarily far out. It is not yet valid instead."""
    future = _attestation(
        issued_at=utcnow() + timedelta(hours=6),
        expires_at=utcnow() + timedelta(hours=7),
    )
    world = _rpc_world({
        "geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: _blob(future),
    })
    assert _evaluate(world).verdict == GovernanceVerdict.REJECTED


def test_the_verifier_reads_the_kernel_clock_not_a_caller_supplied_time():
    """``current_time`` is an open-mode convenience. It cannot buy freshness."""
    stale = _blob(_attestation(
        issued_at=utcnow() - timedelta(hours=2),
        expires_at=utcnow() - timedelta(hours=1),
    ))
    world = _rpc_world({
        "geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: stale,
    })
    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=world,
        current_time=utcnow() - timedelta(hours=2),   # "it was fresh then"
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED


# --- 5. issuer trust --------------------------------------------------------


def test_an_unregistered_issuer_is_refused():
    """A perfectly valid Ed25519 signature by a key nobody registered."""
    rogue_private, _rogue_public = generate_keypair()
    rogue = EVIDENCE_AUTHORITY.attest(
        "lead_A",
        {"geo": "DE", "gdpr_consent": True},
        issuer_key_id="rogue_crm",
        private_key_hex=rogue_private,
    )
    world = _rpc_world({
        "geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: rogue,
    })
    assert _evaluate(world).verdict == GovernanceVerdict.REJECTED


def test_a_registered_key_id_signed_by_the_wrong_key_is_refused():
    """Claiming the registered issuer's NAME is not claiming its key."""
    wrong_private, _ = generate_keypair()
    impostor = EVIDENCE_AUTHORITY.attest(
        "lead_A",
        {"geo": "DE", "gdpr_consent": True},
        private_key_hex=wrong_private,
    )
    world = _rpc_world({
        "geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: impostor,
    })
    assert _evaluate(world).verdict == GovernanceVerdict.REJECTED


def test_an_unsigned_attestation_is_refused():
    unsigned = _blob(_attestation())
    unsigned["signature"] = None
    world = _rpc_world({
        "geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: unsigned,
    })
    assert _evaluate(world).verdict == GovernanceVerdict.REJECTED


def test_verify_attestation_raises_rather_than_returning_false():
    """The failure mode is an exception a caller cannot ignore by forgetting a
    ``not``. Mirrors ``verify_profile``."""
    attestation = _attestation()
    verify_attestation(attestation, EVIDENCE_AUTHORITY.registry())
    with pytest.raises(AttestationVerificationError, match="not a registered"):
        verify_attestation(attestation, PublicKeyRegistry())
    with pytest.raises(AttestationVerificationError, match="unsigned"):
        verify_attestation(
            attestation.model_copy(update={"signature": None}),
            EVIDENCE_AUTHORITY.registry(),
        )


# --- 6. the trust root wins over the config blob ----------------------------


def test_an_issuer_registry_in_the_config_blob_is_ignored_when_a_trust_root_exists(
    tmp_path, monkeypatch
):
    """Same reasoning as ``profile_keys``: the agent side authors the whole
    ``world_state`` field, so an issuer registry it also authors would verify
    whatever it chose to sign."""
    profile_private, profile_public = generate_keypair()
    profile = sign_profile(
        ApplicabilityProfile(
            profile_id="prof_tr", tier1_constraints=[], issued_at=datetime(2026, 1, 1)
        ),
        profile_private,
        PROFILE_KEY_ID,
    )
    trust_root = provision_trust_root(
        str(tmp_path / "root"),
        profile_keys={PROFILE_KEY_ID: profile_public},
        evidence_issuers={EVIDENCE_AUTHORITY.key_id: EVIDENCE_AUTHORITY.public_key_hex},
    )
    monkeypatch.setenv(TRUST_ROOT_ENV, trust_root.path)
    assert load_trust_root().evidence_issuers == {
        EVIDENCE_AUTHORITY.key_id: EVIDENCE_AUTHORITY.public_key_hex
    }

    # The agent-authored blob names its OWN issuer and omits the real one.
    agent_private, agent_public = generate_keypair()
    config = dump_governed_config(
        profile,
        PublicKeyRegistry({PROFILE_KEY_ID: profile_public}),
        PublicKeyRegistry({"agent_chosen_issuer": agent_public}),
    )
    kernel = kernel_from_governed_config(config, trust_root=load_trust_root())

    agent_signed = _rpc_world({
        "geo": "DE",
        "gdpr_consent": True,
        EVIDENCE_ATTESTATION_PROPERTY: EVIDENCE_AUTHORITY.attest(
            "lead_A", {"geo": "DE", "gdpr_consent": True},
            issuer_key_id="agent_chosen_issuer", private_key_hex=agent_private,
        ),
    })
    assert _evaluate(agent_signed, kernel=kernel).verdict == GovernanceVerdict.REJECTED

    trust_rooted = _rpc_world({
        "geo": "DE",
        "gdpr_consent": True,
        EVIDENCE_ATTESTATION_PROPERTY: EVIDENCE_AUTHORITY.attest(
            "lead_A", {"geo": "DE", "gdpr_consent": True}
        ),
    })
    assert _evaluate(trust_rooted, kernel=kernel).verdict == GovernanceVerdict.APPROVED


def test_a_trust_root_without_evidence_issuers_accepts_nothing(tmp_path, monkeypatch):
    """A valid, fail-closed state — not a boot failure."""
    profile_private, profile_public = generate_keypair()
    profile = sign_profile(
        ApplicabilityProfile(
            profile_id="prof_bare", tier1_constraints=[], issued_at=datetime(2026, 1, 1)
        ),
        profile_private,
        PROFILE_KEY_ID,
    )
    trust_root = provision_trust_root(
        str(tmp_path / "bare"), profile_keys={PROFILE_KEY_ID: profile_public}
    )
    monkeypatch.setenv(TRUST_ROOT_ENV, trust_root.path)
    kernel = kernel_from_governed_config(
        dump_governed_config(profile, PublicKeyRegistry({PROFILE_KEY_ID: profile_public})),
        trust_root=load_trust_root(),
    )
    world = _rpc_world({
        "geo": "DE",
        "gdpr_consent": True,
        EVIDENCE_ATTESTATION_PROPERTY: EVIDENCE_AUTHORITY.attest(
            "lead_A", {"geo": "DE", "gdpr_consent": True}
        ),
    })
    assert _evaluate(world, kernel=kernel).verdict == GovernanceVerdict.REJECTED


def test_a_malformed_evidence_issuers_map_fails_the_trust_root_closed(tmp_path):
    root = tmp_path / "trust_root.json"
    root.write_text(json.dumps({
        "profile_keys": {PROFILE_KEY_ID: "ab"},
        "evidence_issuers": {"crm": 17},
        "kernel_public_key_hex": "cd",
        "kernel_identity_path": "kernel_identity.json",
    }), encoding="utf-8")
    with pytest.raises(Exception, match="evidence_issuers"):
        load_trust_root(str(root))


# --- 7. withdrawal replay: bounded, not closed ------------------------------


def test_withdrawn_consent_can_be_replayed_until_the_ttl_expires():
    """The honest statement of what this mechanism does about withdrawal.

    An agent captures an attested ``gdpr_consent: true``. The subject withdraws
    at the source of record. The agent re-presents its saved copy. GAP has no
    revocation list and no callback, so the old attestation KEEPS WORKING until
    it ages out — that is a window, not a closure, and the TTL is the only thing
    bounding it.

    This test asserts the bound rather than a guarantee, because the guarantee
    does not exist. GAP is not compliant on immediate withdrawal.
    """
    captured = EVIDENCE_AUTHORITY.attest("lead_A", {"geo": "DE", "gdpr_consent": True})

    # Withdrawal happens at the source of record. Nothing tells GAP.
    replayed = _rpc_world({
        "geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: captured,
    })
    assert _evaluate(replayed).verdict == GovernanceVerdict.APPROVED, (
        "inside the window a replayed attestation still works — say so plainly"
    )

    # The bound: once the kernel's ceiling passes, the same copy is inert.
    aged = _blob(_attestation(
        issued_at=utcnow() - EVIDENCE_AUTHORITY.verifier().max_age - timedelta(seconds=1),
        expires_at=utcnow() + timedelta(days=3650),
    ))
    expired_world = _rpc_world({
        "geo": "DE", "gdpr_consent": True, EVIDENCE_ATTESTATION_PROPERTY: aged,
    })
    assert _evaluate(expired_world).verdict == GovernanceVerdict.REJECTED


def test_the_default_ceiling_is_not_the_decision_ttl():
    """``max_age`` answers a compliance question, not an engineering one, so it
    must not have been copied from the 900s decision TTL."""
    default = EvidenceVerifier(PublicKeyRegistry()).max_age
    assert default != timedelta(seconds=kernel_module._DEFAULT_DECISION_TTL_SECONDS)
    assert timedelta(0) < default <= timedelta(minutes=15)


def test_a_non_positive_ceiling_is_refused():
    with pytest.raises(ValueError, match="max_age must be positive"):
        EvidenceVerifier(PublicKeyRegistry(), max_age=timedelta(0))


# --- the signed payload is a hard boundary ----------------------------------


def test_the_signed_payload_scope_is_pinned():
    """Widening this scope breaks legitimate updates; narrowing it reopens a
    bypass. Either direction must be a deliberate, reviewed change.

    In particular ``entity_id`` stops the cross-entity lift and ``properties``
    stops value substitution, while the entity's ordinary keys (``name``,
    ``value``, ``status``, ``last_contacted``) stay OUT because executors write
    them by design.
    """
    assert SIGNED_ATTESTATION_FIELDS == (
        "attestation_id", "entity_id", "properties",
        "issued_at", "expires_at", "issuer_key_id",
    )
    assert set(SIGNED_ATTESTATION_FIELDS) | {"signature"} == set(
        EvidenceAttestation.model_fields
    ), "a new model field is outside the signature until someone decides otherwise"

    canonical = attestation_payload(_attestation())
    payload = json.loads(canonical)
    assert payload["_domain"] == "gap.evidence_attestation.v1"
    assert set(payload) == set(SIGNED_ATTESTATION_FIELDS) | {"_domain"}
    assert canonical == json.dumps(payload, sort_keys=True, default=str), (
        "sort_keys=True, exactly as canonical_decision_payload does it"
    )


def test_only_governance_relevant_keys_are_bound():
    """Binding the whole property bag would invalidate the attestation on every
    ordinary executor write — the over-scoping failure."""
    minted = EVIDENCE_AUTHORITY.signer().mint("lead_A", {
        "geo": "DE", "gdpr_consent": True,
        "name": "EU Lead", "value": 50000, "last_contacted": None,
    })
    assert set(minted.properties) == {"geo", "gdpr_consent"}


def test_an_ordinary_property_change_does_not_invalidate_an_attestation():
    attestation = EVIDENCE_AUTHORITY.attest("lead_A", {"geo": "DE", "gdpr_consent": True})
    world = _rpc_world({
        "geo": "DE", "gdpr_consent": True, "name": "renamed", "value": 999,
        "last_contacted": utcnow().isoformat(),
        EVIDENCE_ATTESTATION_PROPERTY: attestation,
    })
    assert _evaluate(world).verdict == GovernanceVerdict.APPROVED


def test_a_signature_over_this_payload_cannot_be_replayed_as_a_decision():
    """Domain separation, same as ``canonical_decision_payload``."""
    assert "gap.evidence_attestation.v1" in attestation_payload(_attestation())
    assert "gap.governance.decision" not in attestation_payload(_attestation())


# --- posture ----------------------------------------------------------------


def test_a_governed_kernel_without_issuers_rejects_every_world_model_gate():
    """Correct fail-closed behaviour that WILL surprise prototype users.

    ``GovernanceKernel(governed=True, applicability_profile=...)`` with no issuer
    registry requires attestation and has nothing that can verify one, so every
    world-model-backed proposal now rejects — including one carrying a perfectly
    good signature from an issuer this kernel was never told about.
    """
    kernel = _governed_kernel(evidence_issuers=PublicKeyRegistry())
    world = _rpc_world({
        "geo": "DE",
        "gdpr_consent": True,
        EVIDENCE_ATTESTATION_PROPERTY: EVIDENCE_AUTHORITY.attest(
            "lead_A", {"geo": "DE", "gdpr_consent": True}
        ),
    })
    assert _evaluate(world, kernel=kernel).verdict == GovernanceVerdict.REJECTED


def test_the_no_issuer_warning_names_the_consequence(caplog):
    private_hex, public_hex = generate_keypair()
    profile = sign_profile(
        ApplicabilityProfile(
            profile_id="p_warn", tier1_constraints=[], issued_at=datetime(2026, 1, 1)
        ),
        private_hex,
        PROFILE_KEY_ID,
    )
    with caplog.at_level(logging.WARNING, logger="gap_kernel.governance"):
        GovernanceKernel(
            governed=True,
            applicability_profile=profile,
            profile_key_registry=PublicKeyRegistry({PROFILE_KEY_ID: public_hex}),
        )
    warnings = " ".join(record.getMessage() for record in caplog.records)
    assert "no evidence issuers" in warnings
    assert "unevaluable" in warnings


def test_the_open_posture_has_no_verifier_at_all():
    """``None`` is the open posture and is NOT the same thing as an empty
    registry, which is the fail-closed one."""
    assert GovernanceKernel()._evidence_verifier is None
    assert GovernanceKernel()._require_attested_evidence is False
    assert _governed_kernel()._require_attested_evidence is True
    assert isinstance(_governed_kernel()._evidence_verifier, EvidenceVerifier)


def test_open_mode_still_rules_on_unsigned_facts():
    """Prototypes have no consent-of-record system. Unchanged."""
    decision = GovernanceKernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=_rpc_world({"geo": "DE", "gdpr_consent": True}),
    )
    assert decision.verdict == GovernanceVerdict.APPROVED


def test_the_pure_function_form_of_the_bug_is_gone():
    """``evidence_is_attested(entity, key)`` could only ever read what the agent
    sent, so it is the bug in functional form. It must not survive as a public
    callable that a future evaluator could reach for."""
    import gap_kernel.world_model.store as store_module

    assert not hasattr(store_module, "evidence_is_attested")
    assert not hasattr(store_module, "attested_properties")


def test_contact_hours_is_hardened_too():
    """The second — and last — world-model-backed evaluator."""
    hours_intent = IntentVector(
        id="i1", objective="Respect local contact hours", priority=80,
        hard_constraints=[Constraint(
            name="no_contact_outside_hours", type=ConstraintType.HARD,
            description="No automated outreach 10PM-7AM lead local time",
        )],
        soft_constraints=[], created_by="human", created_at=utcnow(),
    )
    kernel = _governed_kernel()

    unsigned = kernel.evaluate_proposal(
        proposal=_outreach(), intents=[hours_intent],
        world_state=_rpc_world({"local_hour": 14}),
        action_type_id="task_execution",
    )
    assert unsigned.verdict == GovernanceVerdict.REJECTED

    signed = kernel.evaluate_proposal(
        proposal=_outreach(), intents=[hours_intent],
        world_state=_rpc_world({
            "local_hour": 14,
            EVIDENCE_ATTESTATION_PROPERTY: EVIDENCE_AUTHORITY.attest(
                "lead_A", {"local_hour": 14}
            ),
        }),
        action_type_id="task_execution",
    )
    assert signed.verdict == GovernanceVerdict.APPROVED

    # Attested and outside the window: readable, not favourable.
    late = kernel.evaluate_proposal(
        proposal=_outreach(), intents=[hours_intent],
        world_state=_rpc_world({
            "local_hour": 23,
            EVIDENCE_ATTESTATION_PROPERTY: EVIDENCE_AUTHORITY.attest(
                "lead_A", {"local_hour": 23}
            ),
        }),
        action_type_id="task_execution",
    )
    assert late.verdict == GovernanceVerdict.REJECTED


# --- the weakest seam: the hand-maintained key set --------------------------


class _PropertyKeyReader(ast.NodeVisitor):
    """Collect every literal key read off an entity's ``properties`` mapping.

    Seeded with the parameter names the kernel uses for a raw property mapping
    (``props``, ``properties``), and extended with anything assigned from an
    expression ending in ``.properties``.
    """

    def __init__(self):
        self.bound = {"props", "properties"}
        self.keys: set = set()

    # ``props = entity.properties``
    def visit_Assign(self, node):
        if self._is_properties(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bound.add(target.id)
        self.generic_visit(node)

    # ``entity.properties["geo"]`` / ``props["geo"]``
    def visit_Subscript(self, node):
        if self._is_properties(node.value) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                self.keys.add(node.slice.value)
        self.generic_visit(node)

    # ``props.get("geo", ...)``
    def visit_Call(self, node):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and self._is_properties(func.value)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            self.keys.add(node.args[0].value)
        self.generic_visit(node)

    # ``"geo" in properties``
    def visit_Compare(self, node):
        if (
            isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and any(isinstance(op, ast.In) for op in node.ops)
            and any(self._is_properties(c) for c in node.comparators)
        ):
            self.keys.add(node.left.value)
        self.generic_visit(node)

    def _is_properties(self, node) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "properties":
            return True
        return isinstance(node, ast.Name) and node.id in self.bound


def test_every_property_an_evaluator_reads_is_governance_relevant():
    """The cheapest defence of the design's weakest seam.

    ``GOVERNANCE_RELEVANT_PROPERTIES`` is hand-maintained, and its failure mode
    is fail-OPEN: an evaluator author who reads a new key off ``entity.properties``
    and never adds it here gets a HARD constraint ruling on a fact with no
    attestation behind it, silently, with no test failing. So the set is
    re-derived here from the kernel's own source rather than trusted.

    If this fails, the fix is almost never to edit the assertion. It is either to
    add the key to ``GOVERNANCE_RELEVANT_PROPERTIES`` (and mint attestations over
    it) or to stop reading it in an evaluator.
    """
    reader = _PropertyKeyReader()
    reader.visit(ast.parse(inspect.getsource(kernel_module)))

    assert reader.keys, "the scanner found nothing — it has stopped scanning"
    assert reader.keys <= set(GOVERNANCE_RELEVANT_PROPERTIES), (
        f"the governance kernel reads {sorted(reader.keys - set(GOVERNANCE_RELEVANT_PROPERTIES))} "
        f"off an entity's properties, and those keys carry no attestation "
        f"requirement. A HARD constraint would rule on a fact nobody signed."
    )


def test_the_two_hardened_evaluators_are_the_only_world_model_backed_ones():
    """Stated in the same breath as every attestation claim: this hardens TWO of
    nine. The other seven rule on agent-authored ``action.parameters`` or
    ``estimated_cost``, five of them behind a flag the agent sets — omit
    ``safety_critical`` and the physical-safety check never runs."""
    source_by_name = {
        name: inspect.getsource(fn)
        for name, fn in kernel_module._CONSTRAINT_EVALUATORS.items()
    }
    assert len(source_by_name) == 9
    world_backed = {
        name for name, src in source_by_name.items()
        if "world_state.entities" in src
    }
    assert world_backed == {"gdpr_consent_required", "no_contact_outside_hours"}
    verifier_users = {
        name for name, src in source_by_name.items()
        if "evidence_verifier.attests" in src
    }
    assert verifier_users == world_backed, (
        "an evaluator that reads the world model without calling the verifier "
        "rules on an unsigned fact"
    )


class TestEntityIdentityBinding:
    """An attestation binds an entity_id; the kernel resolves a target by map key.

    Those are two different strings, and an agent-authored world model supplies
    both. Filing a genuinely attested entity under a different map key and
    targeting that key therefore lifted one real consent record onto arbitrarily
    many invented targets, without touching the signed blob at all — the
    attestation verified against the field while the gate read the entity under
    the key. `WorldModelStore` keys by `entity.entity_id` and cannot produce the
    divergence, so only the RPC path was exposed, which is the path this
    mechanism exists to defend.
    """

    def test_an_entity_filed_under_a_foreign_key_is_refused(self):
        with pytest.raises(ValidationError, match="keyed by their own entity_id"):
            WorldModel.model_validate({
                "entities": {
                    "victim_lead": {
                        "entity_type": "lead",
                        "entity_id": "lead_A",
                        "properties": {"geo": "DE", "gdpr_consent": True},
                        "last_updated": utcnow().isoformat(),
                        "source": "agent_supplied",
                        "obligations": [],
                    }
                },
                "last_reconciled": utcnow().isoformat(),
            })

    def test_the_clone_variant_is_refused(self):
        # The real lead present AND a second copy filed under an invented key,
        # which is how one attestation laundered outreach to many targets.
        genuine = EVIDENCE_AUTHORITY.attest(
            "lead_A", {"geo": "DE", "gdpr_consent": True, "local_hour": 14}
        )
        payload = {
            "entity_type": "lead",
            "entity_id": "lead_A",
            "properties": {
                "geo": "DE", "gdpr_consent": True, "local_hour": 14,
                EVIDENCE_ATTESTATION_PROPERTY: genuine,
            },
            "last_updated": utcnow().isoformat(),
            "source": "agent_supplied",
            "obligations": [],
        }
        with pytest.raises(ValidationError, match="keyed by their own entity_id"):
            WorldModel.model_validate({
                "entities": {"lead_A": payload, "victim_no_consent": payload},
                "last_reconciled": utcnow().isoformat(),
            })

    def test_a_correctly_keyed_entity_still_works(self):
        # The positive control: this constraint must not break the honest path.
        world = _rpc_world(
            {
                "geo": "DE", "gdpr_consent": True, "local_hour": 14,
                EVIDENCE_ATTESTATION_PROPERTY: EVIDENCE_AUTHORITY.attest(
                    "lead_A", {"geo": "DE", "gdpr_consent": True, "local_hour": 14}
                ),
            }
        )
        assert _evaluate(world).verdict == GovernanceVerdict.APPROVED


class TestContactActionClassificationFailsClosed:
    """Renaming an action must not walk around the gate it would fail.

    Both world-model-backed gates turned on an allowlist of four exact literals,
    so an EU lead with no consent and no attestation was APPROVED under
    `action_type` of `email`, `outreach`, `SEND_EMAIL`, or `send_email ` with a
    trailing space. The classification is an input to a HARD constraint, so an
    unknown type has to count as contact — otherwise the attestation protects a
    door with no walls.
    """

    @pytest.mark.parametrize(
        "action_type",
        ["send_email", "email", "SEND_EMAIL", "send_email ", "outreach", "e-mail"],
    )
    def test_an_unknown_action_type_is_treated_as_contact(self, action_type):
        world = _rpc_world({"geo": "DE", "gdpr_consent": False})
        decision = _governed_kernel().evaluate_proposal(
            proposal=_outreach(action_type=action_type),
            intents=[_gdpr_intent()],
            world_state=world,
            action_type_id="task_execution",
        )
        assert decision.verdict == GovernanceVerdict.REJECTED

    @pytest.mark.parametrize("action_type", ["query_crm", "route_to_human", "log_event"])
    def test_a_known_non_contact_action_is_not_gated(self, action_type):
        # The positive control: fail-closed must not become reject-everything,
        # or the CGA loop can never find its way to an approved plan.
        world = _rpc_world({"geo": "DE", "gdpr_consent": False})
        decision = _governed_kernel().evaluate_proposal(
            proposal=_outreach(action_type=action_type),
            intents=[_gdpr_intent()],
            world_state=world,
            action_type_id="task_execution",
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
