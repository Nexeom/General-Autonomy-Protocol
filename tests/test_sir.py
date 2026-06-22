"""Tests for Structured Intent Resolution (SIR)."""

from datetime import datetime, timedelta

import pytest

from gap_kernel.crypto.signing import generate_keypair
from gap_kernel.governance.sir import StandingIntentError, StructuredIntentResolver
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.sir import (
    ConfirmationState,
    IntentDeclaration,
    MetaIntent,
    StandingIntentDeclaration,
)

NOW = datetime(2026, 6, 22, 12, 0, 0)


def _resolver():
    return StructuredIntentResolver()


# --- SIR-1: Intent Declaration ---------------------------------------------

def test_resolve_produces_five_component_declaration():
    decl = _resolver().resolve("Respond to high-value leads", AuthorizationLevel.L1)
    assert decl.stated_intent == "Respond to high-value leads"
    assert decl.interpreted_intent  # defaulted from stated
    assert isinstance(decl.meta_intent, MetaIntent)
    assert decl.declared_boundaries == []
    assert decl.confirmation_state == ConfirmationState.PENDING


# --- SIR-3: proportional resolution ----------------------------------------

@pytest.mark.parametrize("level,mode", [
    (AuthorizationLevel.L0, "standing_declaration"),
    (AuthorizationLevel.L1, "structured_confirmation"),
    (AuthorizationLevel.L2, "collaborative_specification"),
    (AuthorizationLevel.L3, "full_negotiation"),
    (AuthorizationLevel.L4, "full_negotiation"),
])
def test_resolution_mode_scales_with_level(level, mode):
    assert StructuredIntentResolver.resolution_mode(level) == mode


def test_requires_confirmation_only_above_l0():
    r = _resolver()
    assert r.requires_confirmation(AuthorizationLevel.L0) is False
    assert r.requires_confirmation(AuthorizationLevel.L1) is True
    assert r.requires_confirmation(AuthorizationLevel.L3) is True


# --- readiness gate ---------------------------------------------------------

def test_l1_pending_is_not_ready_until_confirmed():
    r = _resolver()
    decl = r.resolve("do the thing", AuthorizationLevel.L2)
    assert r.is_ready_for_cga(decl) is False
    assert r.is_ready_for_cga(r.confirm(decl)) is True


def test_corrected_declaration_is_ready():
    r = _resolver()
    decl = r.resolve("email everyone", AuthorizationLevel.L1)
    corrected = r.correct(decl, field="interpreted_intent",
                          corrected_value="email only opted-in leads")
    assert corrected.confirmation_state == ConfirmationState.CORRECTED
    assert len(corrected.correction_records) == 1
    assert r.is_ready_for_cga(corrected) is True


# --- SIR-5: standing declaration governance --------------------------------

def _standing(authored_by="ops_lead", expires_at=None, intent_class="crm_sync"):
    r = _resolver()
    decl = r.confirm(
        r.resolve("routine CRM sync", AuthorizationLevel.L0, intent_class=intent_class, created_at=NOW)
    )
    return StandingIntentDeclaration(
        standing_id="std_1",
        intent_class=intent_class,
        declaration=decl,
        authored_by=authored_by,
        expires_at=expires_at or (NOW + timedelta(days=30)),
    )


def test_l0_ready_only_with_valid_standing_declaration():
    r = _resolver()
    decl = r.resolve("routine CRM sync", AuthorizationLevel.L0, intent_class="crm_sync", created_at=NOW)
    assert r.is_ready_for_cga(decl, now=NOW) is False  # no standing
    assert r.is_ready_for_cga(decl, standing=_standing(), now=NOW) is True


def test_standing_does_not_cover_unrelated_intent():
    """A standing for one intent class must not bless an unrelated L0 intent."""
    r = _resolver()
    unrelated = r.resolve("wire $1M to vendor", AuthorizationLevel.L0,
                          intent_class="funds_transfer", created_at=NOW)
    assert r.is_ready_for_cga(unrelated, standing=_standing(intent_class="crm_sync"), now=NOW) is False


def test_l0_declaration_without_intent_class_is_not_covered():
    r = _resolver()
    decl = r.resolve("routine CRM sync", AuthorizationLevel.L0, created_at=NOW)  # no intent_class
    assert r.is_ready_for_cga(decl, standing=_standing(), now=NOW) is False


def test_standing_authored_by_system_is_rejected():
    r = _resolver()
    standing = _standing(authored_by="governance_system")
    with pytest.raises(StandingIntentError, match="human authority"):
        r.validate_standing(standing, now=NOW)


def test_expired_standing_is_rejected():
    r = _resolver()
    standing = _standing(expires_at=NOW - timedelta(days=1))
    with pytest.raises(StandingIntentError, match="expired"):
        r.validate_standing(standing, now=NOW)
    # ...and an L0 action backed by it is not ready.
    decl = r.resolve("routine CRM sync", AuthorizationLevel.L0, created_at=NOW)
    assert r.is_ready_for_cga(decl, standing=standing, now=NOW) is False


# --- SIR-4: cryptographic seal + lineage link ------------------------------

def test_seal_and_verify_confirmed_declaration():
    r = _resolver()
    priv, pub = generate_keypair()
    decl = r.confirm(r.resolve("ship the order", AuthorizationLevel.L2))
    sealed = r.seal(decl, priv, "human_alice", decision_id="gov_123")
    assert sealed.linked_decision_id == "gov_123"
    assert r.verify_seal(sealed, pub) is True


def test_sealed_declaration_tamper_is_detected():
    r = _resolver()
    priv, pub = generate_keypair()
    sealed = r.seal(
        r.confirm(r.resolve("ship the order", AuthorizationLevel.L2)),
        priv, "human_alice", decision_id="gov_123",
    )
    sealed.interpreted_intent = "ship a different order"  # tamper after sealing
    assert r.verify_seal(sealed, pub) is False


def test_cannot_seal_unconfirmed_declaration():
    r = _resolver()
    priv, _ = generate_keypair()
    decl = r.resolve("ship the order", AuthorizationLevel.L2)  # PENDING
    with pytest.raises(StandingIntentError, match="not confirmed"):
        r.seal(decl, priv, "human_alice")
