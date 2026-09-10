"""Gateway process-death recovery, renewal and cross-instance dispatch exclusion."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from gap_kernel.gateway.approval import sign_approval
from gap_kernel.verification.execution_ledger import STATUS_IN_PROGRESS
from tests.test_gateway import (
    backend as backend,
    configured as configured,
    execute,
    gateway as gateway,
    proposal,
    propose,
    running_gateway,
)


REPO_ROOT = Path(__file__).resolve().parents[1]

CLAIM_THEN_DIE = """
import os
import sys
from pathlib import Path
from gap_kernel.gateway.service import GatewayService

configured, state = map(Path, sys.argv[1:])
service = GatewayService(configured / 'gateway' / 'config.json', state)
with service._exclusive():
    response = service._stored('request')
    decision = response['decision']
    service.execution_ledger.set_context(decision['nonce'], service._context(response))
    service.execution_ledger.begin(
        decision['nonce'], decision_id=decision['id'],
        proposal_id=response['proposal']['id'], decision=decision,
    )
    # No exception unwinding, connection close, or transaction cleanup occurs.
    os._exit(73)
"""

LEGACY_CLAIM_THEN_DIE = """
import os
import sys
from pathlib import Path
from gap_kernel.gateway.service import GatewayService

configured, state = map(Path, sys.argv[1:])
service = GatewayService(configured / 'gateway' / 'config.json', state)
with service._exclusive():
    response = service._stored('request')
    decision = response['decision']
    service.execution_ledger.begin(
        decision['nonce'], decision_id=decision['id'],
        proposal_id=response['proposal']['id'],
    )
    # Older records contain neither the audit context nor attempt authority.
    os._exit(73)
"""

COMMIT_NOTE_THEN_DIE = """
import json
import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from gap_kernel.gateway.sink import create_sink_app

from tests.test_gateway import LocalToolTransport, execute, running_gateway

configured, state, sink_path, approval_path, checkpoint = map(Path, sys.argv[1:])
token_file = configured / 'sink' / 'tool-token'
with TestClient(create_sink_app(str(token_file), str(sink_path))) as sink:
    transport = LocalToolTransport(sink, token_file.read_text().strip())
    def die_before_receipt(request, payload, response):
        assert response.status_code == 200
        checkpoint.write_text(json.dumps(payload))
        # The sink's committed note survives; no gateway receipt is returned.
        os._exit(74)
    transport.after_response = die_before_receipt
    with running_gateway(configured, state, transport) as gateway:
        execute(gateway, 'request', json.loads(approval_path.read_text()))
raise AssertionError('crash injection did not run')
"""


def run_crashing_child(script, expected_exit, *paths):
    result = subprocess.run(
        [sys.executable, "-c", script, *(str(path) for path in paths)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr


def advance_past_decision_expiry(monkeypatch, response):
    after_expiry = datetime.fromisoformat(response["decision"]["expires_at"]) + timedelta(seconds=1)
    for module in (
        "gap_kernel.execution.fabric", "gap_kernel.governance.kernel",
        "gap_kernel.gateway.approval", "gap_kernel.gateway.service",
        "gap_kernel.verification.execution_ledger", "gap_kernel.world_model.attestation",
    ):
        monkeypatch.setattr(module + ".utcnow", lambda: after_expiry)


def test_process_death_after_durable_claim_can_resume_before_lease_expires(
    configured, backend, tmp_path,
):
    state = tmp_path / "claim-state"
    with running_gateway(configured, state, backend) as first:
        response = propose(first, proposal())
    nonce = response["decision"]["nonce"]

    run_crashing_child(CLAIM_THEN_DIE, 73, configured, state)

    with running_gateway(configured, state, backend) as restarted:
        ledger = restarted.service.execution_ledger
        assert ledger.status(nonce) == STATUS_IN_PROGRESS
        assert ledger.current_attempt(nonce) == response["decision"]
        attempted_at = ledger._conn.execute(
            "SELECT last_attempt_at FROM executions WHERE nonce=?", (nonce,),
        ).fetchone()[0]
        assert not ledger._lease_expired(attempted_at)
        observed = restarted.client.get("/v1/requests/request")
        assert observed.status_code == 200
        assert observed.json()["status"] == "interrupted"
        assert backend.calls == []  # Observation never dispatches.

        completed = execute(restarted, "request")
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert ledger._conn.execute(
            "SELECT attempts FROM executions WHERE nonce=?", (nonce,),
        ).fetchone()[0] == 2
    assert backend.calls == [("GET", "/records/demo", None)]


def test_process_death_after_sink_commit_reuses_exact_tool_key_on_restart(
    configured, backend, tmp_path,
):
    state = tmp_path / "committed-state"
    approval_path = tmp_path / "approval.json"
    checkpoint = tmp_path / "committed-tool-request.json"
    with running_gateway(configured, state, backend) as first:
        response = propose(first, proposal(tool="notify"))
        approval = sign_approval(response, first.identity)
        approval_path.write_text(json.dumps(approval))

    run_crashing_child(
        COMMIT_NOTE_THEN_DIE, 74, configured, state,
        tmp_path / "sink.sqlite", approval_path, checkpoint,
    )
    committed_request = json.loads(checkpoint.read_text())
    assert backend.notes() == 1

    with running_gateway(configured, state, backend) as restarted:
        nonce = response["decision"]["nonce"]
        assert restarted.service.execution_ledger.status(nonce) == STATUS_IN_PROGRESS
        assert restarted.client.get("/v1/requests/request").json()["status"] == "interrupted"
        assert backend.calls == []
        completed = execute(restarted, "request", approval)
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert completed.json()["result"]["actions_completed"][0]["data"]["receipt"] == (
            committed_request["idempotency_key"]
        )
    assert backend.notes() == 1
    assert backend.calls == [("POST", "/notify", committed_request)]


@pytest.mark.parametrize("failure", ["fail_before_message", "lose_response_message"])
def test_expired_partial_request_renews_same_operation_with_fresh_approval(
    gateway, monkeypatch, failure,
):
    body = proposal(tool="notify", arguments={"message": "first"})
    body["actions"].append({
        "tool": "notify", "target": "demo", "arguments": {"message": "second"},
    })
    original = propose(gateway, body)
    old_approval = sign_approval(original, gateway.identity)
    setattr(gateway.backend, failure, "second")
    failed = execute(gateway, "request", old_approval)
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    first_receipt = failed.json()["result"]["actions_completed"][0]["data"]
    assert gateway.backend.notes() == (1 if failure == "fail_before_message" else 2)
    old_nonce = original["decision"]["nonce"]
    old_receipts = gateway.service.execution_ledger.action_results(old_nonce)
    assert "__oob_reservation__" in old_receipts

    advance_past_decision_expiry(monkeypatch, original)
    call_count = len(gateway.backend.calls)
    expired = execute(gateway, "request", old_approval)
    assert expired.status_code == 409
    assert expired.json()["error"] == "authorization_invalid_or_spent"
    assert len(gateway.backend.calls) == call_count

    renewed_response = gateway.client.post("/v1/requests/request/reauthorize")
    assert renewed_response.status_code == 200, renewed_response.text
    renewed = renewed_response.json()
    assert renewed["status"] == "awaiting_approval"
    assert renewed["proposal"] == original["proposal"]
    assert renewed["decision"]["proposal_digest"] == original["decision"]["proposal_digest"]
    assert renewed["decision"]["id"] != original["decision"]["id"]
    assert renewed["decision"]["nonce"] != old_nonce
    assert renewed["prior_nonces"] == [old_nonce]
    assert len(gateway.backend.calls) == call_count
    inherited = gateway.service.execution_ledger.action_results(renewed["decision"]["nonce"])
    assert inherited == {key: value for key, value in old_receipts.items()
                         if key != "__oob_reservation__"}
    assert execute(gateway, "request").status_code == 403
    assert execute(gateway, "request", old_approval).status_code == 403
    assert len(gateway.backend.calls) == call_count

    new_approval = sign_approval(renewed, gateway.identity)
    completed = execute(gateway, "request", new_approval)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    first = completed.json()["result"]["actions_completed"][0]
    assert first["skipped"] is True
    assert first["data"] == first_receipt
    assert gateway.backend.notes() == 2
    requests = [payload for _, path, payload in gateway.backend.calls if path == "/notify"]
    assert [request["message"] for request in requests] == ["first", "second", "second"]
    assert requests[1]["idempotency_key"] == requests[2]["idempotency_key"]


def test_renewal_supersedes_an_unexpired_approval(gateway):
    original = propose(gateway, proposal(tool="notify"))
    old_approval = sign_approval(original, gateway.identity)
    response = gateway.client.post("/v1/requests/request/reauthorize")
    assert response.status_code == 200, response.text
    renewed = response.json()
    denied = execute(gateway, "request", old_approval)
    assert denied.status_code == 403
    assert denied.json()["error"] == "valid_human_approval_required"
    assert gateway.backend.calls == []
    assert execute(gateway, "request", sign_approval(renewed, gateway.identity)).json()["status"] == (
        "completed"
    )
    assert gateway.backend.notes() == 1


def test_completed_request_cannot_be_reauthorized(gateway):
    propose(gateway, proposal())
    assert execute(gateway, "request").json()["status"] == "completed"
    denied = gateway.client.post("/v1/requests/request/reauthorize")
    assert denied.status_code == 409
    assert denied.json()["error"] == "request_not_renewable"
    assert len(gateway.backend.calls) == 1


@pytest.mark.parametrize("authorization", ["", "Bearer wrong"])
def test_reauthorization_requires_authentication(gateway, authorization):
    original = propose(gateway, proposal(tool="notify"))
    denied = gateway.client.post(
        "/v1/requests/request/reauthorize", headers={"Authorization": authorization},
    )
    assert denied.status_code == 401
    stored = gateway.client.get("/v1/requests/request").json()
    assert stored["decision"] == original["decision"]
    assert gateway.backend.calls == []


def test_live_dispatch_excludes_other_instances_and_returns_retryable_busy(
    configured, backend, tmp_path,
):
    state = tmp_path / "shared-state"
    started, release = Event(), Event()

    def hold_dispatch(request, payload, response):
        started.set()
        assert release.wait(timeout=15), "test did not release live dispatch"

    with running_gateway(configured, state, backend) as first:
        with running_gateway(configured, state, backend) as second:
            response = propose(first, proposal())
            second.service._db.execute("PRAGMA busy_timeout=25")
            backend.after_response = hold_dispatch
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(execute, first, "request")
                try:
                    assert started.wait(timeout=10), "first instance never dispatched"
                    for method, path, body in (
                        ("POST", "/v1/requests/request/execute", {}),
                        ("POST", "/v1/requests/request/reauthorize", None),
                        ("GET", "/v1/requests/request", None),
                        ("POST", "/v1/proposals", proposal("another")),
                    ):
                        blocked = second.client.request(method, path, json=body)
                        assert blocked.status_code == 503, blocked.text
                        assert blocked.json()["error"] == "gateway_busy"
                    assert len(backend.calls) == 1
                    assert second.service.execution_ledger.status(
                        response["decision"]["nonce"]
                    ) == STATUS_IN_PROGRESS
                finally:
                    release.set()
                completed = pending.result(timeout=10)
            assert completed.status_code == 200, completed.text
            assert completed.json()["status"] == "completed"
            replay = execute(second, "request")
            assert replay.status_code == 409
            assert replay.json()["error"] == "authorization_spent"
            assert len(backend.calls) == 1


@pytest.mark.parametrize("invalid_retry", ["expired", "bad_approval"])
def test_invalid_retry_before_observation_preserves_interruption_until_renewed(
    configured, backend, tmp_path, monkeypatch, invalid_retry,
):
    state = tmp_path / "uncertain-state"
    approval_path = tmp_path / "interrupted-approval.json"
    checkpoint = tmp_path / "uncertain-tool-request.json"
    with running_gateway(configured, state, backend) as first:
        original = propose(first, proposal(tool="notify"))
        approval = sign_approval(original, first.identity)
        approval_path.write_text(json.dumps(approval))

    run_crashing_child(
        COMMIT_NOTE_THEN_DIE, 74, configured, state,
        tmp_path / "sink.sqlite", approval_path, checkpoint,
    )
    committed_request = json.loads(checkpoint.read_text())
    assert backend.notes() == 1
    retry_approval = dict(approval)
    if invalid_retry == "expired":
        advance_past_decision_expiry(monkeypatch, original)
    else:
        signature = retry_approval["human_approval_signature"]
        retry_approval["human_approval_signature"] = (
            ("0" if signature[0] != "0" else "1") + signature[1:]
        )

    with running_gateway(configured, state, backend) as restarted:
        # First gateway API interaction after restart is an invalid retry.
        # A failed retry must not erase evidence of the interrupted attempt.
        denied = execute(restarted, "request", retry_approval)
        assert denied.status_code == (409 if invalid_retry == "expired" else 403), denied.text
        assert backend.calls == []
        outcomes = restarted.service.execution_ledger.outcomes(original["decision"]["nonce"])
        assert len(outcomes) == 1
        assert outcomes[0]["result"]["outcome_unknown"] is True
        assert outcomes[0]["result"]["timestamp_kind"] == "recovery_observation"
        assert outcomes[0]["decision"]["human_approval_signature"] == approval["human_approval_signature"]

        observed = restarted.client.get("/v1/requests/request")
        assert observed.status_code == 200, observed.text
        assert observed.json()["status"] == "interrupted"
        assert observed.json()["audit_status"] == "recovery_required"
        audit = restarted.client.get("/v1/audit").json()
        assert audit["valid"] is True
        assert audit["complete"] is False
        assert audit["recovery_required"] == 1

        renewed_response = restarted.client.post("/v1/requests/request/reauthorize")
        assert renewed_response.status_code == 200, renewed_response.text
        renewed = renewed_response.json()
        assert renewed["decision"]["proposal_digest"] == original["decision"]["proposal_digest"]
        assert renewed["decision"]["nonce"] != original["decision"]["nonce"]
        completed = execute(restarted, "request", sign_approval(renewed, restarted.identity))
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert completed.json()["audit_status"] == "recorded"
        receipt = completed.json()["result"]["actions_completed"][0]["data"]["receipt"]
        assert receipt == committed_request["idempotency_key"]
        final_audit = restarted.client.get("/v1/audit").json()
        assert final_audit["valid"] is True
        assert final_audit["complete"] is True
        assert final_audit["recovery_required"] == 0
    assert backend.notes() == 1
    assert backend.calls == [("POST", "/notify", committed_request)]


@pytest.mark.parametrize("expired", [False, True])
def test_legacy_claim_requires_reauthorization_without_erasing_uncertainty(
    configured, backend, tmp_path, monkeypatch, expired,
):
    state = tmp_path / "legacy-claim-state"
    with running_gateway(configured, state, backend) as first:
        original = propose(first, proposal())
    nonce = original["decision"]["nonce"]
    run_crashing_child(LEGACY_CLAIM_THEN_DIE, 73, configured, state)
    if expired:
        advance_past_decision_expiry(monkeypatch, original)

    with running_gateway(configured, state, backend) as restarted:
        ledger = restarted.service.execution_ledger
        assert ledger.current_attempt(nonce) is None
        # No GET or audit request has reconciled the abandoned claim yet.
        denied = execute(restarted, "request")
        assert denied.status_code == 409
        assert denied.json()["error"] == "reauthorization_required"
        assert ledger.status(nonce) == STATUS_IN_PROGRESS
        assert ledger.outcomes(nonce) == []  # Missing historical authority is not invented.
        assert backend.calls == []

        observed = restarted.client.get("/v1/requests/request")
        assert observed.status_code == 200
        assert observed.json()["status"] == "interrupted"
        assert observed.json()["audit_status"] == "recovery_required"
        audit = restarted.client.get("/v1/audit").json()
        assert audit["valid"] is True
        assert audit["complete"] is False
        assert audit["recovery_required"] == 1
        assert backend.calls == []

        renewed_response = restarted.client.post("/v1/requests/request/reauthorize")
        assert renewed_response.status_code == 200, renewed_response.text
        renewed = renewed_response.json()
        assert renewed["status"] == "ready"
        assert renewed["proposal"] == original["proposal"]
        assert renewed["decision"]["nonce"] != nonce
        assert renewed["decision"]["proposal_digest"] == original["decision"]["proposal_digest"]
        assert renewed["prior_nonces"] == [nonce]
        assert ledger.status(nonce) == STATUS_IN_PROGRESS
        # Renewal alone does not resolve the old execution's uncertain outcome.
        awaiting_execution = restarted.client.get("/v1/requests/request").json()
        assert awaiting_execution["status"] == "ready"
        assert awaiting_execution["audit_status"] == "recovery_required"
        assert restarted.client.get("/v1/audit").json()["complete"] is False
        assert backend.calls == []

        completed = execute(restarted, "request")
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert completed.json()["audit_status"] == "recorded"
        resolved_audit = restarted.client.get("/v1/audit").json()
        assert resolved_audit["valid"] is True
        assert resolved_audit["complete"] is True
        assert resolved_audit["recovery_required"] == 0
        assert ledger.current_attempt(nonce) is None
        assert ledger.outcomes(nonce) == []
    assert backend.calls == [("GET", "/records/demo", None)]
