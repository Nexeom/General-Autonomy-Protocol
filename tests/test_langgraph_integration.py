"""Run real compiled LangGraph nodes against an injected HTTP transport.

These tests exercise graph routing and the wire contract. They do not replace
the separate gateway's authorization, process-isolation, or executor tests.
"""

import json

import httpx
import pytest

pytest.importorskip("langgraph")

from gap_kernel.integrations.langgraph import (  # noqa: E402
    GatewayClient,
    GatewayError,
    build_governed_graph,
)


ACTION = {"tool": "lookup", "target": "demo", "arguments": {}}


class GatewayTransport:
    def __init__(self, statuses=("ready",), *, execution_status="completed"):
        self.statuses = iter(statuses)
        self.execution_status = execution_status
        self.requests = []
        self.proposals = {}

    def __call__(self, request):
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer client-token"
        if request.url.path == "/v1/proposals":
            body = json.loads(request.content)
            request_id = body["request_id"]
            record = {
                "request_id": request_id, "status": next(self.statuses),
                "decision": {"decision_id": "signed-at-gateway"},
                "reason": "target is outside the permitted scope",
                "proposal": body,
            }
            self.proposals[request_id] = record
            return httpx.Response(200, json=record)
        request_id = request.url.path.split("/")[3]
        if request.method == "GET":
            return httpx.Response(200, json=self.proposals[request_id])
        assert request.url.path.endswith("/execute")
        assert request_id in self.proposals
        return httpx.Response(200, json={
            "request_id": request_id, "status": self.execution_status,
            "result": {"lookup": "demo record", "success": self.execution_status == "completed"},
        })


def client(transport):
    return GatewayClient(
        "https://gateway.example", "client-token", transport=httpx.MockTransport(transport),
    )


def test_real_graph_executes_only_after_remote_authorization():
    transport = GatewayTransport()
    with client(transport) as gateway:
        graph = build_governed_graph(lambda state: [ACTION], gateway)
        state = graph.invoke({"objective": "Read demo"})
    assert {"plan", "propose", "execute"}.issubset(graph.get_graph().nodes)
    assert state["status"] == "completed"
    assert state["completed"] is True
    assert state["attempts"] == 1
    assert len(transport.requests) == 2
    assert json.loads(transport.requests[1].content) == {}
    assert transport.requests[1].url.path == f"/v1/requests/{state['request_id']}/execute"


def test_rejection_passes_feedback_to_planner_then_reauthorizes_new_proposal():
    transport = GatewayTransport(("rejected", "ready"))
    seen = []

    def planner(state):
        seen.append(state)
        return [dict(ACTION, target="unapproved" if not state["rejections"] else "demo")]

    with client(transport) as gateway:
        state = build_governed_graph(planner, gateway).invoke({"objective": "Read demo"})
    assert state["status"] == "completed"
    assert state["attempts"] == 2
    assert seen[1]["rejections"][0]["reason"] == "target is outside the permitted scope"
    assert len(transport.proposals) == 2  # New canonical request for the new plan.
    assert len(transport.requests) == 3  # Rejected proposal was never executed.
    assert json.loads(transport.requests[1].content)["actions"][0]["target"] == "demo"


def test_replanning_is_bounded_even_above_langgraph_default_recursion_limit():
    transport = GatewayTransport(["rejected"] * 15)
    with client(transport) as gateway:
        state = build_governed_graph(
            lambda state: [ACTION], gateway, max_attempts=15,
        ).invoke({"objective": "Read demo"})
    assert state["status"] == "rejected"
    assert state["completed"] is False
    assert state["attempts"] == 15
    assert len(transport.requests) == 15


def test_pending_human_approval_ends_run_without_dispatch_or_replanning():
    transport = GatewayTransport(("awaiting_approval",))
    with client(transport) as gateway:
        state = build_governed_graph(lambda state: [ACTION], gateway).invoke({
            "objective": "Notify demo",
        })
    assert state["status"] == "awaiting_approval"
    assert state["request_id"] in transport.proposals
    assert state["completed"] is False
    assert state["result"] is None
    assert state["attempts"] == 1
    assert len(transport.requests) == 1


def test_failed_execution_never_marks_completed_or_replans():
    transport = GatewayTransport(execution_status="failed")
    with client(transport) as gateway:
        state = build_governed_graph(lambda state: [ACTION], gateway).invoke({
            "objective": "Read demo",
        })
    assert state["status"] == "failed"
    assert state["completed"] is False
    assert len(transport.requests) == 2


def test_execute_timeout_preserves_request_id_and_does_not_retry_uncertain_side_effect():
    transport = GatewayTransport()

    def interrupted(request):
        if request.url.path.endswith("/execute"):
            transport.requests.append(request)
            raise httpx.ReadTimeout("response lost after dispatch", request=request)
        return transport(request)

    with client(interrupted) as gateway:
        state = build_governed_graph(lambda state: [ACTION], gateway).invoke({
            "objective": "Read demo",
        })
    assert state["status"] == "failed"
    assert state["completed"] is False
    assert state["request_id"] in transport.proposals
    assert "unknown" in state["reason"]
    assert len(transport.requests) == 2


@pytest.mark.parametrize("response", [
    {"status": "completed", "result": {}},
    {"status": "ready"},
    {"status": "unrecognized", "decision": {}},
])
def test_malformed_authorization_response_cannot_reach_execution(response):
    requests = []

    def bad_gateway(request):
        requests.append(request)
        return httpx.Response(200, json={
            "request_id": json.loads(request.content)["request_id"], **response,
        })

    with client(bad_gateway) as gateway:
        state = build_governed_graph(lambda state: [ACTION], gateway).invoke({
            "objective": "Read demo",
        })
    assert state["status"] == "failed"
    assert state["completed"] is False
    assert len(requests) == 1


def test_mismatched_gateway_request_id_fails_closed():
    with client(lambda request: httpx.Response(200, json={
        "request_id": "different-request", "status": "ready", "decision": {},
    })) as gateway:
        state = build_governed_graph(lambda state: [ACTION], gateway).invoke({
            "objective": "Read demo",
        })
    assert state["status"] == "failed"
    assert "mismatched" in state["reason"]


@pytest.mark.parametrize("answer, status", [
    (None, "abstained"), ([], "abstained"),
    ("call lookup", "failed"), ([{"tool": "lookup"}], "failed"),
    ([dict(ACTION, arguments={"value": float("nan")})], "failed"),
])
def test_invalid_or_empty_plan_never_sends_http_request(answer, status):
    transport = GatewayTransport()
    with client(transport) as gateway:
        state = build_governed_graph(lambda state: answer, gateway).invoke({
            "objective": "Read demo",
        })
    assert state["status"] == status
    assert state["completed"] is False
    assert not transport.requests


def test_planner_cannot_mutate_graph_state_to_forge_completion():
    transport = GatewayTransport(("rejected",))

    def planner(state):
        state["completed"] = True
        state["status"] = "completed"
        state["rejections"].append({"reason": "fabricated"})
        return [ACTION]

    with client(transport) as gateway:
        state = build_governed_graph(planner, gateway, max_attempts=1).invoke({
            "objective": "Read demo", "completed": True, "status": "completed",
        })
    assert state["status"] == "rejected"
    assert state["completed"] is False
    assert len(state["rejections"]) == 1
    assert state["rejections"][0]["reason"] != "fabricated"


def test_separate_approval_transport_executes_only_canonical_stored_request():
    transport = GatewayTransport(("awaiting_approval",))
    approval = {
        "human_approval_signature": "signature-from-independent-human-workflow",
        "human_approver_public_key_id": "human-1",
        "human_approval_timestamp": "2026-09-10T15:00:00Z",
        "human_approval_valid_until": "2026-09-10T15:05:00Z",
    }
    with client(transport) as gateway:
        record = gateway.propose([ACTION], request_id="chosen-request")
        stored = gateway.get_request(record["request_id"])
        result = gateway.execute(stored["request_id"], approval=approval)
    assert stored["proposal"]["actions"] == [ACTION]
    assert result["status"] == "completed"
    assert json.loads(transport.requests[-1].content) == {"approval": approval}


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not JSON"),
    httpx.Response(401, json={"detail": "Unauthorized"}),
    httpx.Response(307, headers={"location": "https://other.example"}),
])
def test_transport_errors_and_redirects_do_not_authorize_execution(response):
    with client(lambda request: response) as gateway:
        with pytest.raises(GatewayError):
            gateway.propose([ACTION])


@pytest.mark.parametrize("url", [
    "http://external.example", "https://user:secret@gateway.example",
    "https://gateway.example/path", "https://gateway.example?key=secret", "file:///tmp/socket",
])
def test_bearer_token_requires_secure_gateway_origin(url):
    with pytest.raises(ValueError, match="HTTPS origin"):
        GatewayClient(url, "client-token")


def test_private_container_http_requires_explicit_configuration():
    transport = GatewayTransport()
    with GatewayClient(
        "http://gateway:8090", "client-token", allow_insecure_http=True,
        transport=httpx.MockTransport(transport),
    ) as gateway:
        assert gateway.propose([ACTION])["status"] == "ready"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_attempt_limit_rejected(limit):
    with client(GatewayTransport()) as gateway:
        with pytest.raises(ValueError, match="positive integer"):
            build_governed_graph(lambda state: [ACTION], gateway, max_attempts=limit)
