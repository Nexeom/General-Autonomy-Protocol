"""Run a LangGraph planner through the remote GAP gateway.

Install: pip install -e ".[langgraph]"
Configure GAP_GATEWAY_URL and GAP_GATEWAY_TOKEN with client access only.
Run: python examples/langgraph_agent.py --fixture lookup
Or:  python examples/langgraph_agent.py --planner my_planner:plan --objective "..."

The supplied fixture planner is deterministic test data, not a model evaluation.
A real model integration implements ``plan(state) -> list[GatewayAction] | None``.
It receives the objective and prior governance rejection records. Its returned
actions still cross the remote gateway's authorization and execution boundary.
Provider credentials, if needed by a real planner, remain that planner's concern;
tool credentials and human approval signing keys do not belong in this process.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os

from gap_kernel.integrations.langgraph import (
    AgentState,
    GatewayAction,
    GatewayClient,
    Planner,
    build_governed_graph,
)


def fixture_planner(scenario: str) -> Planner:
    """Return deterministic proposals to exercise transport and graph routing."""
    def plan(state: AgentState) -> list[GatewayAction]:
        if scenario == "approval":
            return [{"tool": "notify", "target": "demo", "arguments": {
                "message": "Demonstration notification: requires independent human approval.",
            }}]
        if scenario == "replan" and not state["rejections"]:
            # The gateway rejects this target. The next proposal is independently
            # evaluated; the client cannot grant itself access to either target.
            return [{"tool": "lookup", "target": "unapproved-target", "arguments": {}}]
        return [{"tool": "lookup", "target": "demo", "arguments": {}}]

    return plan


def load_planner(spec: str) -> Planner:
    """Load a user-selected callable, such as a structured-output LLM adapter."""
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("planner must be written as module_name:callable_name")
    planner = getattr(importlib.import_module(module_name), attribute)
    if not callable(planner):
        raise ValueError("selected planner must be callable")
    return planner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--fixture", choices=("lookup", "replan", "approval"), default="lookup")
    source.add_argument("--planner", help="your model-backed planner as module_name:callable_name")
    parser.add_argument("--objective", default="Read the permitted demo record.")
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    url = os.environ.get("GAP_GATEWAY_URL", "http://127.0.0.1:8090")
    token = os.environ.get("GAP_GATEWAY_TOKEN")
    if not token:
        parser.error("set GAP_GATEWAY_TOKEN to the client-only gateway bearer token")
    planner = load_planner(args.planner) if args.planner else fixture_planner(args.fixture)
    with GatewayClient(url, token) as gateway:
        graph = build_governed_graph(planner, gateway, max_attempts=args.max_attempts)
        state = graph.invoke({"objective": args.objective})
    print(json.dumps(state, indent=2))
    if state["status"] == "awaiting_approval":
        print("Stopped for independent human approval. Review the stored request at the gateway.")
    return {"completed": 0, "failed": 1, "rejected": 2, "awaiting_approval": 3,
            "abstained": 4}[state["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
