"""Run real local HTTP dispatch with generated identities and a scripted approver.

No external account, model API, email, or paid service is used. For an actual
human confirmation use gap_kernel.gateway.approval against the Docker setup.
"""
import json

from demo_support import LocalDeployment, propose
from gap_kernel.gateway.approval import sign_approval
from gap_kernel.integrations.langgraph import GatewayClient, build_governed_graph


def run_demo():
    checks = {}
    with LocalDeployment() as demo:
        rejected = propose(demo.http, tool="delete_all")
        checks["unknown_tool_rejected"] = rejected["status"] == "rejected"
        lookup = propose(demo.http)
        response = demo.http.post(f"/v1/requests/{lookup['request_id']}/execute", json={})
        checks["autonomous_lookup_completed"] = response.json()["status"] == "completed"

        notification = propose(demo.http, tool="notify", arguments={"message": "A governed local note"})
        route = f"/v1/requests/{notification['request_id']}/execute"
        checks["human_gate_required"] = notification["status"] == "awaiting_approval"
        checks["missing_approval_rejected"] = demo.http.post(route, json={}).status_code == 403
        approval = sign_approval(notification, demo.approver)
        tampered = {**approval, "human_approval_signature": "0" * 128}
        checks["tampered_approval_rejected"] = demo.http.post(
            route, json={"approval": tampered}).status_code == 403
        changed = {"request_id": notification["request_id"], "actions": [
            {"tool": "notify", "target": "demo", "arguments": {"message": "changed"}}]}
        checks["proposal_tamper_rejected"] = demo.http.post(
            "/v1/proposals", json=changed).status_code == 409
        response = demo.http.post(route, json={"approval": approval})
        checks["signed_approval_dispatch_completed"] = response.json()["status"] == "completed"
        checks["replay_rejected"] = demo.http.post(route, json={"approval": approval}).status_code == 409

        def planner(state):
            # A deterministic fixture using the real framework, not an LLM
            # capability benchmark. A model-backed callable uses this same API.
            return [{"tool": "lookup", "target": "demo", "arguments": {}}]

        with GatewayClient(demo.url, demo.token) as gateway:
            graph = build_governed_graph(planner, gateway)
            result = graph.invoke({"objective": "Read the demonstration record"})
        checks["real_langgraph_completed"] = result["status"] == "completed"
        audit = demo.http.get("/v1/audit").json()
        checks["signed_lineage_valid"] = audit["valid"] and audit["records"] >= 5
    output = {"mode": "local functional demo; same OS user", "approver": "scripted test fixture",
              "checks": checks, "passed": all(checks.values())}
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    raise SystemExit(0 if run_demo()["passed"] else 1)
