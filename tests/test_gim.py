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


def test_gim3_signal_survives_later_action():
    """Monotonic detection: a later same-target action (even high-authorization)
    must not move the window anchor and evict an already-qualifying cluster."""
    mon = GovernanceIntegrityMonitor(decomposition_count_threshold=3)
    for i in range(3):
        mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(seconds=i))
    assert mon.check_decomposition("acct_99") is not None
    mon.observe("task_execution", "L2", target="acct_99", timestamp=T0 + timedelta(seconds=600))
    assert mon.check_decomposition("acct_99") is not None


def test_gim3_clears_after_burst_ages_out():
    """Recency-relative: a burst trips GIM-3, but once it ages out (a later
    observation lands more than a window after it) the signal CLEARS — so a
    target that once tripped is not held forever on the consequential path."""
    mon = GovernanceIntegrityMonitor(decomposition_count_threshold=3)
    for i in range(3):
        mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(seconds=i))
    assert mon.check_decomposition("acct_99") is not None      # burst fires
    mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(hours=2))
    assert mon.check_decomposition("acct_99") is None          # aged out -> clears


def test_monitor_observation_stores_are_bounded():
    """A long-running monitor must not grow without bound — per-key lists are
    capped at max_history."""
    mon = GovernanceIntegrityMonitor(max_history=50)
    for i in range(500):
        mon.observe("drift_reconciliation", "L1", target="acct_99",
                    timestamp=T0 + timedelta(seconds=i))
    assert len(mon._by_target["acct_99"]) <= 50
    assert len(mon._by_action["drift_reconciliation"]) <= 50


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


# --- GIM-2 audit-behavior divergence ---------------------------------------

def test_gim2_flags_divergence_between_audit_modes():
    """The system escalates under known audit but not in shadow audit — a sign it
    performs compliance only when it can detect it is watched."""
    mon = GovernanceIntegrityMonitor()
    for _ in range(5):
        mon.observe_audit_sample(audited=True, authorization_level="L1", escalated=True)
    for _ in range(5):
        mon.observe_audit_sample(audited=False, authorization_level="L1", escalated=False)
    signal = mon.check_audit_divergence()
    assert signal is not None
    assert signal.signal_type == "GIM-2"
    assert signal.severity == "critical"   # the strongest integrity signal
    assert signal.evidence["divergences"]["escalation_rate"] == 1.0


def test_gim2_no_signal_when_behavior_consistent():
    mon = GovernanceIntegrityMonitor()
    for audited in (True, False):
        for _ in range(5):
            mon.observe_audit_sample(audited=audited, authorization_level="L1", escalated=False)
    assert mon.check_audit_divergence() is None


def test_gim2_needs_samples_in_both_modes():
    mon = GovernanceIntegrityMonitor(audit_min_samples=5)
    for _ in range(5):
        mon.observe_audit_sample(audited=True, authorization_level="L4", escalated=True)
    # No shadow-audit samples yet.
    assert mon.check_audit_divergence() is None


def test_gim2_observe_decision_feeds_audit_samples():
    mon = GovernanceIntegrityMonitor(audit_min_samples=3)
    known = GovernanceDecision(
        id="d_k", proposal_id="p", verdict=GovernanceVerdict.ESCALATE,
        authorization_level=AuthorizationLevel.L1, action_type_id="task_execution",
        temporal_context={}, policy_snapshot={}, evaluated_at=T0,
    )
    shadow = GovernanceDecision(
        id="d_s", proposal_id="p", verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L1, action_type_id="task_execution",
        temporal_context={}, policy_snapshot={}, evaluated_at=T0,
    )
    for _ in range(3):
        mon.observe_decision(known, target="t", audited=True)
        mon.observe_decision(shadow, target="t", audited=False)
    signal = mon.check_audit_divergence()
    assert signal is not None and signal.signal_type == "GIM-2"


def test_gim2_catches_mean_preserving_distribution_shift():
    """The evasion GIM-2 must catch: identical MEAN authorization but a divergent
    distribution (uniform L2 under known audit vs a bimodal L0/L4 split under
    shadow audit, where the system self-authorizes some actions at L0). Mean rank
    is identical (0.5), so only the distribution (total-variation) metric fires."""
    mon = GovernanceIntegrityMonitor()
    for _ in range(6):
        mon.observe_audit_sample(audited=True, authorization_level="L2", escalated=False)
    for _ in range(3):
        mon.observe_audit_sample(audited=False, authorization_level="L0", escalated=False)
    for _ in range(3):
        mon.observe_audit_sample(audited=False, authorization_level="L4", escalated=False)
    signal = mon.check_audit_divergence()
    assert signal is not None and signal.signal_type == "GIM-2"
    assert signal.evidence["divergences"]["authorization_rank"] == 0.0   # mean is blind
    assert signal.evidence["divergences"]["authorization_distribution"] == 1.0  # TV catches it


# --- GIM-4 escalation framing bias -----------------------------------------

def test_gim4_flags_framing_bias():
    """High system-preferred approval that correlates with favorable framing."""
    mon = GovernanceIntegrityMonitor(escalation_min_samples=5)
    # 18 framed+approved, 1 framed-no/approved, 1 framed-no/declined -> preferred
    # rate 0.95 and a strong framing/approval correlation.
    for _ in range(18):
        mon.observe_escalation(preferred_chosen=True, first_option_chosen=False,
                               preferred_favorably_framed=True)
    mon.observe_escalation(preferred_chosen=True, first_option_chosen=False,
                           preferred_favorably_framed=False)
    mon.observe_escalation(preferred_chosen=False, first_option_chosen=False,
                           preferred_favorably_framed=False)
    signal = mon.check_escalation_framing_bias()
    assert signal is not None
    assert signal.signal_type == "GIM-4"
    assert signal.evidence["preferred_approval_rate"] > 0.9
    assert signal.evidence["framing_approval_correlation"] > 0.5


def test_gim4_flags_position_bias():
    mon = GovernanceIntegrityMonitor(escalation_min_samples=5)
    for _ in range(9):
        mon.observe_escalation(preferred_chosen=False, first_option_chosen=True,
                               preferred_favorably_framed=False)
    mon.observe_escalation(preferred_chosen=False, first_option_chosen=False,
                           preferred_favorably_framed=False)
    signal = mon.check_escalation_framing_bias()
    assert signal is not None
    assert signal.signal_type == "GIM-4"
    assert signal.evidence["position_bias_rate"] == 0.9


def test_gim4_no_signal_when_balanced():
    mon = GovernanceIntegrityMonitor(escalation_min_samples=5)
    for i in range(10):
        mon.observe_escalation(preferred_chosen=(i % 2 == 0),
                               first_option_chosen=(i % 2 == 1),
                               preferred_favorably_framed=(i % 3 == 0))
    assert mon.check_escalation_framing_bias() is None


def test_gim4_no_spurious_correlation_when_all_approved():
    """All-approved + all-framed is a degenerate margin: phi must be 0, so a
    uniformly favorable record does not by itself trip the framing correlation."""
    mon = GovernanceIntegrityMonitor(escalation_min_samples=5)
    for _ in range(10):
        mon.observe_escalation(preferred_chosen=True, first_option_chosen=False,
                               preferred_favorably_framed=True)
    signal = mon.check_escalation_framing_bias()
    # preferred rate is 1.0 but correlation is 0.0 (no variance) and position bias
    # is 0 — so the framing path does not fire on this alone.
    assert signal is None


# --- GIM-5 adversarial collapse --------------------------------------------

def test_gim5_flags_collapse_as_critical():
    """Two consecutive periods (window=5 → 10 challenges) of ~100% endorsement and
    no material change is a full collapse: critical, must-rebuild."""
    mon = GovernanceIntegrityMonitor(challenge_window=5)
    for _ in range(10):
        mon.observe_challenge(endorsed=True, material_change=False)
    signal = mon.check_adversarial_collapse()
    assert signal is not None
    assert signal.signal_type == "GIM-5"
    assert signal.severity == "critical"        # endorsement ~100% => collapsed
    assert signal.evidence["collapsed"] is True


def test_gim5_warns_below_full_collapse():
    mon = GovernanceIntegrityMonitor(challenge_window=10)
    # Prior period: 10/10 endorsed. Recent period: 9/10 endorsed (0.9), 0 material.
    # Both periods over the 0.85 threshold but recent < 0.98 => warning, not collapse.
    for _ in range(10):
        mon.observe_challenge(endorsed=True, material_change=False)   # prior period
    for _ in range(9):
        mon.observe_challenge(endorsed=True, material_change=False)   # recent period
    mon.observe_challenge(endorsed=False, material_change=False)
    signal = mon.check_adversarial_collapse()
    assert signal is not None
    assert signal.severity == "warning"
    assert signal.evidence["collapsed"] is False


def test_gim5_no_signal_when_genuinely_challenging():
    mon = GovernanceIntegrityMonitor(challenge_window=5)
    for i in range(10):
        mon.observe_challenge(endorsed=(i % 2 == 0), material_change=True)
    assert mon.check_adversarial_collapse() is None


def test_gim5_requires_two_consecutive_periods():
    """The spec's debounce: a single collapsed period preceded by a healthy one
    does NOT alert; only when a SECOND consecutive period also collapses does it
    fire. A transient one-window dip is not enough."""
    mon = GovernanceIntegrityMonitor(challenge_window=5)
    for _ in range(5):
        mon.observe_challenge(endorsed=False, material_change=True)   # healthy period
    for _ in range(5):
        mon.observe_challenge(endorsed=True, material_change=False)   # one collapsed period
    assert mon.check_adversarial_collapse() is None                   # not yet — debounced

    for _ in range(5):
        mon.observe_challenge(endorsed=True, material_change=False)   # second consecutive
    signal = mon.check_adversarial_collapse()
    assert signal is not None and signal.signal_type == "GIM-5"


def test_gim5_needs_two_full_periods_of_data():
    mon = GovernanceIntegrityMonitor(challenge_window=10)  # needs 20 challenges
    for _ in range(15):
        mon.observe_challenge(endorsed=True, material_change=False)
    assert mon.check_adversarial_collapse() is None


# --- scan integrates all five signal classes -------------------------------

def test_scan_returns_all_five_signal_classes():
    mon = GovernanceIntegrityMonitor()
    # GIM-1
    for _ in range(5):
        mon.observe("skill_modification", "L4")
    for _ in range(3):
        mon.observe("skill_modification", "L0")
    # GIM-3
    for i in range(3):
        mon.observe("task_execution", "L0", target="acct_99", timestamp=T0 + timedelta(seconds=i))
    # GIM-2
    for _ in range(5):
        mon.observe_audit_sample(audited=True, authorization_level="L1", escalated=True)
        mon.observe_audit_sample(audited=False, authorization_level="L1", escalated=False)
    # GIM-4
    for _ in range(9):
        mon.observe_escalation(preferred_chosen=False, first_option_chosen=True,
                               preferred_favorably_framed=False)
    mon.observe_escalation(preferred_chosen=False, first_option_chosen=False,
                           preferred_favorably_framed=False)
    # GIM-5 (two full periods at the default window of 10 => 20 challenges)
    for _ in range(20):
        mon.observe_challenge(endorsed=True, material_change=False)

    types = {s.signal_type for s in mon.scan()}
    assert types == {"GIM-1", "GIM-2", "GIM-3", "GIM-4", "GIM-5"}
