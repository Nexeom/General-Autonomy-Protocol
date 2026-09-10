"""SDK receipt values retain durable completion and cannot cause duplicate retries."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from gap_kernel._time import utcnow
from gap_kernel.execution.fabric import ExecutionError, ExecutionFabric, ReplayExecutionError
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.verification.execution_ledger import STATUS_COMPLETE, STATUS_FAILED, ExecutionLedger
from gap_kernel.verification.oob_ledger import OOBLedger


SDK_VALUES = [
    pytest.param(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                 "2026-01-02T03:04:05Z", id="datetime"),
    pytest.param(UUID("da6b1273-d040-4b8d-985e-0d45a9b74b7f"),
                 "da6b1273-d040-4b8d-985e-0d45a9b74b7f", id="uuid"),
    pytest.param(Decimal("12.50"), "12.50", id="decimal"),
]


def signed_operation(targets=("first",)):
    now = utcnow()
    world = WorldModel(last_reconciled=now)
    intent = IntentVector(
        id="receipt-intent", objective="Record local receipts", priority=50,
        hard_constraints=[], soft_constraints=[], created_by="operator", created_at=now,
    )
    proposal = StrategyProposal(
        id="receipt-proposal", intent_id=intent.id, attempt_number=1,
        plan_description="Harmless local executor effects", estimated_cost=0,
        rationale="Verify durable receipts", generated_at=now,
        actions=[PlannedAction(action_type="update_record", target=target,
                               parameters={}, risk_score=1) for target in targets],
    )
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(proposal, [intent], world)
    assert decision.verdict.value == "approved"
    assert decision.authorization_level.value == "L0"
    assert decision.decision_signature and decision.nonce
    return SimpleNamespace(world=world, kernel=kernel, proposal=proposal, decision=decision)


@contextmanager
def durable_fabric(case, ledger_path):
    with closing(ExecutionLedger(str(ledger_path))) as ledger, closing(OOBLedger()) as approvals:
        fabric = ExecutionFabric(
            case.world, kernel_public_key_hex=case.kernel.public_key_hex,
            execution_ledger=ledger, oob_ledger=approvals,
        )
        yield fabric, ledger


def tool_receipts(ledger, nonce):
    return {key: value for key, value in ledger.action_results(nonce).items()
            if key != "__oob_reservation__"}


@pytest.mark.parametrize("sdk_value,normalized", SDK_VALUES)
def test_sdk_receipt_completes_once_and_survives_durable_reopen(tmp_path, sdk_value, normalized):
    case = signed_operation()
    path = tmp_path / "executions.sqlite"
    effects = []

    def executor(action):
        effects.append(action.target)
        return {"receipt": "first-receipt", "sdk": {"value": sdk_value}}

    with durable_fabric(case, path) as (fabric, ledger):
        fabric.register_executor("update_record", executor)
        completed = fabric.execute(case.proposal, case.decision)
        assert completed.success is True
        returned_value = completed.actions_completed[0]["data"]["sdk"]["value"]
        assert isinstance(returned_value, type(sdk_value))
        assert returned_value == sdk_value
        result_json = completed.model_dump(mode="json")
        assert result_json["actions_completed"][0]["data"] == {
            "receipt": "first-receipt", "sdk": {"value": normalized},
        }
        retained = tool_receipts(ledger, case.decision.nonce)
        assert len(retained) == 1
        assert next(iter(retained.values())) == result_json["actions_completed"][0]
        assert ledger.status(case.decision.nonce) == STATUS_COMPLETE
        with pytest.raises(ReplayExecutionError):
            fabric.execute(case.proposal, case.decision)
        assert effects == ["first"]

    with durable_fabric(case, path) as (restarted, ledger):
        restarted.register_executor("update_record", executor)
        assert tool_receipts(ledger, case.decision.nonce) == retained
        assert ledger.outcomes(case.decision.nonce)[0]["result"] == result_json
        with pytest.raises(ReplayExecutionError):
            restarted.execute(case.proposal, case.decision)
    assert effects == ["first"]


@pytest.mark.parametrize("sdk_value,normalized", SDK_VALUES)
def test_partial_retry_after_reopen_retains_normalized_receipt_without_repeating_effect(
    tmp_path, sdk_value, normalized,
):
    case = signed_operation(("first", "second"))
    path = tmp_path / "partial.sqlite"
    calls, effects = [], []

    def executor(action):
        calls.append(action.target)
        if action.target == "second" and calls.count("second") == 1:
            raise RuntimeError("temporary failure before second effect")
        effects.append(action.target)
        return {"receipt": action.target, "sdk": {"value": sdk_value}}

    with durable_fabric(case, path) as (fabric, ledger):
        fabric.register_executor("update_record", executor)
        failed = fabric.execute(case.proposal, case.decision)
        assert failed.success is False
        assert ledger.status(case.decision.nonce) == STATUS_FAILED
        retained = tool_receipts(ledger, case.decision.nonce)
        assert len(retained) == 1
        first_receipt = next(iter(retained.values()))
        assert first_receipt["data"] == {"receipt": "first", "sdk": {"value": normalized}}
        assert effects == ["first"]

    with durable_fabric(case, path) as (restarted, ledger):
        restarted.register_executor("update_record", executor)
        assert tool_receipts(ledger, case.decision.nonce) == retained
        completed = restarted.execute(case.proposal, case.decision)
        assert completed.success is True
        assert completed.model_dump(mode="json")["actions_completed"][0] == {
            **first_receipt, "skipped": True,
        }
        assert all(ledger.action_results(case.decision.nonce)[key] == receipt
                   for key, receipt in retained.items())
        assert ledger.status(case.decision.nonce) == STATUS_COMPLETE
        assert [outcome["result"]["success"] for outcome in ledger.outcomes(case.decision.nonce)] == [
            False, True,
        ]
        with pytest.raises(ReplayExecutionError):
            restarted.execute(case.proposal, case.decision)
    assert calls == ["first", "second", "second"]
    assert effects == ["first", "second"]


def test_unsupported_receipt_records_explicit_uncertainty_and_stops_remaining_actions(tmp_path):
    class UnsupportedReceipt:
        pass

    case = signed_operation(("first", "second"))
    path = tmp_path / "unsupported.sqlite"
    effects = []

    def executor(action):
        effects.append(action.target)
        return {"receipt": UnsupportedReceipt()}

    with durable_fabric(case, path) as (fabric, ledger):
        fabric.register_executor("update_record", executor)
        with pytest.raises(ExecutionError) as raised:
            fabric.execute(case.proposal, case.decision)
        assert raised.value.__cause__ is not None
        assert effects == ["first"]
        assert ledger.status(case.decision.nonce) == STATUS_FAILED
        assert tool_receipts(ledger, case.decision.nonce) == {}
        outcomes = ledger.outcomes(case.decision.nonce)
        assert len(outcomes) == 1
        result = outcomes[0]["result"]
        assert result["success"] is False
        assert result["outcome_unknown"] is True
        assert result["actions_completed"] == []
        assert len(result["actions_failed"]) == 1
        failure = result["actions_failed"][0]
        assert failure["target"] == "first"
        assert failure["failure_stage"] == "receipt_persistence"
        assert failure["outcome_unknown"] is True
        assert failure["error"]
        assert outcomes[0]["decision"] == case.decision.model_dump(mode="json")

    with durable_fabric(case, path) as (_, ledger):
        assert ledger.status(case.decision.nonce) == STATUS_FAILED
        assert ledger.outcomes(case.decision.nonce) == outcomes
        assert tool_receipts(ledger, case.decision.nonce) == {}
    assert effects == ["first"]


def test_executor_termination_after_effect_propagates_and_journals_uncertainty(tmp_path):
    case = signed_operation(("first", "second"))
    effects = []

    def executor(action):
        effects.append(action.target)
        raise KeyboardInterrupt("interrupted after effect")

    with durable_fabric(case, tmp_path / "terminated.sqlite") as (fabric, ledger):
        fabric.register_executor("update_record", executor)
        with pytest.raises(KeyboardInterrupt, match="interrupted after effect"):
            fabric.execute(case.proposal, case.decision)
        assert effects == ["first"]
        assert ledger.status(case.decision.nonce) == STATUS_FAILED
        assert tool_receipts(ledger, case.decision.nonce) == {}
        outcomes = ledger.outcomes(case.decision.nonce)
        assert len(outcomes) == 1
        result = outcomes[0]["result"]
        assert result["success"] is False
        assert result["outcome_unknown"] is True
        assert result["actions_completed"] == []
        assert len(result["actions_failed"]) == 1
        failure = result["actions_failed"][0]
        assert failure["target"] == "first"
        assert failure["failure_stage"] == "dispatch"
        assert failure["outcome_unknown"] is True
