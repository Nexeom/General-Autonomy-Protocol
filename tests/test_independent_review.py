"""Independent authorization review — the GIM independence axis (G-3).

An independent classifier re-rates each operation's required authorization level
and divergence from the system's classification is flagged — catching the system
under-classifying an operation (assigning too little oversight) the way the base
GIM detectors (which watch only the system's own stream) cannot.
"""

from datetime import datetime

from gap_kernel.governance.independent_review import (
    IndependentAuthorizationMonitor,
    RuleBasedIndependentClassifier,
)
from gap_kernel.models.governance import AuthorizationLevel as AL
from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.strategy import PlannedAction, StrategyProposal


# --- the reference independent classifier -----------------------------------

def test_classifier_rates_high_risk_operations_high():
    clf = RuleBasedIndependentClassifier()
    assert clf.classify("delete_records", {}, risk_score=10) == AL.L4
    assert clf.classify("query_status", {}, risk_score=1) == AL.L0


def test_classifier_is_independent_of_action_type_registry():
    """It rates from risk signals, not a registry — so a benign-named action with
    a high risk score and a dangerous parameter still rates high."""
    clf = RuleBasedIndependentClassifier()
    level = clf.classify("routine_task", {"op": "wire transfer $1M"}, risk_score=9)
    assert level in (AL.L3, AL.L4)


def test_classifier_fails_toward_oversight_on_malformed_risk():
    clf = RuleBasedIndependentClassifier()
    assert clf.classify("x", {}, risk_score="not-a-number") == AL.L4  # max oversight


def test_classifier_does_not_crash_on_malformed_parameters():
    """A non-dict parameters or a value whose __str__ raises must not crash a
    fail-safe oversight check — it classifies from the risk score instead."""
    clf = RuleBasedIndependentClassifier()

    class _Bad:
        def __str__(self):
            raise RuntimeError("boom")

    assert clf.classify("x", {"k": _Bad()}, risk_score=1) == AL.L0   # no crash
    assert clf.classify("x", ["not", "a", "dict"], risk_score=1) == AL.L0  # no crash


# --- divergence monitor -----------------------------------------------------

def test_severe_under_classification_is_flagged_critical():
    """The spec's hard alert: system L0, independent L3/L4."""
    mon = IndependentAuthorizationMonitor()
    mon.observe("delete_all_customers", {}, risk_score=10, system_level=AL.L0)
    signal = mon.check_divergence()
    assert signal is not None
    assert signal.signal_type == "GIM-INDEP" and signal.severity == "critical"
    assert signal.evidence["gap"] >= 3


def test_agreement_produces_no_signal():
    mon = IndependentAuthorizationMonitor()
    for _ in range(10):
        # system L0 on genuinely routine ops; independent also rates L0.
        mon.observe("query_status", {}, risk_score=1, system_level=AL.L0)
    assert mon.check_divergence() is None


def test_sustained_under_classification_drift_is_flagged():
    mon = IndependentAuthorizationMonitor(min_samples=5)
    # Mild per-op gap (independent one level above system, no severe single op):
    # risk_score 3 -> independent L1, system says L0 -> gap 1 sustained.
    for _ in range(8):
        mon.observe("compute_metric", {}, risk_score=3, system_level=AL.L0)
    signal = mon.check_divergence()
    assert signal is not None and signal.signal_type == "GIM-INDEP"
    assert signal.severity == "warning"
    assert signal.evidence["drift"] > 0.15


def test_over_classification_is_not_flagged():
    """The system assigning a HIGHER level than the independent evaluator is not a
    governance-erosion concern (extra oversight), so no drift alert."""
    mon = IndependentAuthorizationMonitor(min_samples=5)
    for _ in range(8):
        mon.observe("query_status", {}, risk_score=1, system_level=AL.L4)  # over-cautious
    assert mon.check_divergence() is None


def test_observe_decision_convenience():
    mon = IndependentAuthorizationMonitor()
    proposal = StrategyProposal(
        id="p1", intent_id="i1", attempt_number=1, plan_description="x",
        actions=[PlannedAction(action_type="wipe_database", target="db",
                               parameters={}, risk_score=10)],
        estimated_cost=0.0, rationale="r", generated_at=datetime.utcnow(),
    )
    decision = GovernanceDecision(
        id="d1", proposal_id="p1", verdict=GovernanceVerdict.APPROVED,
        authorization_tier="auto", authorization_level=AL.L0, evaluated_at=datetime.utcnow(),
    )
    mon.observe_decision(decision, proposal)
    signal = mon.check_divergence()
    assert signal is not None and signal.severity == "critical"


def test_state_is_bounded():
    mon = IndependentAuthorizationMonitor(max_history=20)
    for _ in range(200):
        mon.observe("query", {}, risk_score=1, system_level=AL.L0)
    assert len(mon._divergences) <= 20


def test_custom_classifier_is_pluggable():
    class _AlwaysL4:
        def classify(self, action_type, parameters, risk_score):
            return AL.L4
    mon = IndependentAuthorizationMonitor(_AlwaysL4())
    mon.observe("anything", {}, risk_score=1, system_level=AL.L0)
    assert mon.check_divergence().severity == "critical"
