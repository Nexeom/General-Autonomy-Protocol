"""Authority must still hold when each new side effect starts."""

import json
from datetime import timedelta

import pytest

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair, sign
from gap_kernel.execution.fabric import ExecutionError, ExecutionFabric, OOBVerificationError
from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.verification.execution_ledger import ExecutionLedger, STATUS_FAILED


@pytest.fixture
def setup(monkeypatch, tmp_path):
    now = utcnow()
    clock = [now]
    monkeypatch.setattr("gap_kernel.execution.fabric.utcnow", lambda: clock[0])
    monkeypatch.setattr("gap_kernel.governance.kernel.utcnow", lambda: clock[0])
    world = WorldModel(entities={}, last_reconciled=now)
    intent = IntentVector(id="intent", objective="read records", priority=50,
                          hard_constraints=[], soft_constraints=[],
                          created_by="operator", created_at=now)
    kernel = GovernanceKernel(decision_ttl_seconds=30)
    ledger = ExecutionLedger(str(tmp_path / "executions.sqlite"))
    private, public = generate_keypair()

    def create(*, needs_approval=False, before_dispatch=None, kill_switch=None):
        proposal = StrategyProposal(
            id="batch", intent_id=intent.id, attempt_number=1,
            plan_description="two reads", estimated_cost=0, rationale="test",
            generated_at=now,
            actions=[PlannedAction(action_type="query_crm", target=target,
                                   parameters={}, risk_score=6 if needs_approval else 1)
                     for target in ("first", "second")],
        )
        decision = kernel.evaluate_proposal(proposal, [intent], world)
        if needs_approval:
            decision.human_approver_public_key_id = "operator"
            decision.human_approval_timestamp = now
            decision.human_approval_valid_until = now + timedelta(seconds=20)
            decision.human_approval_signature = sign(
                private, ExecutionFabric._oob_signed_message(decision),
            )
        fabric = ExecutionFabric(
            world, kernel_public_key_hex=kernel.public_key_hex,
            execution_ledger=ledger,
            public_key_registry=PublicKeyRegistry({"operator": public}),
            before_dispatch=before_dispatch, kill_switch=kill_switch,
        )
        return proposal, decision, fabric

    yield now, clock, ledger, private, create
    ledger.close()


@pytest.mark.parametrize("deadline", ["decision", "approval"])
def test_expiry_after_first_action_blocks_second_and_preserves_result(setup, deadline):
    now, clock, ledger, _, create = setup
    proposal, decision, fabric = create(needs_approval=deadline == "approval")
    calls = []

    def dispatch(action):
        calls.append(action.target)
        clock[0] = now + timedelta(seconds=30 if deadline == "decision" else 20)
        return {"receipt": "first-result"}

    fabric.register_executor("query_crm", dispatch)
    with pytest.raises(ExecutionError, match="expired"):
        fabric.execute(proposal, decision)
    assert calls == ["first"]
    assert ledger.status(decision.nonce) == STATUS_FAILED
    outcome = ledger.outcomes(decision.nonce)[-1]["result"]
    assert outcome["success"] is False
    assert outcome["actions_completed"][0]["data"] == {"receipt": "first-result"}
    assert outcome["actions_failed"][0]["target"] == "second"
    assert outcome["actions_failed"][0]["failure_stage"] == "before_dispatch"


def test_hook_failure_stops_batch_and_resume_skips_prior_effect(setup):
    _, _, ledger, _, create = setup
    guarded = []
    blocked = [True]

    def guard(action):
        guarded.append(action.target)
        if action.target == "second" and blocked[0]:
            raise ExecutionError("evidence unavailable")

    proposal, decision, fabric = create(before_dispatch=guard)
    calls = []
    fabric.register_executor("query_crm", lambda action: calls.append(action.target) or {"receipt": action.target})
    with pytest.raises(ExecutionError, match="evidence unavailable"):
        fabric.execute(proposal, decision)
    assert calls == ["first"]
    assert ledger.status(decision.nonce) == STATUS_FAILED
    blocked[0] = False
    result = fabric.execute(proposal, decision)
    assert result.success
    assert calls == ["first", "second"]
    assert guarded == ["first", "second", "second"]
    assert result.actions_completed[0]["skipped"] is True
    assert result.actions_completed[0]["data"] == {"receipt": "first"}


def test_expiry_during_trusted_hook_cannot_dispatch(setup):
    now, clock, ledger, _, create = setup

    def guard(action):
        clock[0] = now + timedelta(seconds=30)

    proposal, decision, fabric = create(before_dispatch=guard)
    calls = []
    fabric.register_executor("query_crm", lambda action: calls.append(action.target) or {})
    with pytest.raises(ExecutionError, match="expired"):
        fabric.execute(proposal, decision)
    assert calls == []
    assert ledger.status(decision.nonce) == STATUS_FAILED


def test_mid_batch_kill_switch_blocks_the_next_effect(setup):
    _, _, ledger, _, create = setup
    switch = KillSwitch()
    proposal, decision, fabric = create(kill_switch=switch)
    calls = []

    def dispatch(action):
        calls.append(action.target)
        switch.engage("second")
        return {}

    fabric.register_executor("query_crm", dispatch)
    with pytest.raises(ExecutionError, match="kill-switch"):
        fabric.execute(proposal, decision)
    assert calls == ["first"]
    assert ledger.status(decision.nonce) == STATUS_FAILED


def test_hook_termination_is_recorded_but_not_swallowed(setup):
    _, _, ledger, _, create = setup

    def guard(action):
        raise KeyboardInterrupt("operator interrupted")

    proposal, decision, fabric = create(before_dispatch=guard)
    with pytest.raises(KeyboardInterrupt, match="operator interrupted"):
        fabric.execute(proposal, decision)
    assert ledger.status(decision.nonce) == STATUS_FAILED
    assert ledger.outcomes(decision.nonce)[-1]["result"]["success"] is False


def test_approval_timestamp_tampering_invalidates_signature(setup):
    now, clock, _, _, create = setup
    proposal, decision, fabric = create(needs_approval=True)
    clock[0] = now + timedelta(seconds=10)
    decision.human_approval_timestamp = now + timedelta(seconds=1)
    with pytest.raises(OOBVerificationError, match="signature is invalid"):
        fabric.execute(proposal, decision)


def test_v1_approval_is_not_silently_accepted(setup):
    _, _, _, private, create = setup
    proposal, decision, fabric = create(needs_approval=True)
    payload = json.loads(ExecutionFabric._oob_signed_message(decision))
    assert payload["_domain"] == "gap.oob_approval.v2"
    payload.pop("approved_at")
    payload["_domain"] = "gap.oob_approval.v1"
    decision.human_approval_signature = sign(private, json.dumps(payload, sort_keys=True))
    with pytest.raises(OOBVerificationError, match="signature is invalid"):
        fabric.execute(proposal, decision)


def test_retry_recovers_approval_committed_before_reservation_marker(setup, monkeypatch):
    _, _, ledger, _, create = setup
    proposal, decision, fabric = create(needs_approval=True)
    calls = []
    fabric.register_executor("query_crm", lambda action: calls.append(action.target) or {})
    record_action = ledger.record_action
    fail_once = [True]

    def persist_action(nonce, key, **kwargs):
        if key == "__oob_reservation__" and fail_once[0]:
            fail_once[0] = False
            raise RuntimeError("reservation marker unavailable")
        record_action(nonce, key, **kwargs)

    monkeypatch.setattr(ledger, "record_action", persist_action)
    with pytest.raises(RuntimeError, match="reservation marker unavailable"):
        fabric.execute(proposal, decision)
    assert calls == []
    assert ledger.status(decision.nonce) == STATUS_FAILED
    assert fabric._oob_ledger.has_been_used(decision.id, decision.human_approval_signature)
    assert fabric.execute(proposal, decision).success
    assert calls == ["first", "second"]


@pytest.mark.parametrize("invalid", [
    "missing", "naive", "future", "before_decision", "expired", "outlives_decision",
])
def test_authentically_signed_invalid_approval_time_is_rejected(setup, invalid):
    now, clock, ledger, private, create = setup
    proposal, decision, fabric = create(needs_approval=True)
    clock[0] = now + timedelta(seconds=10)
    if invalid == "missing":
        decision.human_approval_timestamp = None
    elif invalid == "naive":
        decision.human_approval_timestamp = now.replace(tzinfo=None)
    elif invalid == "future":
        decision.human_approval_timestamp = clock[0] + timedelta(seconds=1)
    elif invalid == "before_decision":
        decision.human_approval_timestamp = now - timedelta(seconds=1)
    elif invalid == "expired":
        decision.human_approval_valid_until = clock[0]
    else:
        decision.human_approval_valid_until = decision.expires_at + timedelta(seconds=1)
    decision.human_approval_signature = sign(private, ExecutionFabric._oob_signed_message(decision))
    with pytest.raises(OOBVerificationError):
        fabric.execute(proposal, decision)
    assert ledger.status(decision.nonce) is None
