"""Execution replay protection — a signed decision is a SINGLE-USE authorization.

Every other guard in the Execution Fabric (kill-switch, proposal binding, digest
binding, signature verification, verdict check) is stateless, so each one passes
identically on every replay of the same decision. These tests pin the stateful
half: the kernel stamps a ``nonce`` and an ``expires_at`` into the SIGNED payload,
and the ExecutionLedger is the replay authority at EVERY authorization level —
not just the L2+ gates the OOB ledger covers.
"""

from datetime import timedelta

import pytest

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair, sign
from gap_kernel.execution.fabric import (
    ExecutionError,
    ExecutionFabric,
    OOBVerificationError,
    ReplayExecutionError,
)
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
    canonical_decision_payload,
)
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.verification.execution_ledger import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    ExecutionLedger,
    ExecutionReplayError,
)

APPROVER = "human_approver_alice"


def _world():
    return WorldModel(
        entities={
            "lead_123": EntityState(
                entity_type="lead", entity_id="lead_123", properties={},
                last_updated=utcnow(), source="test",
            )
        },
        last_reconciled=utcnow(),
    )


def _intent():
    return IntentVector(
        id="i1", objective="o", priority=50, hard_constraints=[], soft_constraints=[],
        created_by="t", created_at=utcnow(),
    )


def _proposal(pid="prop_replay", risk=3, actions=None):
    return StrategyProposal(
        id=pid,
        intent_id="i1",
        attempt_number=1,
        plan_description="p",
        actions=actions or [
            PlannedAction(action_type="query_crm", target="lead_123", parameters={}, risk_score=risk)
        ],
        estimated_cost=0.01,
        rationale="r",
        generated_at=utcnow(),
    )


def _kernel_signed(kernel, proposal):
    decision = kernel.evaluate_proposal(
        proposal=proposal, intents=[_intent()], world_state=_world()
    )
    assert decision.verdict == GovernanceVerdict.APPROVED
    return decision


def _attach_oob(decision, approver_priv, *, key_id=APPROVER, valid_until=None):
    decision.human_approver_public_key_id = key_id
    decision.human_approval_timestamp = utcnow()
    decision.human_approval_valid_until = valid_until or (utcnow() + timedelta(minutes=5))
    decision.human_approval_signature = sign(
        approver_priv, ExecutionFabric._oob_signed_message(decision)
    )
    return decision


# --- The headline defect: a signed risk-3 decision replayed 4/4 --------------

def test_signed_low_risk_decision_executes_exactly_once():
    """The verified defect: one signed risk-3 (L0, routine autonomous) decision
    executed 4/4 times. The second attempt must now be refused."""
    kernel = GovernanceKernel()
    proposal = _proposal(risk=3)
    decision = _kernel_signed(kernel, proposal)
    assert decision.authorization_level == AuthorizationLevel.L0

    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    assert fabric.execute(proposal, decision).success is True

    for _ in range(3):
        with pytest.raises(ReplayExecutionError, match="already been executed"):
            fabric.execute(proposal, decision)


def test_replay_refused_across_a_fresh_fabric_sharing_the_ledger():
    """Replay protection is a property of the shared ledger, not of one process."""
    kernel = GovernanceKernel()
    ledger = ExecutionLedger()
    proposal = _proposal()
    decision = _kernel_signed(kernel, proposal)

    a = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex,
                        execution_ledger=ledger)
    b = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex,
                        execution_ledger=ledger)
    assert a.execute(proposal, decision).success is True
    with pytest.raises(ReplayExecutionError, match="already been executed"):
        b.execute(proposal, decision)


@pytest.mark.parametrize(
    "level",
    [AuthorizationLevel.L0, AuthorizationLevel.L1, AuthorizationLevel.L2,
     AuthorizationLevel.L3, AuthorizationLevel.L4],
)
def test_replay_refused_at_every_authorization_level(level):
    kernel_priv, kernel_pub = generate_keypair()
    approver_priv, approver_pub = generate_keypair()
    proposal = _proposal()
    decision = GovernanceDecision(
        id="gov_lvl", proposal_id=proposal.id, verdict=GovernanceVerdict.APPROVED,
        authorization_level=level, temporal_context={}, policy_snapshot={},
        evaluated_at=utcnow(), nonce="nonce_lvl",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    decision.decision_signature = sign(kernel_priv, canonical_decision_payload(decision))
    if level in (AuthorizationLevel.L2, AuthorizationLevel.L3, AuthorizationLevel.L4):
        _attach_oob(decision, approver_priv)

    fabric = ExecutionFabric(
        _world(), kernel_public_key_hex=kernel_pub,
        public_key_registry=PublicKeyRegistry({APPROVER: approver_pub}),
    )
    assert fabric.execute(proposal, decision).success is True
    with pytest.raises(ExecutionError, match="already been executed|already been used"):
        fabric.execute(proposal, decision)


# --- The replay fields are inside the SIGNED payload ------------------------

def test_kernel_stamps_nonce_and_expiry():
    kernel = GovernanceKernel()
    decision = _kernel_signed(kernel, _proposal())
    assert decision.nonce
    assert decision.expires_at is not None
    assert decision.expires_at > utcnow()


def test_nonce_is_unique_per_decision():
    kernel = GovernanceKernel()
    nonces = {
        _kernel_signed(kernel, _proposal(pid=f"p{i}")).nonce for i in range(25)
    }
    assert len(nonces) == 25


@pytest.mark.parametrize("field", ["nonce", "expires_at", "kernel_public_key_id"])
def test_replay_fields_are_covered_by_the_kernel_signature(field):
    """Rewriting the nonce, the expiry, or the signing-key id must invalidate the
    signature — otherwise they are unauthenticated metadata an attacker rewrites."""
    kernel = GovernanceKernel()
    proposal = _proposal()
    decision = _kernel_signed(kernel, proposal)
    tampered = {
        "nonce": "attacker_chosen_nonce",
        "expires_at": utcnow() + timedelta(days=3650),
        "kernel_public_key_id": "some_other_kernel",
    }[field]
    setattr(decision, field, tampered)

    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    with pytest.raises(ExecutionError, match="invalid|forgery"):
        fabric.execute(proposal, decision)


def test_canonical_payload_uses_the_v2_domain_tag():
    kernel = GovernanceKernel()
    payload = canonical_decision_payload(_kernel_signed(kernel, _proposal()))
    assert '"gap.governance.decision.v2"' in payload


# --- Expiry ------------------------------------------------------------------

def test_expired_decision_is_refused():
    kernel_priv, kernel_pub = generate_keypair()
    proposal = _proposal()
    decision = GovernanceDecision(
        id="gov_exp", proposal_id=proposal.id, verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L0, temporal_context={},
        policy_snapshot={}, evaluated_at=utcnow(), nonce="nonce_exp",
        expires_at=utcnow() - timedelta(seconds=1),
    )
    decision.decision_signature = sign(kernel_priv, canonical_decision_payload(decision))
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel_pub)
    with pytest.raises(ExecutionError, match="expired"):
        fabric.execute(proposal, decision)


def test_kernel_decision_ttl_is_configurable_and_must_be_positive():
    from gap_kernel.errors import GovernanceConfigError

    kernel = GovernanceKernel(decision_ttl_seconds=60)
    decision = _kernel_signed(kernel, _proposal())
    assert decision.expires_at <= utcnow() + timedelta(seconds=60)
    with pytest.raises(GovernanceConfigError, match="decision_ttl_seconds"):
        GovernanceKernel(decision_ttl_seconds=0)


# --- Fail closed on a missing nonce -----------------------------------------

def test_verifying_fabric_refuses_a_decision_with_no_nonce():
    """A decision the fabric authenticates must carry its single-use fields; with
    none, replay protection is unevaluable, which is a violation, not a pass."""
    kernel_priv, kernel_pub = generate_keypair()
    proposal = _proposal()
    decision = GovernanceDecision(
        id="gov_nononce", proposal_id=proposal.id, verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L0, temporal_context={},
        policy_snapshot={}, evaluated_at=utcnow(),
    )
    decision.decision_signature = sign(kernel_priv, canonical_decision_payload(decision))
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel_pub)
    with pytest.raises(ExecutionError, match="single-use"):
        fabric.execute(proposal, decision)


# --- B3: the verifying key resolves from a signed field ---------------------

def test_kernel_key_resolves_from_the_signed_key_id():
    kernel = GovernanceKernel(kernel_key_id="kernel_a")
    proposal = _proposal()
    decision = _kernel_signed(kernel, proposal)
    fabric = ExecutionFabric(
        _world(),
        kernel_key_registry=PublicKeyRegistry({"kernel_a": kernel.public_key_hex}),
    )
    assert fabric.execute(proposal, decision).success is True


def test_unknown_kernel_key_id_is_refused():
    kernel = GovernanceKernel(kernel_key_id="rogue_kernel")
    proposal = _proposal()
    decision = _kernel_signed(kernel, proposal)
    fabric = ExecutionFabric(
        _world(),
        kernel_key_registry=PublicKeyRegistry({"kernel_a": generate_keypair()[1]}),
    )
    with pytest.raises(ExecutionError, match="not registered"):
        fabric.execute(proposal, decision)


# --- B2: reserve before dispatch, per-action completion ---------------------

def _two_action_proposal(pid="prop_partial"):
    return _proposal(
        pid=pid,
        actions=[
            PlannedAction(action_type="send_email", target="lead_123", parameters={}, risk_score=3),
            PlannedAction(action_type="flaky", target="lead_123", parameters={}, risk_score=3),
        ],
    )


class _CountingExecutor:
    """Succeeds only from the ``succeed_from``-th call onward; counts every call."""

    def __init__(self, succeed_from=1):
        self.calls = 0
        self.succeed_from = succeed_from

    def __call__(self, action):
        self.calls += 1
        if self.calls < self.succeed_from:
            raise RuntimeError("transient failure")
        return {"status": "ok"}


def test_failed_execution_stays_resumable():
    kernel = GovernanceKernel()
    proposal = _two_action_proposal()
    decision = _kernel_signed(kernel, proposal)
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    flaky = _CountingExecutor(succeed_from=2)
    fabric.register_executor("flaky", flaky)

    first = fabric.execute(proposal, decision)
    assert first.success is False
    second = fabric.execute(proposal, decision)  # resume, not replay
    assert second.success is True


def test_resumed_execution_does_not_repeat_completed_side_effects():
    """A partial-failure retry must not re-run the actions that already succeeded
    under the same authorization."""
    kernel = GovernanceKernel()
    proposal = _two_action_proposal()
    decision = _kernel_signed(kernel, proposal)
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=kernel.public_key_hex)
    email = _CountingExecutor()
    fabric.register_executor("send_email", email)
    fabric.register_executor("flaky", _CountingExecutor(succeed_from=2))

    assert fabric.execute(proposal, decision).success is False
    assert email.calls == 1
    assert fabric.execute(proposal, decision).success is True
    assert email.calls == 1  # the completed side effect was NOT repeated
    with pytest.raises(ReplayExecutionError):
        fabric.execute(proposal, decision)


def test_one_human_approval_does_not_re_execute_completed_actions():
    """B2 end to end: an L2 partial-failure retry rides the SAME human approval,
    so the already-delivered action must not fire a second time."""
    kernel_priv, kernel_pub = generate_keypair()
    approver_priv, approver_pub = generate_keypair()
    proposal = _two_action_proposal(pid="prop_l2_partial")
    decision = GovernanceDecision(
        id="gov_l2_partial", proposal_id=proposal.id,
        verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L2, temporal_context={},
        policy_snapshot={}, evaluated_at=utcnow(), nonce="nonce_l2_partial",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    decision.decision_signature = sign(kernel_priv, canonical_decision_payload(decision))
    _attach_oob(decision, approver_priv)

    fabric = ExecutionFabric(
        _world(), kernel_public_key_hex=kernel_pub,
        public_key_registry=PublicKeyRegistry({APPROVER: approver_pub}),
    )
    email = _CountingExecutor()
    fabric.register_executor("send_email", email)
    fabric.register_executor("flaky", _CountingExecutor(succeed_from=2))

    assert fabric.execute(proposal, decision).success is False
    assert fabric.execute(proposal, decision).success is True
    assert email.calls == 1


def test_oob_approval_is_reserved_before_dispatch():
    """TOCTOU: the human approval must be spent BEFORE any action is dispatched,
    not after — otherwise a concurrent execution can spend it a second time."""
    from gap_kernel.verification.oob_ledger import OOBLedger

    kernel_priv, kernel_pub = generate_keypair()
    approver_priv, approver_pub = generate_keypair()
    proposal = _proposal(pid="prop_reserve")
    decision = GovernanceDecision(
        id="gov_reserve", proposal_id=proposal.id, verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L2, temporal_context={},
        policy_snapshot={}, evaluated_at=utcnow(), nonce="nonce_reserve",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    decision.decision_signature = sign(kernel_priv, canonical_decision_payload(decision))
    _attach_oob(decision, approver_priv)

    oob_ledger = OOBLedger()
    fabric = ExecutionFabric(
        _world(), kernel_public_key_hex=kernel_pub, oob_ledger=oob_ledger,
        public_key_registry=PublicKeyRegistry({APPROVER: approver_pub}),
    )
    seen = {}

    def _observe(action):
        seen["reserved"] = oob_ledger.has_been_used(
            decision.id, decision.human_approval_signature
        )
        return {"status": "ok"}

    fabric.register_executor("query_crm", _observe)
    assert fabric.execute(proposal, decision).success is True
    assert seen["reserved"] is True


def test_failed_dispatch_does_not_burn_the_approval_for_its_own_retry():
    kernel_priv, kernel_pub = generate_keypair()
    approver_priv, approver_pub = generate_keypair()
    proposal = _proposal(pid="prop_retry")
    decision = GovernanceDecision(
        id="gov_retry", proposal_id=proposal.id, verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L2, temporal_context={},
        policy_snapshot={}, evaluated_at=utcnow(), nonce="nonce_retry",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    decision.decision_signature = sign(kernel_priv, canonical_decision_payload(decision))
    _attach_oob(decision, approver_priv)

    fabric = ExecutionFabric(
        _world(), kernel_public_key_hex=kernel_pub,
        public_key_registry=PublicKeyRegistry({APPROVER: approver_pub}),
    )
    fabric.register_executor("query_crm", _CountingExecutor(succeed_from=2))
    assert fabric.execute(proposal, decision).success is False
    assert fabric.execute(proposal, decision).success is True


def test_a_failed_reservation_is_not_waved_through_on_resume():
    """A resumed execution must not skip the approval reservation just because a
    row exists — only a reservation that actually SUCCEEDED is carried forward."""
    from gap_kernel.verification.oob_ledger import OOBLedger

    kernel_priv, kernel_pub = generate_keypair()
    approver_priv, approver_pub = generate_keypair()
    proposal = _proposal(pid="prop_resv_fail")
    decision = GovernanceDecision(
        id="gov_resv_fail", proposal_id=proposal.id,
        verdict=GovernanceVerdict.APPROVED,
        authorization_level=AuthorizationLevel.L2, temporal_context={},
        policy_snapshot={}, evaluated_at=utcnow(), nonce="nonce_resv_fail",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    decision.decision_signature = sign(kernel_priv, canonical_decision_payload(decision))
    _attach_oob(decision, approver_priv)

    oob_ledger = OOBLedger()
    oob_ledger.record_use(decision.id, decision.human_approval_signature, APPROVER)

    dispatched = _CountingExecutor()
    fabric = ExecutionFabric(
        _world(), kernel_public_key_hex=kernel_pub, oob_ledger=oob_ledger,
        public_key_registry=PublicKeyRegistry({APPROVER: approver_pub}),
    )
    fabric.register_executor("query_crm", dispatched)

    for _ in range(2):
        with pytest.raises(OOBVerificationError, match="already been used"):
            fabric.execute(proposal, decision)
    assert dispatched.calls == 0


def test_a_second_decision_cannot_spend_the_same_approval():
    """A re-nonced decision carrying the SAME human approval is refused: the
    approval is spent in the OOB ledger, so re-minting the nonce buys nothing."""
    kernel_priv, kernel_pub = generate_keypair()
    approver_priv, approver_pub = generate_keypair()
    proposal = _proposal(pid="prop_share")
    valid_until = utcnow() + timedelta(minutes=5)

    def _build(nonce):
        d = GovernanceDecision(
            id="gov_share", proposal_id=proposal.id, verdict=GovernanceVerdict.APPROVED,
            authorization_level=AuthorizationLevel.L2, temporal_context={},
            policy_snapshot={}, evaluated_at=utcnow(), nonce=nonce,
            expires_at=utcnow() + timedelta(minutes=5),
        )
        _attach_oob(d, approver_priv, valid_until=valid_until)
        d.decision_signature = sign(kernel_priv, canonical_decision_payload(d))
        return d

    fabric = ExecutionFabric(
        _world(), kernel_public_key_hex=kernel_pub,
        public_key_registry=PublicKeyRegistry({APPROVER: approver_pub}),
    )
    assert fabric.execute(proposal, _build("nonce_a")).success is True
    with pytest.raises(OOBVerificationError, match="already been used"):
        fabric.execute(proposal, _build("nonce_b"))


# --- ExecutionLedger state machine ------------------------------------------

class TestExecutionLedger:
    def test_begin_claims_a_fresh_row(self):
        ledger = ExecutionLedger()
        row = ledger.begin("n1", decision_id="d1", proposal_id="p1")
        assert row.status == STATUS_IN_PROGRESS
        assert row.resumed is False
        assert row.attempts == 1
        assert row.completed_actions == frozenset()

    def test_begin_after_failure_resumes(self):
        ledger = ExecutionLedger()
        ledger.begin("n1", decision_id="d1", proposal_id="p1")
        ledger.finish("n1", success=False)
        # A settled failure is FAILED, not IN_PROGRESS: an attempt that stopped
        # and an attempt still running must be distinguishable, or concurrent
        # presentations of one authorization all resume and all dispatch.
        assert ledger.status("n1") == STATUS_FAILED
        row = ledger.begin("n1", decision_id="d1", proposal_id="p1")
        assert row.resumed is True
        assert row.attempts == 2

    def test_begin_after_success_is_a_replay(self):
        ledger = ExecutionLedger()
        ledger.begin("n1", decision_id="d1", proposal_id="p1")
        ledger.finish("n1", success=True)
        assert ledger.status("n1") == STATUS_COMPLETE
        with pytest.raises(ExecutionReplayError, match="already been executed"):
            ledger.begin("n1", decision_id="d1", proposal_id="p1")

    def test_failed_finish_stamps_last_attempt_and_leaves_finished_at_null(self):
        ledger = ExecutionLedger()
        ledger.begin("n1", decision_id="d1", proposal_id="p1")
        ledger.finish("n1", success=False)
        row = ledger._conn.execute(
            "SELECT last_attempt_at, finished_at FROM executions WHERE nonce = ?", ("n1",)
        ).fetchone()
        assert row["last_attempt_at"] is not None
        assert row["finished_at"] is None

    def test_completed_actions_survive_across_attempts(self):
        ledger = ExecutionLedger()
        ledger.begin("n1", decision_id="d1", proposal_id="p1")
        ledger.record_action("n1", "0:aaa")
        ledger.record_action("n1", "0:aaa")  # idempotent
        ledger.finish("n1", success=False)
        row = ledger.begin("n1", decision_id="d1", proposal_id="p1")
        assert row.completed_actions == frozenset({"0:aaa"})

    def test_nonce_bound_to_its_decision_and_proposal(self):
        ledger = ExecutionLedger()
        ledger.begin("n1", decision_id="d1", proposal_id="p1")
        ledger.finish("n1", success=False)
        with pytest.raises(ExecutionReplayError, match="bound to"):
            ledger.begin("n1", decision_id="d_other", proposal_id="p1")

    def test_finish_on_an_unknown_nonce_is_refused(self):
        ledger = ExecutionLedger()
        with pytest.raises(ExecutionReplayError, match="no execution"):
            ledger.finish("nope", success=True)

    def test_prune_keys_on_finished_or_first_seen(self):
        """An abandoned in-progress row ages out on the same clock as a finished
        one — COALESCE(finished_at, first_seen_at)."""
        ledger = ExecutionLedger()
        stale = (utcnow() - timedelta(days=30)).isoformat()

        ledger.begin("old_done", decision_id="d1", proposal_id="p1")
        ledger.finish("old_done", success=True)
        ledger.begin("old_stuck", decision_id="d2", proposal_id="p2")  # never finished
        ledger.record_action("old_stuck", "0:aaa")
        ledger.begin("fresh", decision_id="d3", proposal_id="p3")

        ledger._conn.execute(
            "UPDATE executions SET first_seen_at = ?, finished_at = ? "
            "WHERE nonce = 'old_done'", (stale, stale),
        )
        ledger._conn.execute(
            "UPDATE executions SET first_seen_at = ? WHERE nonce = 'old_stuck'", (stale,)
        )

        assert ledger.prune(max_age_seconds=3600) == 2
        assert ledger.status("old_done") is None
        assert ledger.status("old_stuck") is None
        assert ledger.status("fresh") == STATUS_IN_PROGRESS
        assert ledger.completed_actions("old_stuck") == frozenset()

    def test_prune_retains_a_finished_row_younger_than_the_horizon(self):
        ledger = ExecutionLedger()
        ledger.begin("n1", decision_id="d1", proposal_id="p1")
        ledger.finish("n1", success=True)
        assert ledger.prune(max_age_seconds=3600) == 0
        assert ledger.status("n1") == STATUS_COMPLETE
