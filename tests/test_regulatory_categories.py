"""Regulatory Constraint Categories — concrete evaluators (G-1 / SA-5).

The spec defines eight Regulatory Constraint Categories. Three had concrete
evaluators (data privacy, communications, financial cost-cap); this suite covers
the rest — transparency (Cat 3), anti-discrimination (Cat 4), financial AML
(Cat 5), healthcare minimum-necessary (Cat 6), and safety (Cat 7) — each with a
violation case (the structural requirement absent) and a compliant case.
"""

from datetime import datetime

import pytest

from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel


def _evaluate(constraint_name, *, action_type="query_crm", params=None, description="",
              threshold=None):
    kernel = GovernanceKernel()
    intent = IntentVector(
        id="i1", objective="o", priority=50,
        hard_constraints=[Constraint(name=constraint_name, type=ConstraintType.HARD,
                                     description=description, threshold=threshold)],
        soft_constraints=[], created_by="t", created_at=datetime.utcnow(),
    )
    proposal = StrategyProposal(
        id="p1", intent_id="i1", attempt_number=1, plan_description="x",
        actions=[PlannedAction(action_type=action_type, target="t1",
                               parameters=params or {}, risk_score=1)],
        estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
    )
    return kernel.evaluate_proposal(proposal=proposal, intents=[intent],
                                    world_state=WorldModel(entities={}, last_reconciled=datetime.utcnow()))


def _rejected(decision, name):
    return decision.verdict == GovernanceVerdict.REJECTED and name in decision.violated_constraints


def _not_rejected(decision):
    return decision.verdict != GovernanceVerdict.REJECTED


# --- Category 3: Transparency (AI interaction disclosure) --------------------

def test_transparency_violation_when_ai_not_disclosed():
    d = _evaluate("ai_interaction_disclosure", action_type="send_email", params={})
    assert _rejected(d, "ai_interaction_disclosure")


def test_transparency_compliant_when_ai_disclosed():
    d = _evaluate("ai_interaction_disclosure", action_type="send_email",
                  params={"ai_disclosed": True})
    assert _not_rejected(d)


def test_transparency_not_triggered_for_non_interactive_action():
    d = _evaluate("ai_interaction_disclosure", action_type="query_crm", params={})
    assert _not_rejected(d)


# --- Category 4: Anti-Discrimination (fairness evaluation) -------------------

def test_fairness_violation_when_consequential_decision_unevaluated():
    d = _evaluate("fairness_evaluation_required",
                  params={"consequential_decision": True})
    assert _rejected(d, "fairness_evaluation_required")


def test_fairness_compliant_with_evaluation_present():
    d = _evaluate("fairness_evaluation_required",
                  params={"consequential_decision": True,
                          "fairness_evaluation": "passed bias audit"})
    assert _not_rejected(d)


def test_fairness_not_triggered_for_non_consequential_action():
    d = _evaluate("fairness_evaluation_required", params={})
    assert _not_rejected(d)


# --- Category 5: Financial (AML screening + sanctions) ----------------------

def test_aml_violation_above_threshold_without_screening():
    d = _evaluate("aml_screening_required", threshold=10000,
                  params={"transaction_amount": 50000})
    assert _rejected(d, "aml_screening_required")


def test_aml_compliant_with_screening_and_sanctions_check():
    d = _evaluate("aml_screening_required", threshold=10000,
                  params={"transaction_amount": 50000, "aml_screened": True,
                          "sanctions_checked": True})
    assert _not_rejected(d)


def test_aml_below_threshold_is_allowed():
    d = _evaluate("aml_screening_required", threshold=10000,
                  params={"transaction_amount": 5000})
    assert _not_rejected(d)


def test_aml_partial_screening_still_violates():
    d = _evaluate("aml_screening_required", threshold=10000,
                  params={"transaction_amount": 50000, "aml_screened": True})  # no sanctions check
    assert _rejected(d, "aml_screening_required")


def test_aml_malformed_amount_fails_closed():
    """A present-but-non-numeric transaction_amount must fail closed (REJECTED),
    never crash the evaluation."""
    for bad in ("N/A", "50,000", {"usd": 50000}, [1, 2]):
        d = _evaluate("aml_screening_required", threshold=10000,
                      params={"transaction_amount": bad})
        assert _rejected(d, "aml_screening_required")


def test_aml_threshold_uses_structured_field_not_description_number():
    """A statutory citation in the description must NOT be mistaken for the
    threshold — the structured `threshold` field governs."""
    d = _evaluate("aml_screening_required", threshold=10000,
                  description="Bank Secrecy Act 31 USC 5311 AML floor",
                  params={"transaction_amount": 50000})  # > 10000, unscreened
    assert _rejected(d, "aml_screening_required")
    ok = _evaluate("aml_screening_required", threshold=10000,
                   description="Bank Secrecy Act 31 USC 5311 AML floor",
                   params={"transaction_amount": 5000})  # < 10000
    assert _not_rejected(ok)


def test_aml_skips_action_without_declared_amount():
    """Documented structural-gate limitation: the AML gate recognizes a financial
    transaction only via the declared `transaction_amount`; an action without it
    is not screened by this constraint (the Action Type Registry is the primary
    governed gate on financial action types)."""
    d = _evaluate("aml_screening_required", threshold=10000,
                  action_type="wire_transfer", params={"amount_usd": 999999})
    assert _not_rejected(d)


# --- Category 6: Healthcare (minimum-necessary PHI) -------------------------

def test_phi_violation_on_bulk_access_without_justification():
    d = _evaluate("minimum_necessary_phi",
                  params={"accesses_phi": True, "scope": "bulk"})
    assert _rejected(d, "minimum_necessary_phi")


def test_phi_compliant_bulk_with_justification():
    d = _evaluate("minimum_necessary_phi",
                  params={"accesses_phi": True, "scope": "bulk",
                          "phi_access_justification": "IRB-123 cohort study"})
    assert _not_rejected(d)


def test_phi_single_record_is_allowed():
    d = _evaluate("minimum_necessary_phi",
                  params={"accesses_phi": True, "record_count": 1})
    assert _not_rejected(d)


def test_phi_record_count_over_threshold_violates():
    d = _evaluate("minimum_necessary_phi", threshold=1,
                  params={"accesses_phi": True, "record_count": 500})
    assert _rejected(d, "minimum_necessary_phi")


def test_phi_malformed_record_count_fails_closed():
    """A present-but-non-numeric record_count on a PHI access fails closed."""
    d = _evaluate("minimum_necessary_phi", threshold=1,
                  params={"accesses_phi": True, "record_count": "many"})
    assert _rejected(d, "minimum_necessary_phi")


def test_phi_threshold_uses_structured_field_not_citation():
    """A 'HIPAA 45 CFR 164.514' citation must NOT be parsed as threshold 45 — the
    structured threshold (1) governs, so a 40-record unjustified access is rejected."""
    d = _evaluate("minimum_necessary_phi", threshold=1,
                  description="HIPAA 45 CFR 164.514 minimum necessary",
                  params={"accesses_phi": True, "record_count": 40})
    assert _rejected(d, "minimum_necessary_phi")


# --- Category 7: Safety (hard safety boundary) ------------------------------

def test_safety_violation_when_not_within_boundary():
    d = _evaluate("safety_boundary", params={"safety_critical": True})
    assert _rejected(d, "safety_boundary")


def test_safety_compliant_within_boundary():
    d = _evaluate("safety_boundary",
                  params={"safety_critical": True, "within_safety_boundary": True})
    assert _not_rejected(d)


def test_safety_not_triggered_for_non_safety_critical_action():
    d = _evaluate("safety_boundary", params={})
    assert _not_rejected(d)


# --- Category 8: IP / Content (IP-risk assessment + provenance) -------------

def test_ip_violation_when_content_generated_without_risk_assessment():
    d = _evaluate("ip_content_risk", params={"generates_content": True})
    assert _rejected(d, "ip_content_risk")


def test_ip_compliant_low_risk_with_assessment():
    d = _evaluate("ip_content_risk",
                  params={"generates_content": True, "ip_risk_assessment": "low risk"})
    assert _not_rejected(d)


def test_ip_high_risk_without_provenance_violates():
    d = _evaluate("ip_content_risk",
                  params={"generates_content": True, "ip_risk_assessment": "reviewed",
                          "public_distribution": True})  # high-risk, no provenance
    assert _rejected(d, "ip_content_risk")


def test_ip_high_risk_with_provenance_is_compliant():
    d = _evaluate("ip_content_risk",
                  params={"generates_content": True, "ip_risk_assessment": "reviewed",
                          "trademark_usage": True, "provenance": "C2PA manifest #abc"})
    assert _not_rejected(d)


def test_ip_not_triggered_for_non_content_action():
    d = _evaluate("ip_content_risk", params={})
    assert _not_rejected(d)
