"""Outcome recovery, audit completeness, and authority at every tool boundary."""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from gap_kernel.gateway.approval import sign_approval
from gap_kernel.models.world import EVIDENCE_ATTESTATION_PROPERTY
from tests.test_gateway import (  # fixtures are registered by pytest on import
    backend as backend, configured as configured, gateway as gateway,
    execute, proposal, propose, running_gateway,
)


def unavailable(*args, **kwargs):
    raise sqlite3.OperationalError("simulated unavailable lineage storage")


def test_committed_effect_remains_completed_while_audit_is_pending(gateway, monkeypatch):
    proposed = propose(gateway, proposal(tool="notify"))
    approval = sign_approval(proposed, gateway.identity)
    append = gateway.service.lineage.append
    monkeypatch.setattr(gateway.service.lineage, "append", unavailable)
    result = execute(gateway, "request", approval)
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["audit_status"] == "pending"
    assert gateway.backend.notes() == 1
    audit = gateway.client.get("/v1/audit").json()
    assert audit["valid"] is True
    assert audit["complete"] is False
    assert audit["pending_outcomes"] == 1
    monkeypatch.setattr(gateway.service.lineage, "append", append)
    recovered = gateway.client.get("/v1/requests/request").json()
    assert recovered["result"] == result.json()["result"]
    assert recovered["audit_status"] == "recorded"
    assert gateway.client.get("/v1/audit").json()["records"] == 2
    assert gateway.client.get("/v1/audit").json()["complete"] is True
    assert execute(gateway, "request", approval).status_code == 409
    assert gateway.backend.notes() == 1


def test_response_write_failure_recovers_exact_result_after_restart(configured, backend, tmp_path,
                                                                  monkeypatch):
    state = tmp_path / "state"
    with running_gateway(configured, state, backend) as first:
        proposed = propose(first, proposal(tool="notify"))
        approval = sign_approval(proposed, first.identity)
        real_save = first.service._save

        def fail_completed(response):
            if response["status"] == "completed":
                raise sqlite3.OperationalError("request response disk failure")
            real_save(response)

        monkeypatch.setattr(first.service, "_save", fail_completed)
        with pytest.raises(sqlite3.OperationalError, match="disk failure"):
            execute(first, "request", approval)
        assert backend.notes() == 1
        journal = first.service.execution_ledger.outcomes(proposed["decision"]["nonce"])
        expected = journal[-1]["result"]
    with running_gateway(configured, state, backend) as second:
        recovered = second.client.get("/v1/requests/request").json()
        assert recovered["status"] == "completed"
        assert recovered["result"] == expected
        assert recovered["audit_status"] == "recorded"
        expired = datetime.fromisoformat(proposed["decision"]["expires_at"]) + timedelta(seconds=1)
        monkeypatch.setattr("gap_kernel.execution.fabric.utcnow", lambda: expired)
        assert execute(second, "request", approval).json()["error"] == "authorization_spent"
        assert second.client.get("/v1/audit").json()["records"] == 2
        assert backend.notes() == 1


def test_audit_commit_followed_by_error_is_delivered_once(gateway, monkeypatch):
    proposed = propose(gateway, proposal())
    append = gateway.service.lineage.append

    def commit_then_fail(record):
        append(record)
        raise OSError("lost audit acknowledgement")

    monkeypatch.setattr(gateway.service.lineage, "append", commit_then_fail)
    assert execute(gateway, "request").json()["audit_status"] == "pending"
    assert gateway.service.lineage.count() == 2
    monkeypatch.setattr(gateway.service.lineage, "append", append)
    recovered = gateway.client.get("/v1/requests/request").json()
    assert recovered["audit_status"] == "recorded"
    assert recovered["decision"] == proposed["decision"]
    assert gateway.service.lineage.count() == 2


def test_pending_outcome_uses_original_context_after_deployment_changes(configured, backend,
                                                                      tmp_path, monkeypatch):
    state = tmp_path / "state"
    with running_gateway(configured, state, backend) as first:
        proposed = propose(first, proposal())
        original_world = first.service.world.model_dump(mode="json")
        monkeypatch.setattr(first.service.lineage, "append", unavailable)
        result = execute(first, "request").json()
        assert result["audit_status"] == "pending"
    config_path = configured / "gateway/config.json"
    config = json.loads(config_path.read_text())
    world_path = config_path.parent / config["world"]
    world = json.loads(world_path.read_text())
    world["entities"]["demo"]["properties"]["name"] = "changed after execution"
    world_path.write_text(json.dumps(world))
    with running_gateway(configured, state, backend) as second:
        recovered = second.client.get("/v1/requests/request").json()
        assert recovered["status"] == "completed"
        records = second.service.lineage.get_by_cycle("request")
        assert records[-1].world_state_snapshot == original_world
        assert records[-1].execution_result == result["result"]
        assert records[-1].governance_decisions[0].id == proposed["decision"]["id"]


def test_failed_attempt_and_success_each_recover_audit_event(gateway, monkeypatch):
    proposed = propose(gateway, proposal(tool="notify"))
    approval = sign_approval(proposed, gateway.identity)
    append = gateway.service.lineage.append
    monkeypatch.setattr(gateway.service.lineage, "append", unavailable)
    gateway.backend.fail_before_message = "hello"
    failed = execute(gateway, "request", approval).json()
    assert failed["status"] == "failed"
    done = execute(gateway, "request", approval).json()
    assert done["status"] == "completed"
    assert done["pending_audit_events"] == 2
    monkeypatch.setattr(gateway.service.lineage, "append", append)
    assert gateway.client.get("/v1/audit").json()["complete"] is True
    records = gateway.service.lineage.get_by_cycle("request")
    assert len(records) == 3
    assert [r.execution_success for r in records[1:]] == [False, True]
    assert [r.total_attempts for r in records[1:]] == [1, 2]
    assert gateway.backend.notes() == 1


@pytest.mark.parametrize("deadline", ["evidence", "approval", "decision"])
def test_expiry_between_actions_prevents_later_effect(gateway, monkeypatch, deadline):
    attestation = gateway.service.world.entities["demo"].properties[EVIDENCE_ATTESTATION_PROPERTY]
    issued = datetime.fromisoformat(attestation["issued_at"])
    clock = [issued + timedelta(seconds=299 if deadline == "evidence" else 1)]
    for module in ("gateway.service", "gateway.approval", "governance.kernel", "execution.fabric",
                   "world_model.attestation", "verification.execution_ledger", "verification.oob_ledger"):
        monkeypatch.setattr(f"gap_kernel.{module}.utcnow", lambda: clock[0])
    body = proposal(tool="lookup")
    body["actions"].append({"tool": "notify", "target": "demo", "arguments": {"message": "later"}})
    proposed = propose(gateway, body)
    approval = sign_approval(proposed, gateway.identity)
    if deadline == "approval":
        clock[0] = datetime.fromisoformat(approval["human_approval_valid_until"]) - timedelta(seconds=1)
    elif deadline == "decision":
        clock[0] = datetime.fromisoformat(proposed["decision"]["expires_at"]) - timedelta(seconds=1)
        approval = sign_approval(proposed, gateway.identity)

    def advance_after_lookup(request, payload, response):
        if request.url.path == "/records/demo":
            clock[0] += timedelta(seconds=2)

    gateway.backend.after_response = advance_after_lookup
    result = execute(gateway, "request", approval)
    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    assert len(result.json()["result"]["actions_completed"]) == 1
    assert result.json()["result"]["actions_failed"][0]["failure_stage"] == "before_dispatch"
    assert gateway.backend.notes() == 0
    assert len(gateway.backend.calls) == 1
    assert gateway.client.get("/v1/audit").json()["complete"] is True


def test_tampered_approval_timestamp_never_enters_outcome_audit(gateway):
    proposed = propose(gateway, proposal(tool="notify"))
    approval = sign_approval(proposed, gateway.identity)
    approval["human_approval_timestamp"] = "2099-01-01T00:00:00+00:00"
    assert execute(gateway, "request", approval).status_code == 403
    assert gateway.backend.notes() == 0
    assert gateway.service.lineage.count() == 1
