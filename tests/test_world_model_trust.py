"""The world model is EVIDENCE, not telemetry.

The regulatory constraint evaluators in the Governance Kernel rule on facts the
world model carries — a lead's jurisdiction, its GDPR consent, its local hour.
Anything that can write those facts can decide the verdict, so these tests assert:

  * governance-relevant properties are only usable when they arrive with attested
    provenance, and a governed kernel fails closed on unattested evidence
    (mirroring the rule that an unevaluable constraint is a violation);
  * the store is the only writer of that provenance — a caller cannot declare
    itself attested;
  * governance-relevant mutations (a consent flip) are recorded;
  * ordinary, non-governance properties stay freely ingestible;
  * the shipped HTTP surface is read/evaluate only, and mutation is an explicit
    deployment opt-in behind the deployment's own authenticated proxy;
  * importing the API module does not build an ungoverned application.
"""

import logging
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gap_kernel._time import utcnow
from gap_kernel.api.app import create_app
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.world_model.store import (
    EVIDENCE_ATTESTATION_PROPERTY,
    EVIDENCE_PROPERTY,
    GOVERNANCE_RELEVANT_PROPERTIES,
    WorldModelStore,
)
from tests.conftest import EVIDENCE_AUTHORITY

KEY_ID = "regulatory_authority"

# The shared issuer (tests/conftest.py). This channel carries a signer, so the
# store mints a signed attestation for every entity written on it — the
# prototype topology. In production the source of record signs and GAP only
# verifies.
CRM_ATTESTED = EVIDENCE_AUTHORITY.channel(
    channel_id="crm_verified_consent_feed",
    description="Authenticated consent-of-record feed",
)


def _signed_profile():
    private_hex, public_hex = generate_keypair()
    registry = PublicKeyRegistry({KEY_ID: public_hex})
    profile = ApplicabilityProfile(
        profile_id="prof_world_trust",
        tier1_constraints=[],
        issued_at=datetime(2026, 1, 1),
    )
    return sign_profile(profile, private_hex, KEY_ID), registry


def _governed_kernel() -> GovernanceKernel:
    profile, registry = _signed_profile()
    return GovernanceKernel(
        governed=True,
        applicability_profile=profile,
        profile_key_registry=registry,
        evidence_issuers=EVIDENCE_AUTHORITY.registry(),
    )


def _gdpr_intent() -> IntentVector:
    return IntentVector(
        id="intent_gdpr",
        objective="Respond to high-value leads within 10 minutes",
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


def _hours_intent() -> IntentVector:
    return IntentVector(
        id="intent_hours",
        objective="Respect local contact hours",
        priority=80,
        hard_constraints=[
            Constraint(
                name="no_contact_outside_hours",
                type=ConstraintType.HARD,
                description="No automated outreach 10PM-7AM lead local time",
            )
        ],
        soft_constraints=[],
        created_by="human",
        created_at=utcnow(),
    )


def _outreach(target="lead_eu_1", intent_id="intent_gdpr") -> StrategyProposal:
    return StrategyProposal(
        id="prop_outreach",
        intent_id=intent_id,
        attempt_number=1,
        plan_description="Send the follow-up",
        actions=[
            PlannedAction(
                action_type="send_email", target=target, parameters={}, risk_score=1
            )
        ],
        estimated_cost=0.01,
        rationale="lead is waiting",
        generated_at=utcnow(),
    )


def _lead(entity_id="lead_eu_1", **properties) -> EntityState:
    return EntityState(
        entity_type="lead",
        entity_id=entity_id,
        properties=dict(properties),
        last_updated=utcnow(),
        source="crm",
        obligations=[],
    )


# --- A1: the reproduced consent flip, in the governed + isolated posture -----


def _governed_client():
    """The shipped governed posture: signed floor, kernel out of process."""
    profile, registry = _signed_profile()
    app = create_app(
        applicability_profile=profile,
        profile_key_registry=registry,
        evidence_issuers=EVIDENCE_AUTHORITY.registry(),
        enable_mutating_routes=True,
    )
    return TestClient(app)


def _ingest(client, properties, entity_id="lead_eu_1"):
    return client.post("/world/ingest", json={
        "entity_type": "lead",
        "entity_id": entity_id,
        "properties": properties,
        "source": "attacker_controlled",
    })


def _evaluate(client, intent_id):
    payload = _outreach(intent_id=intent_id).model_dump(mode="json")
    return client.post("/governance/evaluate", json={
        "proposal": payload,
        "intent_ids": [intent_id],
        "action_type_id": "task_execution",
    }).json()


def _declare_gdpr_intent(client) -> str:
    response = client.post("/intents", json={
        "objective": "Respond to high-value leads within 10 minutes",
        "priority": 80,
        "hard_constraints": [{
            "name": "gdpr_consent_required",
            "description": "Verify GDPR consent before any direct outreach to EU leads",
        }],
        "created_by": "human",
    })
    assert response.status_code == 200
    return response.json()["id"]


def test_http_consent_flip_cannot_buy_a_signed_approval():
    """An unauthenticated POST /world/ingest could flip ``gdpr_consent`` to True
    and turn the identical rejected proposal into a kernel-signed APPROVED.
    Ingested facts carry no signature, so the HARD constraint is unevaluable —
    which is a violation, before and after the flip.

    NOT EVIDENCE THAT SIGNED EVIDENCE ATTESTATION LANDED. This test passes
    identically whether SEA works or is a complete no-op, because the ingest path
    goes through ``WorldModelStore``, which never attested an HTTP write in the
    first place. Believing this test verified the fix is exactly the mistake that
    let the forgeable-stamp critical be recorded as closed for a whole release.
    The tests that would actually fail against a no-op are the RPC-path ones in
    ``tests/test_evidence_attestation.py``, starting with
    ``test_the_exact_forged_stamp_no_longer_buys_an_approval``.
    """
    client = _governed_client()
    try:
        intent_id = _declare_gdpr_intent(client)

        assert _ingest(client, {"geo": "DE", "gdpr_consent": False}).status_code == 200
        first = _evaluate(client, intent_id)
        assert first["verdict"] == "rejected"
        assert "gdpr_consent_required" in first["violated_constraints"]

        # The flip: same entity, consent now True.
        assert _ingest(client, {"geo": "DE", "gdpr_consent": True}).status_code == 200
        after_flip = _evaluate(client, intent_id)
        assert after_flip["verdict"] == "rejected"
        assert "gdpr_consent_required" in after_flip["violated_constraints"]
    finally:
        client.app.state.governance_client.close()


def test_http_jurisdiction_rewrite_cannot_skip_the_eu_branch():
    """The cheaper variant: set ``geo`` outside the EU so the consent branch is
    never entered at all. An unattested jurisdiction cannot certify the gate
    does not apply.

    Same caveat as the test above — this exercises the ingest path, which the
    store has always guarded, so it passes identically whether SEA works or is a
    no-op. It is not evidence the fix landed.
    """
    client = _governed_client()
    try:
        intent_id = _declare_gdpr_intent(client)
        assert _ingest(client, {"geo": "DE", "gdpr_consent": False}).status_code == 200
        assert _evaluate(client, intent_id)["verdict"] == "rejected"

        assert _ingest(client, {"geo": "US"}).status_code == 200
        rewritten = _evaluate(client, intent_id)
        assert rewritten["verdict"] == "rejected"
        assert "gdpr_consent_required" in rewritten["violated_constraints"]
    finally:
        client.app.state.governance_client.close()


# --- A1: the trust model, at the kernel/store boundary ----------------------


def test_governed_kernel_rejects_unattested_consent():
    """An entity whose consent never came through an attested channel leaves the
    GDPR gate with nothing it can certify."""
    store = WorldModelStore()
    store.upsert_entity(_lead(geo="DE", gdpr_consent=True))
    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "gdpr_consent_required" in decision.violated_constraints


def test_governed_kernel_approves_attested_consent():
    """The fix is a trust model, not a blanket denial: the same facts arriving on
    an attested channel are usable evidence and the proposal is approved."""
    store = WorldModelStore()
    store.upsert_entity(
        _lead(geo="DE", gdpr_consent=True, local_hour=14), channel=CRM_ATTESTED
    )
    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.APPROVED


def test_attested_consent_of_false_still_rejects():
    """Attestation makes the evidence readable, not favourable."""
    store = WorldModelStore()
    store.upsert_entity(_lead(geo="DE", gdpr_consent=False), channel=CRM_ATTESTED)
    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "gdpr_consent_required" in decision.violated_constraints


def test_governed_kernel_rejects_unattested_local_hour():
    """The contact-hours gate reads ``local_hour`` off the world model, so it is
    governance-relevant evidence on the same footing as consent."""
    store = WorldModelStore()
    store.upsert_entity(_lead(local_hour=14))
    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(intent_id="intent_hours"),
        intents=[_hours_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "no_contact_outside_hours" in decision.violated_constraints


def test_attested_local_hour_is_evaluated_normally():
    store = WorldModelStore()
    store.upsert_entity(_lead(local_hour=23), channel=CRM_ATTESTED)
    inside_window = WorldModelStore()
    inside_window.upsert_entity(_lead(local_hour=14), channel=CRM_ATTESTED)

    kernel = _governed_kernel()
    late = kernel.evaluate_proposal(
        proposal=_outreach(intent_id="intent_hours"),
        intents=[_hours_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    ok = kernel.evaluate_proposal(
        proposal=_outreach(intent_id="intent_hours"),
        intents=[_hours_intent()],
        world_state=inside_window.model,
        action_type_id="task_execution",
    )
    assert late.verdict == GovernanceVerdict.REJECTED
    assert ok.verdict == GovernanceVerdict.APPROVED


def test_open_mode_keeps_the_permissive_evidence_posture():
    """Open/prototype deployments are unchanged — the governed posture is what
    fails closed, and it is the default whenever a floor is loaded."""
    store = WorldModelStore()
    store.upsert_entity(_lead(geo="DE", gdpr_consent=True))
    decision = GovernanceKernel().evaluate_proposal(
        proposal=_outreach(), intents=[_gdpr_intent()], world_state=store.model
    )
    assert decision.verdict == GovernanceVerdict.APPROVED


def test_attestation_can_be_waived_only_by_naming_it():
    kernel_kwargs = dict(require_attested_evidence=False, allow_untracked_targets=True)
    profile, registry = _signed_profile()
    kernel = GovernanceKernel(
        governed=True,
        applicability_profile=profile,
        profile_key_registry=registry,
        evidence_issuers=EVIDENCE_AUTHORITY.registry(),
        **kernel_kwargs,
    )
    store = WorldModelStore()
    store.upsert_entity(_lead(geo="DE", gdpr_consent=True))
    decision = kernel.evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.APPROVED


def test_governed_kernel_requires_attested_evidence_by_default():
    assert _governed_kernel()._require_attested_evidence is True
    assert GovernanceKernel()._require_attested_evidence is False


# --- A1: the kernel owns verification, the store only carries ---------------


def test_store_carries_a_caller_supplied_attestation_and_the_kernel_rejects_it():
    """This assertion is the INVERSE of the one it replaces, for the same outcome.

    The old test asserted the store DISCARDED a caller-supplied stamp — which it
    did, and which bought nothing, because a caller that assembles the
    ``WorldModel`` itself never reaches the store at all. So the store no longer
    discards: it CARRIES the blob, shape-checked and verbatim, because in
    production the blob is minted outside this process by the source of record
    and the store has no issuer registry to check it with.

    Carrying it is safe precisely because it is worthless without a signature the
    presenter cannot produce. Same rejection, opposite mechanism.
    """
    store = WorldModelStore()
    forged = {
        "attestation_id": "att_forged",
        "entity_id": "lead_eu_1",
        "properties": {"geo": "DE", "gdpr_consent": True},
        "issued_at": utcnow().isoformat(),
        "expires_at": (utcnow() + timedelta(days=3650)).isoformat(),
        "issuer_key_id": "totally_a_real_crm",
        "signature": "00" * 64,
    }
    store.upsert_entity(
        _lead(geo="DE", gdpr_consent=True, **{EVIDENCE_ATTESTATION_PROPERTY: forged})
    )

    carried = store.get_entity("lead_eu_1").properties[EVIDENCE_ATTESTATION_PROPERTY]
    assert carried == forged, "the store carries it; the store is not the guard"

    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED
    assert "gdpr_consent_required" in decision.violated_constraints


def test_a_malformed_attestation_blob_is_dropped_rather_than_carried():
    """Shape check only — it could never verify, and carrying it would put noise
    in every world-state snapshot."""
    store = WorldModelStore()
    store.upsert_entity(
        _lead(geo="DE", **{EVIDENCE_ATTESTATION_PROPERTY: {"nonsense": True}})
    )
    assert EVIDENCE_ATTESTATION_PROPERTY not in store.get_entity("lead_eu_1").properties


def test_execution_updates_cannot_launder_governance_properties():
    """An executor writing back an outcome cannot make the new value count.

    The signature binds the VALUE it was issued over, so flipping ``gdpr_consent``
    after the fact leaves the attestation vouching for ``False`` while the entity
    carries ``True``. The kernel's canonical-JSON value check fails, the fact is
    unattested, and the gate has nothing to certify. Restoring standing needs a
    new signature, which needs the issuer key.
    """
    store = WorldModelStore()
    store.upsert_entity(_lead(geo="DE", gdpr_consent=False), channel=CRM_ATTESTED)
    store.update_from_execution(
        "lead_eu_1",
        {
            "gdpr_consent": True,
            EVIDENCE_PROPERTY: {"attested": True,
                                "governance_properties": ["gdpr_consent"]},
        },
    )

    entity = store.get_entity("lead_eu_1")
    assert entity.properties["gdpr_consent"] is True, (
        "the write itself is allowed; what it must lose is attested standing"
    )
    signed = entity.properties[EVIDENCE_ATTESTATION_PROPERTY]["properties"]
    assert signed["gdpr_consent"] is False, "the signature still binds the old value"

    verifier = EVIDENCE_AUTHORITY.verifier()
    assert not verifier.attests(entity, "gdpr_consent")
    assert verifier.attests(entity, "geo"), "an untouched key keeps its standing"

    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED


def test_governance_property_mutations_are_recorded():
    """A consent flip is at minimum auditable, whichever channel made it."""
    store = WorldModelStore()
    store.upsert_entity(_lead(geo="DE", gdpr_consent=False), channel=CRM_ATTESTED)
    store.upsert_entity(_lead(geo="DE", gdpr_consent=True))

    mutations = store.governance_property_mutations()
    flips = [m for m in mutations if m["property"] == "gdpr_consent"]
    assert len(flips) == 2
    assert flips[-1]["previous_value"] is False
    assert flips[-1]["new_value"] is True
    assert flips[-1]["attested"] is False
    assert flips[-1]["entity_id"] == "lead_eu_1"


def test_ordinary_properties_stay_ingestible_and_unstamped():
    """Non-governance telemetry is exactly that — the reconciler keeps working."""
    store = WorldModelStore()
    created = (utcnow() - timedelta(minutes=8)).isoformat()
    store.upsert_entity(_lead(name="EU Lead", value=50000, created_at=created))

    entity = store.get_entity("lead_eu_1")
    assert entity.properties["value"] == 50000
    assert entity.properties["created_at"] == created
    assert EVIDENCE_PROPERTY not in entity.properties
    assert store.governance_property_mutations() == []


def test_governance_relevant_set_covers_every_property_the_kernel_reads():
    """The declared set is derived from the evaluators, not guessed at."""
    assert GOVERNANCE_RELEVANT_PROPERTIES == frozenset(
        {"gdpr_consent", "geo", "jurisdiction", "local_hour"}
    )


def test_jurisdiction_alias_is_governance_relevant():
    """``jurisdiction`` is the fallback key the GDPR gate reads when ``geo`` is
    absent, so it carries the same trust requirement."""
    store = WorldModelStore()
    store.upsert_entity(_lead(jurisdiction="DE", gdpr_consent=True))
    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED

    attested = WorldModelStore()
    attested.upsert_entity(
        _lead(jurisdiction="DE", gdpr_consent=True), channel=CRM_ATTESTED
    )
    assert _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=attested.model,
        action_type_id="task_execution",
    ).verdict == GovernanceVerdict.APPROVED


def test_unattested_rejection_names_the_evidence_problem():
    store = WorldModelStore()
    store.upsert_entity(_lead(geo="DE", gdpr_consent=True))
    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=store.model,
        action_type_id="task_execution",
    )
    assert "attested" in decision.rejection_detail


def test_an_entity_never_written_through_the_store_is_unattested():
    """A WorldModel assembled by hand carries no provenance, so it fails closed."""
    world = WorldModel(
        entities={"lead_eu_1": _lead(geo="DE", gdpr_consent=True)},
        last_reconciled=utcnow(),
    )
    decision = _governed_kernel().evaluate_proposal(
        proposal=_outreach(),
        intents=[_gdpr_intent()],
        world_state=world,
        action_type_id="task_execution",
    )
    assert decision.verdict == GovernanceVerdict.REJECTED


# --- A2: the shipped HTTP surface is read/evaluate only ---------------------


def _routes(app) -> set:
    return {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }


def test_action_type_registration_route_is_gone():
    """The Action Type Registry rides in the signed Applicability Profile. There
    is no HTTP path to it in either posture."""
    for app in (create_app(), create_app(enable_mutating_routes=True)):
        assert ("POST", "/governance/action-types") not in _routes(app)
    assert TestClient(create_app()).post(
        "/governance/action-types", json={"type_id": "x", "description": "d"}
    ).status_code == 405


def test_mutating_routes_are_absent_by_default():
    routes = _routes(create_app())
    for mutation in (
        ("POST", "/intents"),
        ("PUT", "/intents/{intent_id}"),
        ("DELETE", "/intents/{intent_id}"),
        ("POST", "/world/ingest"),
        ("PUT", "/reconciler/config"),
        ("POST", "/reconciler/trigger"),
        ("POST", "/escalations/{escalation_id}/resolve"),
    ):
        assert mutation not in routes


def test_read_and_evaluate_routes_ship_by_default():
    routes = _routes(create_app())
    for readable in (
        ("GET", "/world/state"),
        ("GET", "/intents"),
        ("GET", "/governance/policies"),
        ("GET", "/governance/action-types"),
        ("GET", "/lineage"),
        ("POST", "/governance/evaluate"),
    ):
        assert readable in routes


def test_unauthenticated_caller_cannot_strip_a_hard_constraint():
    """PUT /intents/{id} let any caller replace an intent's HARD constraint set."""
    client = TestClient(create_app())
    assert client.put("/intents/intent_1", json={"objective": "o"}).status_code == 405
    assert client.delete("/intents/intent_1").status_code == 405
    # /world/ingest has no read counterpart, so the path itself is simply absent.
    assert client.post("/world/ingest", json={
        "entity_type": "lead", "entity_id": "e", "properties": {},
    }).status_code == 404


def test_mutating_routes_are_registered_when_a_deployment_opts_in():
    routes = _routes(create_app(enable_mutating_routes=True))
    assert ("POST", "/intents") in routes
    assert ("POST", "/world/ingest") in routes


def test_constraint_threshold_and_tier_survive_the_http_round_trip():
    """The API could not express ``threshold`` or ``tier`` at all, so a constraint
    created over HTTP silently lost its numeric floor."""
    client = TestClient(create_app(enable_mutating_routes=True))
    created = client.post("/intents", json={
        "objective": "Screen payments",
        "priority": 60,
        "hard_constraints": [{
            "name": "aml_screening_required",
            "description": "Screen transactions over $10,000",
            "threshold": 10000.0,
            "tier": 2,
        }],
        "created_by": "human",
    })
    assert created.status_code == 200
    constraint = created.json()["intent"]["hard_constraints"][0]
    assert constraint["threshold"] == 10000.0
    assert constraint["tier"] == 2

    fetched = client.get(f"/intents/{created.json()['id']}").json()
    assert fetched["hard_constraints"][0]["threshold"] == 10000.0


def test_http_cannot_mint_a_regulatory_floor_constraint():
    """Tier 1 is the signed floor. Claiming it over HTTP is refused."""
    client = TestClient(create_app(enable_mutating_routes=True))
    response = client.post("/intents", json={
        "objective": "Claim the floor",
        "priority": 60,
        "hard_constraints": [{"name": "cost_ceiling", "description": "$5.00", "tier": 1}],
        "created_by": "human",
    })
    assert response.status_code == 400


def test_malformed_constraint_payloads_fail_closed():
    client = TestClient(create_app(enable_mutating_routes=True))
    base = {"objective": "o", "priority": 50, "created_by": "human"}
    assert client.post("/intents", json={
        **base, "hard_constraints": [{"description": "no name"}],
    }).status_code == 400
    assert client.post("/intents", json={
        **base, "hard_constraints": [{"name": "cost_ceiling", "tier": 9}],
    }).status_code == 400


# --- A3: importing the module must not build an ungoverned app --------------


def test_importing_the_module_does_not_build_an_ungoverned_app():
    import gap_kernel.api.app as app_module

    assert "app" not in vars(app_module)


def test_materializing_the_module_level_app_warns_loudly(caplog):
    import gap_kernel.api.app as app_module

    vars(app_module).pop("app", None)
    with caplog.at_level(logging.WARNING, logger="gap_kernel.api"):
        materialized = app_module.app
    assert isinstance(materialized, type(create_app()))
    warnings = " ".join(record.getMessage() for record in caplog.records)
    assert "UNGOVERNED" in warnings
    assert "regulatory floor" in warnings
    vars(app_module).pop("app", None)


def test_unknown_module_attribute_still_raises():
    import gap_kernel.api.app as app_module

    with pytest.raises(AttributeError):
        app_module.not_a_real_attribute
