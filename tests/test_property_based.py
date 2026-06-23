"""Property-based fuzzing (SA-5).

Hypothesis-driven sweeps over arbitrary inputs assert the governance core's
load-bearing invariant: it FAILS CLOSED, never FAILS CRASHED. Arbitrary action
parameters, constraint thresholds, and observation streams must never raise an
uncaught exception out of the kernel or the monitors — a class of bug
(crash-on-malformed-input) that example-based tests miss.
"""

from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.self_evolution import SelfEvolutionMonitor
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel

_VERDICTS = {GovernanceVerdict.APPROVED, GovernanceVerdict.REJECTED, GovernanceVerdict.ESCALATE}

# Arbitrary metadata values of every shape a deployment (or a bug) might supply.
_values = st.one_of(
    st.none(), st.booleans(), st.integers(min_value=-10**9, max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=12), st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=4), st.integers(), max_size=2),
)
_param_keys = st.sampled_from([
    "transaction_amount", "record_count", "accesses_phi", "scope", "aml_screened",
    "sanctions_checked", "consequential_decision", "fairness_evaluation", "ai_disclosed",
    "safety_critical", "within_safety_boundary", "generates_content", "ip_risk_assessment",
    "public_distribution", "trademark_usage", "copyright_similarity", "provenance",
    "phi_access_justification",
])
_params = st.dictionaries(_param_keys, _values, max_size=8)
_constraint_names = st.sampled_from([
    "ai_interaction_disclosure", "fairness_evaluation_required", "aml_screening_required",
    "minimum_necessary_phi", "safety_boundary", "ip_content_risk", "cost_ceiling",
])
_action_types = st.sampled_from(["query_crm", "send_email", "send_sms", "wire_transfer", "generate_report"])
_thresholds = st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False, width=32),
                        st.integers(min_value=-5, max_value=10**6))


@given(constraint_name=_constraint_names, params=_params, action_type=_action_types,
       threshold=_thresholds, description=st.text(max_size=40))
@settings(max_examples=400, deadline=None)
def test_regulatory_evaluation_never_crashes(constraint_name, params, action_type,
                                             threshold, description):
    """Governance must return a verdict for ANY action parameters — never raise."""
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
                               parameters=params, risk_score=1)],
        estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
    )
    decision = kernel.evaluate_proposal(
        proposal=proposal, intents=[intent],
        world_state=WorldModel(entities={}, last_reconciled=datetime.utcnow()),
    )
    assert decision.verdict in _VERDICTS


_LEVELS = st.sampled_from(["L0", "L1", "L2", "L3", "L4"])
# A SMALL key alphabet so observations collide on the same per-key bucket — with
# up to 60 observations across a few keys and max_history=5, buckets reliably
# exceed the cap, so the bounded-state assertion below actually exercises (and
# would fail without) the eviction in _cap.
_KEYS = st.sampled_from(["k0", "k1", "k2"])
_obs = st.tuples(_KEYS, _LEVELS, _KEYS)


@given(observations=st.lists(_obs, max_size=60))
@settings(max_examples=150, deadline=None)
def test_integrity_monitor_never_crashes_and_stays_bounded(observations):
    cap = 5
    mon = GovernanceIntegrityMonitor(max_history=cap)
    base = datetime(2026, 1, 1)
    for i, (action_type, level, target) in enumerate(observations):
        mon.observe(action_type, level, target=target, timestamp=base + timedelta(seconds=i))
    signals = mon.scan()                      # must not raise
    assert isinstance(signals, list)
    for bucket in mon._by_target.values():    # state is bounded (load-bearing)
        assert len(bucket) <= cap
    for bucket in mon._by_action.values():
        assert len(bucket) <= cap


def test_monitor_buckets_are_capped_even_under_one_hot_key():
    """Deterministic guard that the per-key memory bound is real (fails against a
    no-op _cap): many observations sharing ONE (action_type, target) key are
    trimmed to max_history."""
    cap = 5
    gim = GovernanceIntegrityMonitor(max_history=cap)
    sem = SelfEvolutionMonitor(max_history=cap)
    base = datetime(2026, 1, 1)
    for i in range(50):
        ts = base + timedelta(seconds=i)
        gim.observe("act", "L1", target="tgt", timestamp=ts)
        sem.observe("skill_modification", "L1", target="tgt", timestamp=ts)
    assert len(gim._by_action["act"]) == cap
    assert len(gim._by_target["tgt"]) == cap
    assert len(sem._mods) == cap


@given(observations=st.lists(st.tuples(_LEVELS, st.text(min_size=1, max_size=6)), max_size=60))
@settings(max_examples=150, deadline=None)
def test_self_evolution_monitor_never_crashes_and_stays_bounded(observations):
    mon = SelfEvolutionMonitor(max_history=20)
    base = datetime(2026, 1, 1)
    for i, (level, target) in enumerate(observations):
        mon.observe("skill_modification", level, target=target, timestamp=base + timedelta(seconds=i))
    assert isinstance(mon.scan(), list)        # must not raise
    assert isinstance(mon.is_flagged(), bool)
    assert len(mon._mods) <= 20                # bounded
