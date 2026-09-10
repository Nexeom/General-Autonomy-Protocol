"""LangGraph client for a separately deployed GAP execution gateway.

The planner proposes JSON actions. Only the gateway owns executors, tool
credentials, approval verification, and the authority to dispatch those actions.
Install ``gap-kernel[langgraph]`` to use this optional integration.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from langgraph.graph import END, START, StateGraph


class GatewayAction(TypedDict):
    tool: str
    target: str
    arguments: dict[str, Any]


class AgentInput(TypedDict):
    objective: str


class AgentState(AgentInput, total=False):
    attempts: int
    status: Literal[
        "planning", "proposed", "ready", "rejected", "awaiting_approval",
        "completed", "failed", "abstained",
    ]
    completed: bool
    actions: list[GatewayAction]
    request_id: str | None
    decision: dict[str, Any]
    result: dict[str, Any] | None
    reason: str
    rejections: list[dict[str, Any]]


Planner = Callable[[AgentState], Sequence[GatewayAction] | None]


class GatewayError(RuntimeError):
    """A gateway request failed; a mutation's outcome can be uncertain."""

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


def _request_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("request_id must contain 1-64 letters, digits, hyphens or underscores")
    return value


def _actions(value: Sequence[GatewayAction]) -> list[GatewayAction]:
    """Validate the transport shape, without making local authorization decisions."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("planner must return a sequence of JSON actions or None")
    for action in value:
        if not isinstance(action, dict) or set(action) != {"tool", "target", "arguments"}:
            raise ValueError("each action requires exactly tool, target, and arguments")
        if not all(isinstance(action[key], str) and action[key] for key in ("tool", "target")):
            raise ValueError("tool and target must be nonempty strings")
        if not isinstance(action["arguments"], dict):
            raise ValueError("action arguments must be a JSON object")
    # Freeze the proposal as ordinary JSON data before crossing the HTTP boundary.
    return json.loads(json.dumps(list(value), allow_nan=False))


class GatewayClient:
    """Authenticated HTTP client; it never loads a GAP kernel or local tools.

    HTTPS is required except for loopback development or an explicitly configured
    private-network demonstration (``allow_insecure_http=True``). HTTP mutations are not
    retried automatically: after a timeout, inspect ``get_request(request_id)``
    before deciding whether to retry the same request. A bearer token authorizes
    gateway access; it is neither a tool credential nor a human approval.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        allow_insecure_http: bool = False,
    ) -> None:
        parsed = urlsplit(base_url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.scheme not in {"https", "http"}
            or (parsed.scheme == "http" and not loopback and not allow_insecure_http)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("gateway URL must be an HTTPS origin (HTTP is allowed on loopback)")
        if not token or not token.strip():
            raise ValueError("a gateway bearer token is required")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _call(
        self, method: str, path: str, request_id: str, payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GatewayError(
                f"gateway returned HTTP {exc.response.status_code}; inspect the stored request",
                request_id=request_id,
            ) from exc
        except httpx.RequestError as exc:
            raise GatewayError(
                "gateway outcome is unknown; inspect the stored request before retrying",
                request_id=request_id,
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise GatewayError("gateway returned invalid JSON", request_id=request_id) from exc
        if not isinstance(data, dict) or data.get("request_id") != request_id:
            raise GatewayError("gateway returned a mismatched request", request_id=request_id)
        return data

    def propose(
        self, actions: Sequence[GatewayAction], *, request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = _request_id(request_id or str(uuid4()))
        proposal = _actions(actions)
        if not proposal:
            raise ValueError("at least one action is required")
        data = self._call("POST", "/v1/proposals", request_id, {
            "request_id": request_id, "actions": proposal,
        })
        if data.get("status") not in {"ready", "rejected", "awaiting_approval"}:
            raise GatewayError("gateway returned an invalid proposal status", request_id=request_id)
        if data["status"] != "rejected" and not isinstance(data.get("decision"), dict):
            raise GatewayError("gateway omitted its decision", request_id=request_id)
        return data

    def execute(
        self, request_id: str, *, approval: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the stored proposal; caller-supplied actions are never accepted.

        Approval, when required, must come from an authorized human signing
        workflow. This client only transports it; the gateway verifies it.
        """
        request_id = _request_id(request_id)
        payload = {"approval": dict(approval)} if approval is not None else {}
        data = self._call("POST", f"/v1/requests/{request_id}/execute", request_id, payload)
        if data.get("status") not in {"completed", "failed"}:
            raise GatewayError("gateway returned an invalid execution status", request_id=request_id)
        if data["status"] == "completed" and not isinstance(data.get("result"), dict):
            raise GatewayError("gateway omitted its execution result", request_id=request_id)
        return data

    def get_request(self, request_id: str) -> dict[str, Any]:
        """Read the canonical stored proposal, decision, and current outcome."""
        request_id = _request_id(request_id)
        return self._call("GET", f"/v1/requests/{request_id}", request_id)


def build_governed_graph(planner: Planner, gateway: GatewayClient, *, max_attempts: int = 3):
    """Compile a real LangGraph workflow around a remote enforcement boundary.

    Call ``graph.invoke({"objective": "..."})``. The planner receives a deep
    copy of state and returns actions, or ``None``/``[]`` to abstain. Only a
    governance rejection triggers replanning, up to ``max_attempts`` proposals.
    Pending human approval ends this run with the canonical request ID. Failed
    or uncertain execution ends the run without replanning or marking success.
    This adapter does not claim that a completed tool call fulfilled the user's
    broader objective; ``completed`` refers only to the gateway action batch.
    """
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")

    def initialize(state: AgentInput) -> AgentState:
        objective = state.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective must be a nonempty string")
        return {
            "objective": objective, "attempts": 0, "status": "planning", "completed": False,
            "actions": [], "request_id": None, "decision": {}, "result": None,
            "reason": "", "rejections": [],
        }

    def plan(state: AgentState) -> dict[str, Any]:
        try:
            proposed = planner(deepcopy(state))
            actions = [] if proposed is None else _actions(proposed)
        except Exception:
            # A planner can contain arbitrary model/provider code. It is not a
            # trusted source of completion, approval, or error-message contents.
            return {"status": "failed", "reason": "planner failed to produce valid JSON actions"}
        if not actions:
            return {"status": "abstained", "actions": [], "reason": "planner proposed no action"}
        return {
            "actions": actions, "attempts": state["attempts"] + 1,
            "status": "proposed", "request_id": str(uuid4()), "reason": "",
        }

    def propose(state: AgentState) -> dict[str, Any]:
        try:
            record = gateway.propose(state["actions"], request_id=state["request_id"])
        except GatewayError as exc:
            return {"status": "failed", "reason": str(exc), "request_id": exc.request_id}
        update = {
            "status": record["status"], "decision": record.get("decision") or {},
            "reason": record.get("reason") or "", "request_id": record["request_id"],
        }
        if record["status"] == "rejected":
            update["rejections"] = state["rejections"] + [deepcopy(record)]
        return update

    def execute(state: AgentState) -> dict[str, Any]:
        request_id = state["request_id"]
        if request_id is None:
            return {"status": "failed", "completed": False, "reason": "missing gateway request ID"}
        try:
            record = gateway.execute(request_id)
        except GatewayError as exc:
            return {"status": "failed", "completed": False, "reason": str(exc)}
        return {
            "status": record["status"], "completed": record["status"] == "completed",
            "result": record.get("result"), "reason": record.get("reason") or "",
        }

    def route_proposal(state: AgentState) -> str:
        if state["status"] == "ready":
            return "execute"
        if state["status"] == "rejected" and state["attempts"] < max_attempts:
            return "plan"
        return END

    builder = StateGraph(AgentState, input_schema=AgentInput)
    builder.add_node("initialize", initialize)
    builder.add_node("plan", plan)
    builder.add_node("propose", propose)
    builder.add_node("execute", execute)
    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "plan")
    builder.add_conditional_edges(
        "plan", lambda state: "propose" if state["status"] == "proposed" else END,
        {"propose": "propose", END: END},
    )
    builder.add_conditional_edges(
        "propose", route_proposal, {"execute": "execute", "plan": "plan", END: END},
    )
    builder.add_edge("execute", END)
    return builder.compile().with_config({"recursion_limit": 2 * max_attempts + 5})
