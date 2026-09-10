"""A committed tool effect remains uncertain until its receipt can be recovered."""

import sqlite3

from gap_kernel.gateway.approval import sign_approval
from tests.test_gateway import (
    backend as backend,
    configured as configured,
    execute,
    gateway as gateway,
    proposal,
    propose,
    running_gateway,
)


def reject_receipt(ledger, action_index):
    """Fail the real SQLite receipt write, after the sink commits its note."""
    ledger._conn.execute(f"""CREATE TRIGGER reject_tool_receipt
        BEFORE INSERT ON execution_actions
        WHEN NEW.result_json IS NOT NULL AND NEW.action_key LIKE '{action_index}:%'
        BEGIN SELECT RAISE(ABORT, 'receipt_storage_fault'); END""")


def assert_recovery_required(instance, response, completed_count):
    assert response["status"] == "interrupted"
    assert response["audit_status"] == "recovery_required"
    assert response["result"]["success"] is False
    assert response["result"]["outcome_unknown"] is True
    assert len(response["result"]["actions_completed"]) == completed_count
    assert len(response["result"]["actions_failed"]) == 1
    failure = response["result"]["actions_failed"][0]
    assert failure["action_type"] == "send_email"
    assert failure["target"] == "demo"
    assert failure["success"] is False
    assert failure["failure_stage"] == "receipt_persistence"
    assert failure["outcome_unknown"] is True

    audit = instance.client.get("/v1/audit").json()
    assert audit["valid"] is True
    assert audit["complete"] is False
    assert audit["pending_outcomes"] == 0
    assert audit["recovery_required"] == 1
    records = instance.service.lineage.get_by_cycle("request")
    assert len(records) == 2
    assert records[-1].execution_success is False
    assert records[-1].execution_result == response["result"]


def test_committed_notify_receipt_failure_survives_restart_and_retries_idempotently(
    configured, backend, tmp_path,
):
    state = tmp_path / "state"
    with running_gateway(configured, state, backend) as first:
        proposed = propose(first, proposal(tool="notify"))
        approval = sign_approval(proposed, first.identity)
        nonce = proposed["decision"]["nonce"]
        reject_receipt(first.service.execution_ledger, 0)

        attempted = execute(first, "request", approval)
        assert attempted.status_code == 200, attempted.text
        failed = attempted.json()
        assert backend.notes() == 1
        assert len(backend.calls) == 1
        assert_recovery_required(first, failed, completed_count=0)
        assert not any(first.service.execution_ledger.action_results(nonce).values())
        journal = first.service.execution_ledger.outcomes(nonce)
        assert len(journal) == 1
        assert journal[0]["result"] == failed["result"]
        assert journal[0]["decision"]["human_approval_signature"] == (
            approval["human_approval_signature"]
        )

    with running_gateway(configured, state, backend) as restarted:
        recovered = restarted.client.get("/v1/requests/request").json()
        assert recovered["result"] == failed["result"]
        assert_recovery_required(restarted, recovered, completed_count=0)
        assert len(backend.calls) == 1, "Reconciliation must not dispatch the tool"
        assert backend.notes() == 1

        restarted.service.execution_ledger._conn.execute("DROP TRIGGER reject_tool_receipt")
        retried = execute(restarted, "request", approval)
        assert retried.status_code == 200, retried.text
        completed = retried.json()
        assert completed["status"] == "completed"
        assert completed["audit_status"] == "recorded"
        assert completed["result"]["success"] is True
        assert not completed["result"].get("outcome_unknown", False)
        assert len(completed["result"]["actions_completed"]) == 1
        assert completed["result"]["actions_failed"] == []
        assert len(backend.calls) == 2
        assert backend.calls[0] == backend.calls[1], "Retry must retain the tool idempotency key"
        assert backend.notes() == 1

        audit = restarted.client.get("/v1/audit").json()
        assert audit["valid"] is True
        assert audit["complete"] is True
        assert audit["recovery_required"] == 0
        assert audit["pending_outcomes"] == 0
        journal = restarted.service.execution_ledger.outcomes(nonce)
        assert [outcome["attempt"] for outcome in journal] == [1, 2]
        assert journal[0]["result"] == failed["result"]
        assert journal[1]["result"] == completed["result"]
        records = restarted.service.lineage.get_by_cycle("request")
        assert [record.execution_success for record in records[1:]] == [False, True]
        assert execute(restarted, "request", approval).status_code == 409
        assert backend.notes() == 1


def test_later_receipt_failure_preserves_prior_completion_on_retry(gateway):
    body = proposal(tool="notify", arguments={"message": "first"})
    body["actions"].append({
        "tool": "notify", "target": "demo", "arguments": {"message": "second"},
    })
    proposed = propose(gateway, body)
    approval = sign_approval(proposed, gateway.identity)
    ledger = gateway.service.execution_ledger
    reject_receipt(ledger, 1)

    attempted = execute(gateway, "request", approval)
    assert attempted.status_code == 200, attempted.text
    failed = attempted.json()
    assert gateway.backend.notes() == 2
    assert_recovery_required(gateway, failed, completed_count=1)
    first_receipt = failed["result"]["actions_completed"][0]
    receipts = [receipt for receipt in ledger.action_results(proposed["decision"]["nonce"]).values()
                if receipt is not None]
    assert receipts == [first_receipt]

    ledger._conn.execute("DROP TRIGGER reject_tool_receipt")
    retried = execute(gateway, "request", approval)
    assert retried.status_code == 200, retried.text
    completed = retried.json()
    assert completed["status"] == "completed"
    assert completed["audit_status"] == "recorded"
    assert completed["result"]["actions_failed"] == []
    assert completed["result"]["actions_completed"][0] == {**first_receipt, "skipped": True}
    assert len(completed["result"]["actions_completed"]) == 2
    calls = gateway.backend.calls
    assert [payload["message"] for _, _, payload in calls] == ["first", "second", "second"]
    assert calls[1] == calls[2], "Only the action without a durable receipt should be retried"
    assert gateway.backend.notes() == 2
    assert gateway.client.get("/v1/audit").json()["complete"] is True


def test_receipt_commit_then_error_retries_without_dispatching_again(gateway, monkeypatch):
    proposed = propose(gateway, proposal(tool="notify"))
    approval = sign_approval(proposed, gateway.identity)
    ledger = gateway.service.execution_ledger
    record_action = ledger.record_action

    def commit_then_error(nonce, action_key, *, result=None):
        record_action(nonce, action_key, result=result)
        if result is not None:
            raise sqlite3.OperationalError("receipt committed before acknowledgement failed")

    monkeypatch.setattr(ledger, "record_action", commit_then_error)
    attempted = execute(gateway, "request", approval)
    assert attempted.status_code == 200, attempted.text
    assert_recovery_required(gateway, attempted.json(), completed_count=0)
    assert gateway.backend.notes() == 1
    receipts = [receipt for receipt in ledger.action_results(proposed["decision"]["nonce"]).values()
                if receipt is not None]
    assert len(receipts) == 1

    monkeypatch.setattr(ledger, "record_action", record_action)
    retried = execute(gateway, "request", approval)
    assert retried.status_code == 200, retried.text
    completed = retried.json()
    assert completed["status"] == "completed"
    assert completed["audit_status"] == "recorded"
    assert completed["result"]["actions_completed"] == [{**receipts[0], "skipped": True}]
    assert completed["result"]["actions_failed"] == []
    assert len(gateway.backend.calls) == 1
    assert gateway.backend.notes() == 1
    assert gateway.client.get("/v1/audit").json()["complete"] is True
