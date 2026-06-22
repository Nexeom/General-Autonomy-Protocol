"""Phase F — Governance Integrity Monitoring (Fix 6).

GIM-1 (authorization drift) and GIM-3 (threshold-avoidance decomposition) as
independent, rule-based detectors over the stream of governance decisions.
"""

from datetime import datetime, timedelta

from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
)

T0 = datetime(2026, 6, 22, 12, 0, 0)


# --- GIM-1 authorization drift ---------------------------------------------

def test_gim1_flags_downward_drift():
    mon = GovernanceIntegrityMonitor()
    # Baseline: authorized high; recent: authorized low (erosion).
    for _ in range(5):
        mon.observe("skill_modification", "L4")
    for _ in range(3):
        mon.observe("skill_modification", "L0")
    signal = mon.check_authorization_drift("skill_modification")
    assert signal is not None
    assert signal.signal_type == "GIM-1"
    assert signal.evidence["drift"] >= 1.0


def test_gim1_no_signal_when_stable():
    mon = GovernanceIntegrityMonitor()
    for _ in range(8):
        mon.observe("task_execution", "L2")
    assert mon.check_authorization_drift("task_execution") is None


def test_gim1_needs_enough_history():
    mon = GovernanceIntegrityMonitor()
    for _ in range(3):
        mon.observe("task_execution", "L0")
    assert mon.check_authorization_drift("task_execution") is None


# --- GIM-3 threshold-avoidance decomposition -------------------------------

def test_gim3_flags_decomposition():
    mon = GovernanceIntegrityMonitor(decomposition_count_threshold=3)
    for i in range(3):
        mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(seconds=i))
    signal = mon.check_decomposition("acct_99")
    assert signal is not None
    assert signal.signal_type == "GIM-3"
    assert signal.evidence["count"] == 3


def test_gim3_no_signal_below_threshold():
    mon = GovernanceIntegrityMonitor(decomposition_count_threshold=3)
    for i in range(2):
        mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(seconds=i))
    assert mon.check_decomposition("acct_99") is None


def test_gim3_respects_time_window():
    mon = GovernanceIntegrityMonitor(
        decomposition_count_threshold=3, decomposition_window_seconds=300
    )
    # Three actions but spread far enough apart that only one is in the window.
    mon.observe("task_execution", "L0", target="acct_99", timestamp=T0)
    mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(hours=1))
    mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(hours=2))
    assert mon.check_decomposition("acct_99") is None


def test_gim3_ignores_high_authorization_actions():
    """High-authorization actions are not decomposition — those go through a gate."""
    mon = GovernanceIntegrityMonitor(decomposition_count_threshold=3, decomposition_max_level="L1")
    for i in range(3):
        mon.observe("policy_proposal", "L4", target="acct_99", timestamp=T0 + timedelta(seconds=i))
    assert mon.check_decomposition("acct_99") is None


# --- integration ------------------------------------------------------------

def test_scan_returns_all_signals():
    mon = GovernanceIntegrityMonitor()
    for _ in range(5):
        mon.observe("skill_modification", "L4")
    for _ in range(3):
        mon.observe("skill_modification", "L0")
    for i in range(3):
        mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(seconds=i))
    types = {s.signal_type for s in mon.scan()}
    assert types == {"GIM-1", "GIM-3"}


def test_observe_decision_ingests_governance_decision():
    mon = GovernanceIntegrityMonitor()
    decision = GovernanceDecision(
        id="gov_1", proposal_id="p1", verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L0, action_type_id="task_execution",
        temporal_context={}, policy_snapshot={}, evaluated_at=T0,
    )
    obs = mon.observe_decision(decision, target="acct_1")
    assert obs is not None
    assert obs.action_type == "task_execution"
    assert obs.rank == 0
