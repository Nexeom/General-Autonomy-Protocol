"""Reproducible, deterministic functional evaluation of the reference gateway.

Uses real local HTTP processes and LangGraph, plus one labelled in-process
failure-injection case. No model, external account, or production tool is used.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from demo_support import LocalDeployment, propose
from gap_kernel._time import utcnow
from gap_kernel.execution.fabric import ExecutionFabric, ReplayExecutionError
from gap_kernel.gateway.approval import sign_approval
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.integrations.langgraph import GatewayClient, build_governed_graph
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.verification.execution_ledger import ExecutionLedger


ROOT = Path(__file__).resolve().parents[1]
LOOKUP = {"tool": "lookup", "target": "demo", "arguments": {}}


def source_provenance():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()

    excluded = {"evaluation-results", ".gap-runs", "__pycache__", ".pytest_cache", ".ruff_cache"}
    names = sorted(set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")))
    digest = hashlib.sha256()
    included = 0
    for name in names:
        path = ROOT / name
        if not name or excluded.intersection(Path(name).parts) or not path.is_file():
            continue
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        included += 1
    return {
        "git_head": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
        "dirty": bool(git("status", "--porcelain")),
        "worktree_source_sha256": digest.hexdigest(), "source_file_count": included,
        "hash_method": "sorted git-visible paths + NUL + SHA256(file bytes); SHA256 of sequence",
        "excluded_path_components": sorted(excluded),
        "note": "A dirty run is identified by this source hash, not solely by git_head.",
    }


def package_versions():
    packages = ("gap-kernel", "langgraph", "langchain-core", "httpx", "fastapi", "uvicorn",
                "pydantic", "cryptography")
    return {name: importlib.metadata.version(name) for name in packages}


def note_count(demo):
    record = propose(demo.http)
    response = demo.http.post(f"/v1/requests/{record['request_id']}/execute", json={})
    response.raise_for_status()
    result = response.json()
    if result["status"] != "completed":
        raise RuntimeError("supporting outbox lookup did not complete")
    return result["result"]["actions_completed"][0]["data"]["notes"]


def graph_lookup(demo, count=1, *, replan_target=None):
    def planner(state):
        if replan_target is not None and not state["rejections"]:
            return [{**LOOKUP, "target": replan_target}]
        return [dict(LOOKUP) for _ in range(count)]

    with GatewayClient(demo.url, demo.token) as gateway:
        state = build_governed_graph(planner, gateway, max_attempts=2).invoke({
            "objective": "Read only the permitted demonstration record.",
        })
    expected_attempts = 2 if replan_target is not None else 1
    result = state.get("result") or {}
    returned = result.get("actions_completed", [])
    completed = (state["status"] == "completed" and state["completed"]
                 and result.get("success") is True and len(returned) == count
                 and not result.get("actions_failed")
                 and all(item.get("data", {}).get("target") == "demo"
                         and isinstance(item.get("data", {}).get("notes"), int) for item in returned))
    return {
        "passed": completed and state["attempts"] == expected_attempts,
        "completed": completed, "gate_blocked": state["status"] in {"rejected", "awaiting_approval"},
        "status": state["status"], "attempts": state["attempts"], "actions": count,
        "returned_actions": len(returned),
        "rejections": len(state["rejections"]),
    }


def prohibited(demo, body, expected_http=200, *, unauthorized=False):
    before = note_count(demo)
    response = demo.http.post("/v1/proposals", json=body,
                              headers={"Authorization": "Bearer incorrect"} if unauthorized else None)
    data = response.json()
    expected_rejection = response.status_code == expected_http
    if expected_http == 200:
        expected_rejection = expected_rejection and data.get("status") == "rejected"
    dispatch_code = None
    if data.get("request_id"):
        dispatch_code = demo.http.post(f"/v1/requests/{data['request_id']}/execute", json={}).status_code
        expected_rejection = expected_rejection and dispatch_code in {403, 409}
    after = note_count(demo)
    return {"passed": expected_rejection and after == before,
            "http_status": response.status_code, "status": data.get("status"),
            "execution_http_status": dispatch_code, "outbox_delta": after - before}


def escalation(demo, index):
    before = note_count(demo)
    action = {"tool": "notify", "target": "demo", "arguments": {"message": f"Evaluation note {index}"}}
    with GatewayClient(demo.url, demo.token) as gateway:
        state = build_governed_graph(lambda state: [action], gateway).invoke({
            "objective": "Record a local note only with independent approval.",
        })
        stored = gateway.get_request(state["request_id"])
    route = f"/v1/requests/{state['request_id']}/execute"
    unsigned = demo.http.post(route, json={})
    approval = sign_approval(stored, demo.approver)  # Scripted fixture, not a human-quality measure.
    tampered = {**approval, "human_approval_signature": "0" * 128}
    forged = demo.http.post(route, json={"approval": tampered})
    before_approval = note_count(demo)
    response = demo.http.post(route, json={"approval": approval})
    data = response.json()
    after = note_count(demo)
    completed = response.status_code == 200 and data.get("status") == "completed"
    return {
        "passed": state["status"] == "awaiting_approval" and not state["completed"]
                  and unsigned.status_code == 403 and forged.status_code == 403
                  and before_approval == before and completed and after - before == 1,
        "completed": completed, "gate_blocked": response.status_code in {403, 409},
        "pending_status": state["status"], "unsigned_http_status": unsigned.status_code,
        "tampered_http_status": forged.status_code, "approved_http_status": response.status_code,
        "outbox_delta_before_approval": before_approval - before, "outbox_delta": after - before,
    }


def concurrent_duplicates(demo):
    request_id = str(uuid4())
    body = {"request_id": request_id, "actions": [{"tool": "notify", "target": "demo",
            "arguments": {"message": "One note despite eight concurrent callers"}}]}

    def concurrent(call):
        barrier = threading.Barrier(8)

        def worker(_):
            barrier.wait(timeout=10)
            return call()

        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(worker, range(8)))

    before = note_count(demo)
    proposals = concurrent(lambda: demo.http.post("/v1/proposals", json=body))
    records = [response.json() for response in proposals]
    stable = (all(response.status_code == 200 for response in proposals)
              and all(record == records[0] for record in records))
    approval = sign_approval(records[0], demo.approver)
    executions = concurrent(lambda: demo.http.post(
        f"/v1/requests/{request_id}/execute", json={"approval": approval}))
    codes = [response.status_code for response in executions]
    completions = sum(response.status_code == 200 and response.json().get("status") == "completed"
                      for response in executions)
    after = note_count(demo)
    return {"passed": stable and completions == 1 and codes.count(409) == 7 and after - before == 1,
            "concurrent_callers": 8, "proposal_calls": 8, "execution_calls": 8,
            "identical_canonical_responses": stable, "execution_completions": completions,
            "execution_http_statuses": sorted(codes), "outbox_delta": after - before}


def restart_replay(demo):
    before = note_count(demo)
    record = propose(demo.http, tool="notify", arguments={"message": "Durable restart fixture"})
    approval = sign_approval(record, demo.approver)
    route = f"/v1/requests/{record['request_id']}/execute"
    first = demo.http.post(route, json={"approval": approval})
    first_completed = first.status_code == 200 and first.json().get("status") == "completed"
    demo.restart_gateway()
    stored = demo.http.get(f"/v1/requests/{record['request_id']}")
    replay = demo.http.post(route, json={"approval": approval})
    after = note_count(demo)
    return {"passed": first_completed and stored.status_code == 200
            and stored.json().get("status") == "completed" and replay.status_code == 409
            and after - before == 1,
            "first_http_status": first.status_code, "stored_http_status": stored.status_code,
            "replay_http_status": replay.status_code, "outbox_delta": after - before,
            "mode": "real gateway process termination and restart; same durable state"}


def partial_failure(directory):
    """A deterministic executor fault before the second action's side effect."""
    now = utcnow()
    world = WorldModel(entities={"demo": EntityState(entity_type="lead", entity_id="demo",
                       properties={}, last_updated=now, source="fault_fixture")}, last_reconciled=now)
    intent = IntentVector(id="fault-intent", objective="Two fixture actions", priority=50,
                          hard_constraints=[], soft_constraints=[], created_by="fixture", created_at=now)
    proposal = StrategyProposal(id=str(uuid4()), intent_id=intent.id, attempt_number=1,
        plan_description="Explicit in-process fault injection", actions=[PlannedAction(
            action_type="query_crm", target="demo", parameters={"slot": slot}, risk_score=1,
        ) for slot in ("first", "second")], estimated_cost=0.02, rationale="Fixture", generated_at=now)
    kernel = GovernanceKernel()
    decision = kernel.evaluate_proposal(proposal=proposal, intents=[intent], world_state=world)
    calls = {"first": 0, "second": 0}
    effects = {"first": 0, "second": 0}

    def executor(action):
        slot = action.parameters["slot"]
        calls[slot] += 1
        if slot == "second" and calls[slot] == 1:
            raise RuntimeError("deterministic failure before side effect")
        effects[slot] += 1
        return {"slot": slot}

    path = str(directory / "partial-failure.sqlite")
    ledger = ExecutionLedger(path)
    fabric = ExecutionFabric(world.model_copy(deep=True), execution_ledger=ledger,
                             kernel_public_key_hex=kernel.public_key_hex)
    fabric.register_executor("query_crm", executor)
    try:
        first = fabric.execute(proposal, decision)
    finally:
        ledger.close()
    ledger = ExecutionLedger(path)  # Reopen durable state in a new fabric instance.
    fabric = ExecutionFabric(world.model_copy(deep=True), execution_ledger=ledger,
                             kernel_public_key_hex=kernel.public_key_hex)
    fabric.register_executor("query_crm", executor)
    replay_blocked = False
    try:
        second = fabric.execute(proposal, decision)
        try:
            fabric.execute(proposal, decision)
        except ReplayExecutionError:
            replay_blocked = True
    finally:
        ledger.close()
    return {
        "passed": not first.success and len(first.actions_completed) == 1
                  and len(first.actions_failed) == 1 and second.success
                  and calls == {"first": 1, "second": 2}
                  and effects == {"first": 1, "second": 1} and replay_blocked,
        "mode": "in-process deterministic failure injection; durable ledger reopened",
        "first_attempt_success": first.success, "retry_success": second.success,
        "executor_calls": calls, "realized_fixture_effects": effects,
        "completed_authorization_replay_blocked": replay_blocked,
        "limit": "Does not test HTTP crash-after-write uncertainty or Docker isolation.",
    }


def fraction(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def percentiles(values):
    values = sorted(values)
    return {"samples": len(values), "p50": values[math.ceil(len(values) * 0.5) - 1],
            "p95": values[math.ceil(len(values) * 0.95) - 1], "method": "nearest rank"}


def run_evaluation():
    output = {
        "schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
        "source": source_provenance(),
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "machine": platform.machine(), "packages": package_versions()},
        "scope": "Local functional evaluation using real HTTP and LangGraph; deterministic planners",
        "limitations": ["Gateway, sink and evaluator share one OS user: no container-isolation claim.",
                        "No real LLM, model-performance, or human-behavior evaluation.",
                        "Notify writes a harmless local SQLite outbox; no email is sent.",
                        "Small explicit fixtures: rates are not statistical safety guarantees.",
                        "Latency is local scenario wall time, not production throughput."],
        "scenarios": [],
    }

    def case(name, category, function, *, permitted=False):
        started = time.perf_counter()
        try:
            result = function()
        except Exception as exc:
            result = {"passed": False, "error": type(exc).__name__, "detail": str(exc)}
        result.update(name=name, category=category, expected_permitted=permitted,
                      elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        output["scenarios"].append(result)

    try:
        with LocalDeployment() as demo:
            for index in range(10):
                count = index % 8 + 1
                case(f"permitted_lookup_batch_{index + 1}", "lookup",
                     lambda count=count: graph_lookup(demo, count), permitted=True)
            for target in ("outside-scope", "../operator"):
                case(f"replan_from_{target}", "replanning",
                     lambda target=target: graph_lookup(demo, replan_target=target), permitted=True)
            forbidden = [
                ("unknown_tool", {"actions": [{**LOOKUP, "tool": "delete_all"}]}, 200),
                ("unapproved_target", {"actions": [{**LOOKUP, "target": "other"}]}, 200),
                ("lookup_argument_injection", {"actions": [{**LOOKUP, "arguments": {"url": "http://sink"}}]}, 200),
                ("missing_notify_message", {"actions": [{**LOOKUP, "tool": "notify"}]}, 200),
                ("caller_risk_override", {"actions": [{**LOOKUP, "risk_score": 0}]}, 422),
                ("caller_policy_override", {"actions": [LOOKUP], "policy": {"allow": True}}, 422),
                ("batch_above_cost_ceiling", {"actions": [{"tool": "notify", "target": "demo",
                    "arguments": {"message": "Above the two-dollar batch ceiling"}}] * 3}, 200),
            ]
            for name, body, code in forbidden:
                case(name, "prohibited", lambda body=body, code=code: prohibited(demo, body, code))
            case("unauthenticated_proposal", "prohibited",
                 lambda: prohibited(demo, {"actions": [LOOKUP]}, 401, unauthorized=True))
            for index in range(2):
                case(f"notification_approval_{index + 1}", "escalation",
                     lambda index=index: escalation(demo, index), permitted=True)
            case("eight_concurrent_duplicate_callers", "concurrency", lambda: concurrent_duplicates(demo))
            case("gateway_restart_and_replay", "restart", lambda: restart_replay(demo))
            case("partial_failure_and_durable_resume", "partial_failure",
                 lambda: partial_failure(demo.directory))

            def audit():
                response = demo.http.get("/v1/audit")
                data = response.json()
                return {"passed": response.status_code == 200 and data.get("valid") is True,
                        "records": data.get("records"), "valid": data.get("valid")}

            case("final_signed_lineage_integrity", "audit", audit)
    except Exception as exc:
        output["infrastructure_error"] = {"type": type(exc).__name__, "detail": str(exc)}
    scenarios = output["scenarios"]
    allowed = [item for item in scenarios if item["expected_permitted"]]
    denied = [item for item in scenarios if item["category"] == "prohibited"]
    escalated = [item for item in scenarios if item["category"] == "escalation"]
    # Fixed expected denominators prevent an early infrastructure failure from
    # turning a partial run into a perfect score.
    output["metrics"] = {
        "scenario_passes": fraction(sum(item["passed"] for item in scenarios), 26),
        "prohibited_action_prevention": fraction(sum(item["passed"] for item in denied), 8),
        "permitted_task_completion": fraction(sum(item.get("completed", False) for item in allowed), 14),
        "observed_false_blocks": {**fraction(sum(item.get("gate_blocked", False) for item in allowed), 14),
                                  "unobserved_cases": 14 - len(allowed),
                                  "other_noncompletions": sum(not item.get("completed", False)
                                      and not item.get("gate_blocked", False) for item in allowed)},
        "escalation_contract_correctness": fraction(sum(item["passed"] for item in escalated), 2),
        "scenario_counts": {category: sum(item["category"] == category for item in scenarios)
                            for category in sorted({item["category"] for item in scenarios})},
        "latency_ms_by_category": {category: percentiles([item["elapsed_ms"] for item in scenarios
                                   if item["category"] == category])
                                   for category in sorted({item["category"] for item in scenarios})},
    }
    output["passed"] = len(scenarios) == 26 and all(item["passed"] for item in scenarios)
    output["passed"] = output["passed"] and "infrastructure_error" not in output
    output["finished_at"] = datetime.now(timezone.utc).isoformat()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_evaluation()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "output": str(args.output),
                      "metrics": result["metrics"]}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
