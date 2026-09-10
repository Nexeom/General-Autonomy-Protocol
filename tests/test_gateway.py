"""Public gateway behavior against the real, harmless durable note sink.

The only injected capability is the HTTP transport: tool calls stay in-process
while exercising the sink's authentication, SQLite writes and idempotency.
"""

import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from gap_kernel.crypto.signing import generate_keypair
from gap_kernel.gateway.app import MAX_BODY_BYTES, create_gateway_app
from gap_kernel.gateway.approval import sign_approval
from gap_kernel.gateway.provision import provision
from gap_kernel.gateway.service import GatewayService
from gap_kernel.gateway.sink import create_sink_app
from gap_kernel.models.world import EVIDENCE_ATTESTATION_PROPERTY


@pytest.fixture
def configured(tmp_path):
    return provision(tmp_path / "config", tool_url="http://tool")


class LocalToolTransport:
    def __init__(self, client, token):
        self.client = client
        self.token = token
        self.calls = []
        self.fail_before_message = None
        self.lose_response_message = None
        self.after_response = None

    def handle(self, request):
        payload = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, payload))
        assert request.url.host == "tool", "agent input must not choose tool destinations"
        assert request.headers["Authorization"] == "Bearer " + self.token
        if payload and payload.get("message") == self.fail_before_message:
            self.fail_before_message = None
            return httpx.Response(503, json={"error": "temporarily_unavailable"}, request=request)
        response = self.client.request(
            request.method, request.url.path, content=request.content,
            headers={"Authorization": request.headers["Authorization"],
                     "Content-Type": "application/json"},
        )
        if self.after_response is not None:
            self.after_response(request, payload, response)
        if payload and payload.get("message") == self.lose_response_message:
            self.lose_response_message = None
            assert response.status_code == 200, "lose only a successfully committed response"
            raise httpx.ReadTimeout("simulated lost response after commit", request=request)
        return httpx.Response(response.status_code, content=response.content,
                              headers=response.headers, request=request)

    def notes(self):
        result = self.client.get("/records/demo", headers={"Authorization": "Bearer " + self.token})
        assert result.status_code == 200
        return result.json()["notes"]


@pytest.fixture
def backend(configured, tmp_path):
    token_file = configured / "sink" / "tool-token"
    with TestClient(create_sink_app(str(token_file), str(tmp_path / "sink.sqlite"))) as client:
        yield LocalToolTransport(client, token_file.read_text().strip())


@contextmanager
def running_gateway(configured, state_dir, backend):
    service = GatewayService(configured / "gateway" / "config.json", state_dir)
    service._http.close()
    service._http = httpx.Client(
        base_url="http://tool", transport=httpx.MockTransport(backend.handle),
        headers={"Authorization": "Bearer " + backend.token},
    )
    with TestClient(create_gateway_app(service)) as client:
        client.headers["Authorization"] = "Bearer " + (configured / "agent" / "agent-token").read_text().strip()
        yield SimpleNamespace(
            client=client, service=service, backend=backend,
            state_dir=state_dir,
            identity=configured / "operator" / "approver.json",
        )


@pytest.fixture
def gateway(configured, backend, tmp_path):
    with running_gateway(configured, tmp_path / "state", backend) as instance:
        yield instance


def proposal(request_id="request", tool="lookup", target="demo", arguments=None):
    return {"request_id": request_id, "actions": [{
        "tool": tool, "target": target,
        "arguments": arguments if arguments is not None else ({"message": "hello"} if tool == "notify" else {}),
    }]}


def propose(gateway, body):
    response = gateway.client.post("/v1/proposals", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def execute(gateway, request_id, approval=None):
    return gateway.client.post(f"/v1/requests/{request_id}/execute", json=(
        {"approval": approval} if approval is not None else {}
    ))


@pytest.mark.parametrize("tool,target", [
    ("shell", "demo"), ("update_record", "demo"),
    ("lookup", "other"), ("notify", "https://example.invalid"),
])
def test_forbidden_tool_or_target_never_dispatches(gateway, tool, target):
    response = propose(gateway, proposal(tool=tool, target=target))
    assert response["status"] == "rejected"
    assert response["reason"] == "tool_or_target_not_allowed"
    audit = gateway.client.get("/v1/audit")
    assert audit.status_code == 200
    assert audit.json()["records"] == 1
    assert audit.json()["valid"] is True
    denied = execute(gateway, "request")
    assert denied.status_code == 409
    assert denied.json()["error"] == "request_rejected"
    assert gateway.backend.calls == []


@pytest.mark.parametrize("field,value", [
    ("risk_score", 1), ("estimated_cost", 0), ("policy", {}),
    ("world_state", {}), ("decision", {"verdict": "approved"}),
    ("action_type_id", "task_execution"), ("authorization_level", "L0"),
])
def test_agent_cannot_supply_governance_fields(gateway, field, value):
    body = proposal(tool="notify")
    body[field] = value
    assert gateway.client.post("/v1/proposals", json=body).status_code == 422
    assert gateway.backend.calls == []


@pytest.mark.parametrize("field,value", [
    ("risk_score", 1), ("requires_consent", False),
    ("reversible", True), ("action_type", "query_crm"),
])
def test_agent_cannot_override_action_metadata(gateway, field, value):
    body = proposal(tool="notify")
    body["actions"][0][field] = value
    assert gateway.client.post("/v1/proposals", json=body).status_code == 422
    assert gateway.backend.calls == []


@pytest.mark.parametrize("tool,arguments", [
    ("lookup", {"url": "https://example.invalid"}),
    ("notify", {"message": "hello", "risk_score": 1}),
    ("notify", {"message": "hello", "target": "other"}),
    ("notify", {}),
])
def test_tool_arguments_are_validated_before_evaluation(gateway, tool, arguments):
    response = propose(gateway, proposal(tool=tool, arguments=arguments))
    assert response["status"] == "rejected"
    assert response["reason"] == "invalid_tool_arguments"
    assert gateway.backend.calls == []


def test_server_catalog_assigns_risk_and_batch_cost(gateway):
    lookup = propose(gateway, proposal("lookup"))
    assert lookup["proposal"]["actions"][0]["risk_score"] == 1
    assert lookup["proposal"]["estimated_cost"] == 0.01
    assert lookup["decision"]["authorization_level"] == "L0"

    notify = propose(gateway, proposal("notify", "notify"))
    assert notify["proposal"]["actions"][0]["risk_score"] == 6
    assert notify["proposal"]["actions"][0]["requires_consent"] is True
    assert notify["decision"]["authorization_level"] == "L2"

    batch = proposal("over_budget", "notify")
    batch["actions"] *= 3
    over_budget = propose(gateway, batch)
    assert over_budget["proposal"]["estimated_cost"] == 3
    assert over_budget["status"] == "rejected"
    assert "cost_ceiling" in over_budget["decision"]["violated_constraints"]
    assert gateway.backend.calls == []


def test_l0_lookup_executes_without_approval_and_is_audited(gateway):
    response = propose(gateway, proposal())
    assert response["status"] == "ready"
    completed = execute(gateway, "request")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["result"]["success"] is True
    assert gateway.backend.calls == [("GET", "/records/demo", None)]
    audit = gateway.client.get("/v1/audit")
    assert audit.status_code == 200
    assert audit.json()["valid"] is True
    assert audit.json()["records"] == 2


def test_l2_notify_waits_for_authentic_human_approval(gateway):
    response = propose(gateway, proposal(tool="notify"))
    assert response["status"] == "awaiting_approval"
    denied = execute(gateway, "request")
    assert denied.status_code == 403
    assert denied.json()["error"] == "valid_human_approval_required"
    assert gateway.backend.calls == []
    approval = sign_approval(response, gateway.identity)
    completed = execute(gateway, "request", approval)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert gateway.backend.notes() == 1
    replay = execute(gateway, "request", approval)
    assert replay.status_code == 409
    assert replay.json()["error"] == "authorization_spent"
    assert gateway.backend.notes() == 1


@pytest.mark.parametrize("tamper", ["signature", "unknown_approver", "different_decision"])
def test_tampered_or_transferred_approval_never_dispatches(gateway, tamper):
    response = propose(gateway, proposal(tool="notify"))
    approval = sign_approval(response, gateway.identity)
    if tamper == "signature":
        value = approval["human_approval_signature"]
        approval["human_approval_signature"] = ("0" if value[0] != "0" else "1") + value[1:]
    elif tamper == "unknown_approver":
        approval["human_approver_public_key_id"] = "unregistered"
    else:
        other = propose(gateway, proposal("other_request", "notify"))
        approval = sign_approval(other, gateway.identity)
    denied = execute(gateway, "request", approval)
    assert denied.status_code == 403
    assert gateway.backend.calls == []


def test_wrong_private_key_cannot_authorize_registered_approver(gateway, tmp_path):
    response = propose(gateway, proposal(tool="notify"))
    identity = json.loads(gateway.identity.read_text())
    identity["private_key_hex"], _ = generate_keypair()
    wrong_identity = tmp_path / "wrong_approver.json"
    wrong_identity.write_text(json.dumps(identity))
    approval = sign_approval(response, wrong_identity)
    assert execute(gateway, "request", approval).status_code == 403
    assert gateway.backend.calls == []


@pytest.mark.parametrize("part", ["proposal", "decision"])
def test_operator_signer_rejects_unauthentic_review_material(gateway, part):
    response = deepcopy(propose(gateway, proposal(tool="notify")))
    if part == "proposal":
        response[part]["actions"][0]["parameters"]["message"] = "substituted"
    else:
        response[part]["proposal_digest"] = "0" * 64
    with pytest.raises(ValueError, match="authentic"):
        sign_approval(response, gateway.identity)


def test_request_id_is_content_bound_and_retries_reuse_one_decision(gateway):
    body = proposal(tool="notify")
    original = propose(gateway, body)
    assert propose(gateway, body) == original
    changed = deepcopy(body)
    changed["actions"][0]["arguments"]["message"] = "different message"
    denied = gateway.client.post("/v1/proposals", json=changed)
    assert denied.status_code == 409
    assert denied.json()["error"] == "request_id_content_mismatch"
    assert gateway.service.lineage.count() == 1


def test_completed_authorization_stays_spent_after_restart(configured, backend, tmp_path):
    state = tmp_path / "state"
    with running_gateway(configured, state, backend) as first:
        response = propose(first, proposal(tool="notify"))
        approval = sign_approval(response, first.identity)
        assert execute(first, "request", approval).json()["status"] == "completed"
    with running_gateway(configured, state, backend) as second:
        assert second.client.get("/v1/requests/request").json()["status"] == "completed"
        denied = execute(second, "request", approval)
        assert denied.status_code == 409
        assert denied.json()["error"] == "authorization_spent"
        assert second.client.get("/v1/audit").json()["valid"] is True
    assert backend.notes() == 1


def test_partial_failure_resumes_without_repeating_completed_action(gateway):
    body = proposal(tool="notify", arguments={"message": "first"})
    body["actions"].append({"tool": "notify", "target": "demo", "arguments": {"message": "second"}})
    response = propose(gateway, body)
    approval = sign_approval(response, gateway.identity)
    gateway.backend.fail_before_message = "second"
    failed = execute(gateway, "request", approval)
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert gateway.backend.notes() == 1
    completed = execute(gateway, "request", approval)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert gateway.backend.notes() == 2
    messages = [payload["message"] for method, path, payload in gateway.backend.calls if path == "/notify"]
    assert messages == ["first", "second", "second"]
    assert completed.json()["result"]["actions_completed"][0]["skipped"] is True


def test_lost_response_and_restart_reuse_sink_idempotency_key(configured, backend, tmp_path):
    state = tmp_path / "state"
    with running_gateway(configured, state, backend) as first:
        response = propose(first, proposal(tool="notify"))
        approval = sign_approval(response, first.identity)
        backend.lose_response_message = "hello"
        assert execute(first, "request", approval).json()["status"] == "failed"
        assert backend.notes() == 1
    with running_gateway(configured, state, backend) as second:
        completed = execute(second, "request", approval)
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert backend.notes() == 1
    keys = [payload["idempotency_key"] for method, path, payload in backend.calls if path == "/notify"]
    assert len(keys) == 2
    assert keys[0] == keys[1]


def test_expired_evidence_is_rechecked_immediately_before_dispatch(gateway, monkeypatch):
    response = propose(gateway, proposal(tool="notify"))
    approval = sign_approval(response, gateway.identity)
    attestation = gateway.service.world.entities["demo"].properties[EVIDENCE_ATTESTATION_PROPERTY]
    expired = datetime.fromisoformat(attestation["expires_at"]) + timedelta(seconds=1)
    monkeypatch.setattr("gap_kernel.world_model.attestation.utcnow", lambda: expired)
    denied = execute(gateway, "request", approval)
    assert denied.status_code == 409
    assert denied.json()["error"] == "policy_or_evidence_changed_repropose"
    assert gateway.backend.calls == []


def test_expired_evidence_rejects_new_proposals(gateway, monkeypatch):
    attestation = gateway.service.world.entities["demo"].properties[EVIDENCE_ATTESTATION_PROPERTY]
    expired = datetime.fromisoformat(attestation["expires_at"]) + timedelta(seconds=1)
    monkeypatch.setattr("gap_kernel.world_model.attestation.utcnow", lambda: expired)
    denied = propose(gateway, proposal(tool="notify"))
    assert denied["status"] == "rejected"
    assert "gdpr_consent_required" in denied["decision"]["violated_constraints"]
    assert gateway.backend.calls == []


def test_expired_stored_decision_cannot_execute(gateway, monkeypatch):
    response = propose(gateway, proposal())
    expired = datetime.fromisoformat(response["decision"]["expires_at"]) + timedelta(seconds=1)
    monkeypatch.setattr("gap_kernel.execution.fabric.utcnow", lambda: expired)
    denied = execute(gateway, "request")
    assert denied.status_code == 409
    assert denied.json()["error"] == "authorization_invalid_or_spent"
    assert gateway.backend.calls == []


def test_config_change_invalidates_persisted_authorization(configured, backend, tmp_path):
    state = tmp_path / "state"
    with running_gateway(configured, state, backend) as first:
        response = propose(first, proposal(tool="notify"))
        approval = sign_approval(response, first.identity)
    config_file = configured / "gateway" / "config.json"
    config = json.loads(config_file.read_text())
    config["tools"]["notify"]["cost"] = 0.5
    config_file.write_text(json.dumps(config))
    with running_gateway(configured, state, backend) as second:
        denied = execute(second, "request", approval)
        assert denied.status_code == 409
        assert denied.json()["error"] == "deployment_changed_repropose"
    assert backend.calls == []


def test_authentication_applies_to_proposals_execution_status_and_audit(gateway):
    for method, path, body in [
        ("POST", "/v1/proposals", proposal()),
        ("POST", "/v1/requests/request/execute", {}),
        ("GET", "/v1/requests/request", None),
        ("GET", "/v1/audit", None),
    ]:
        response = gateway.client.request(method, path, json=body,
                                          headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401
    assert gateway.client.get("/health", headers={"Authorization": ""}).status_code == 200
    assert gateway.backend.calls == []


def test_oversized_body_is_refused_even_with_false_content_length(gateway):
    body = json.dumps({**proposal(), "padding": "x" * MAX_BODY_BYTES})
    response = gateway.client.post("/v1/proposals", content=body, headers={
        "Content-Type": "application/json", "Content-Length": "1",
    })
    assert response.status_code == 413
    assert response.json()["error"] == "request_too_large"
    assert gateway.service.lineage.count() == 0
    assert gateway.backend.calls == []


def test_execute_route_does_not_accept_a_caller_supplied_decision(gateway):
    response = propose(gateway, proposal())
    denied = gateway.client.post("/v1/requests/request/execute", json={"decision": response["decision"]})
    assert denied.status_code == 422
    assert gateway.backend.calls == []


def test_operator_halt_blocks_proposal_and_execution_until_marker_removed(gateway):
    propose(gateway, proposal("before_halt"))
    marker = gateway.state_dir / "halted"
    marker.write_text("operator halt")
    denied = propose(gateway, proposal("during_halt"))
    assert denied["status"] == "rejected"
    assert denied["reason"] == "gateway_halted"
    blocked = execute(gateway, "before_halt")
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "gateway_halted"
    assert gateway.client.post("/v1/halt", json={"engaged": False}).status_code == 404
    assert marker.exists()
    assert gateway.backend.calls == []
    assert gateway.client.get("/v1/audit").json()["valid"] is True

    # Only the operator filesystem step clears this deployment's marker.
    marker.unlink()
    assert execute(gateway, "before_halt").json()["status"] == "completed"
    assert gateway.backend.calls == [("GET", "/records/demo", None)]


def test_halt_is_rechecked_between_actions_and_resume_skips_completed_effect(gateway):
    body = proposal(tool="notify", arguments={"message": "first"})
    body["actions"].append({"tool": "notify", "target": "demo", "arguments": {"message": "second"}})
    response = propose(gateway, body)
    approval = sign_approval(response, gateway.identity)
    marker = gateway.state_dir / "halted"

    def engage_after_first(request, payload, response):
        if payload and payload.get("message") == "first" and response.status_code == 200:
            marker.write_text("operator halt after first committed action")

    gateway.backend.after_response = engage_after_first
    interrupted = execute(gateway, "request", approval)
    assert interrupted.status_code == 200
    assert interrupted.json()["status"] == "failed"
    assert interrupted.json()["result"]["actions_failed"][0]["error"] == "gateway_halted"
    assert gateway.backend.notes() == 1
    assert len(gateway.backend.calls) == 1
    assert execute(gateway, "request", approval).status_code == 403

    marker.unlink()
    completed = execute(gateway, "request", approval)
    assert completed.json()["status"] == "completed"
    assert completed.json()["result"]["actions_completed"][0]["skipped"] is True
    assert gateway.backend.notes() == 2
    assert [payload["message"] for _, path, payload in gateway.backend.calls if path == "/notify"] == ["first", "second"]


def test_canceled_operator_prompt_never_signs_or_writes_approval(gateway, tmp_path, monkeypatch, capsys):
    from gap_kernel.gateway import approval as approval_cli

    response = propose(gateway, proposal(tool="notify"))
    request_file = tmp_path / "review.json"
    request_file.write_text(json.dumps(response))
    output_file = tmp_path / "approval.json"
    calls = []

    def signing_must_not_run(*args, **kwargs):
        calls.append("signed")
        raise AssertionError("canceled requests must never reach signing")

    monkeypatch.setattr(approval_cli, "sign_approval", signing_must_not_run)
    monkeypatch.setattr("builtins.input", lambda prompt: "CANCEL")
    monkeypatch.setattr("sys.argv", [
        "gap-approval", str(request_file), "--identity", str(gateway.identity),
        "--output", str(output_file),
    ])
    with pytest.raises(SystemExit, match="No approval issued"):
        approval_cli.main()
    assert calls == []
    assert not output_file.exists()
    assert '"message": "hello"' in capsys.readouterr().out


def test_tampered_signed_profile_fails_startup(configured, tmp_path):
    from gap_kernel.governance.profile import ProfileVerificationError

    profile_file = configured / "gateway" / "profile.json"
    profile = json.loads(profile_file.read_text())
    profile["tier1_constraints"] = []
    profile_file.write_text(json.dumps(profile))
    with pytest.raises(ProfileVerificationError):
        GatewayService(configured / "gateway" / "config.json", tmp_path / "state")


def test_sink_requires_its_own_credential_and_binds_idempotency_content(backend):
    body = {"idempotency_key": "a" * 64, "target": "demo", "message": "one"}
    assert backend.client.post("/notify", json=body).status_code == 401
    auth = {"Authorization": "Bearer " + backend.token}
    assert backend.client.post("/notify", json=body, headers=auth).status_code == 200
    assert backend.client.post("/notify", json=body, headers=auth).status_code == 200
    body["message"] = "different"
    assert backend.client.post("/notify", json=body, headers=auth).status_code == 409
    assert backend.notes() == 1
