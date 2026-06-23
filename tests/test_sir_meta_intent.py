"""SIR-2 meta-intent inference (rule-based).

The inference derives the values motivating a request from the stated intent —
risk tolerance, value hierarchy, and stakeholders actually reflect the text,
rather than returning a fixed placeholder.
"""

from gap_kernel.governance.sir import StructuredIntentResolver, infer_meta_intent
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.sir import MetaIntent


def test_irreversible_intent_lowers_risk_tolerance():
    mi = infer_meta_intent("Permanently delete all customer records")
    assert mi.risk_tolerance == "low"
    assert "customers" in mi.stakeholder_impact
    assert "parties_affected_by_irreversible_action" in mi.stakeholder_impact


def test_routine_read_raises_risk_tolerance():
    mi = infer_meta_intent("Query the CRM and summarize lead status")
    assert mi.risk_tolerance == "high"


def test_neutral_intent_is_moderate():
    mi = infer_meta_intent("Reschedule the team sync to Thursday")
    assert mi.risk_tolerance == "moderate"


def test_financial_intent_weights_value_hierarchy_and_is_low_risk():
    mi = infer_meta_intent("Wire $5000 to the vendor account")
    assert mi.risk_tolerance == "low"                    # "wire" is irreversible
    assert "financial_integrity" in mi.value_hierarchy
    assert mi.value_hierarchy[0] == "safety"             # safety always first
    assert "regulatory_compliance" in mi.value_hierarchy


def test_healthcare_intent_adds_patient_stakeholders():
    mi = infer_meta_intent("Update the patient's clinical diagnosis notes")
    assert mi.risk_tolerance == "low"                    # sensitive domain
    assert "patients" in mi.stakeholder_impact
    assert "patient_safety" in mi.value_hierarchy


def test_inference_reflects_the_stated_intent():
    """Different intents must yield different meta-intents (not a fixed placeholder)."""
    a = infer_meta_intent("Delete the production database")
    b = infer_meta_intent("List today's calendar events")
    assert a.risk_tolerance != b.risk_tolerance
    assert a.stakeholder_impact != b.stakeholder_impact


def test_value_hierarchy_has_no_duplicates():
    # "wire" (financial + irreversible) + "transfer" (financial) must not double-add.
    mi = infer_meta_intent("Wire and transfer funds to settle the invoice")
    assert len(mi.value_hierarchy) == len(set(mi.value_hierarchy))
    assert mi.stakeholder_impact[0] == "requesting_user"


def test_resolver_uses_the_inference_by_default():
    resolver = StructuredIntentResolver()
    decl = resolver.resolve("Wire $1M to an offshore account", AuthorizationLevel.L2)
    assert isinstance(decl.meta_intent, MetaIntent)
    assert decl.meta_intent.risk_tolerance == "low"
    assert "financial_integrity" in decl.meta_intent.value_hierarchy


def test_routine_keywords_are_word_anchored_not_substrings():
    """A routine keyword as a mid-word SUBSTRING must not flip a non-routine intent
    to permissive 'high' (e.g. 'view' inside preview/overview/review, 'read' inside
    already, 'list' inside enlist) — otherwise the human reviewer is mis-cued."""
    for intent in (
        "Preview the production deployment",
        "Grant overview admin rights to the intern",
        "Review the merger agreement and sign off",
        "Enlist the new payroll vendor",
        "Already approved: push the release to all users",
    ):
        assert infer_meta_intent(intent).risk_tolerance != "high", intent


def test_genuine_routine_reads_still_detected():
    for intent in ("Read the config file", "Query the database for orders",
                   "List the open tickets", "Summarize the weekly metrics",
                   "Look up the customer record"):
        assert infer_meta_intent(intent).risk_tolerance == "high", intent


def test_custom_inferencer_is_still_pluggable():
    custom = lambda s: MetaIntent(primary_objective="custom", risk_tolerance="high")
    resolver = StructuredIntentResolver(meta_intent_inferencer=custom)
    decl = resolver.resolve("anything", AuthorizationLevel.L1)
    assert decl.meta_intent.primary_objective == "custom"
