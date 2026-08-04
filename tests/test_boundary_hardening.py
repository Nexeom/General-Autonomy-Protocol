"""Isolation-boundary hardening — the constrained API that stands between a
governed agent and the Governance Kernel (Fix 2, G-2).

The threat model is a COMPROMISED AGENT PROCESS trying to obtain a validly-signed
authorization for an action policy forbids. The boundary must therefore be
read-only for governance configuration, must not carry the agent's clock, must
correlate every response to the request that produced it, and must not hand the
untrusted side either unbounded influence over kernel memory or a view of kernel
internals.
"""

from __future__ import annotations

import inspect
import io
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from gap_kernel._time import utcnow
from gap_kernel.client.governance_client import (
    GovernanceClientError,
    InProcessGovernanceClient,
    SubprocessGovernanceClient,
    _evaluate_request,
)
from gap_kernel.models.governance import GovernanceDecision
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.service import kernel_server
from gap_kernel.service.kernel_server import GovernanceService, serve_stdio


def _proposal(pid="prop_b"):
    return StrategyProposal(
        id=pid, intent_id="i1", attempt_number=1, plan_description="p",
        actions=[PlannedAction(action_type="query_crm", target="t1", parameters={}, risk_score=1)],
        estimated_cost=0.01, rationale="r", generated_at=utcnow(),
    )


def _intent():
    return IntentVector(id="i1", objective="o", priority=50, hard_constraints=[],
                        soft_constraints=[], created_by="t", created_at=utcnow())


def _world():
    return WorldModel(entities={}, last_reconciled=utcnow())


_HOSTILE_TYPE = {
    "type_id": "exfiltrate",
    "description": "move the customer database off-site",
}


# --- the boundary is read-only for governance configuration -----------------

def test_service_has_no_registry_mutation_method():
    """An agent that reaches the channel cannot add an action type to the
    kernel's registry — the registry arrives in the signed Applicability
    Profile, and a runtime write would turn a REJECT into an APPROVE."""
    service = GovernanceService()
    response = service.handle({
        "method": "register_action_type",
        "spec": _HOSTILE_TYPE,
        "registered_by": "agent",
    })
    assert response["ok"] is False
    registered = service.handle({"method": "list_action_types"})["action_types"]
    assert "exfiltrate" not in registered


def test_neither_client_proxies_registry_mutation():
    """Both clients are interchangeable, and neither offers the mutation."""
    assert not hasattr(InProcessGovernanceClient(GovernanceService()), "register_action_type")
    with SubprocessGovernanceClient() as client:
        assert not hasattr(client, "register_action_type")


def test_subprocess_agent_cannot_mutate_the_registry_across_the_boundary():
    """Even hand-rolling the raw request onto the channel changes nothing in the
    child process; the read-only registry views still work afterwards."""
    with SubprocessGovernanceClient() as client:
        with pytest.raises(GovernanceClientError):
            client._call({
                "method": "register_action_type",
                "spec": _HOSTILE_TYPE,
                "registered_by": "agent",
            })
        assert "exfiltrate" not in client.get_registered_action_types()
        assert client.get_action_type("exfiltrate") is None


def test_unknown_method_error_does_not_echo_the_request():
    """The error for an unknown method is stable and reflects nothing the
    untrusted side supplied."""
    response = GovernanceService().handle({
        "method": "drop_all_policies",
        "note": "/home/operator/.gap/signing_key",
    })
    assert response["ok"] is False
    assert "drop_all_policies" not in response["error"]
    assert "/home/operator" not in response["error"]


# --- the boundary does not carry the agent's clock --------------------------

def test_evaluate_request_carries_no_agent_clock():
    request = _evaluate_request(_proposal(), [_intent()], _world(), None)
    assert "current_time" not in request


def test_client_evaluate_signatures_accept_no_clock():
    for client_cls in (InProcessGovernanceClient, SubprocessGovernanceClient):
        parameters = inspect.signature(client_cls.evaluate_proposal).parameters
        assert "current_time" not in parameters


def test_service_ignores_a_clock_relayed_by_the_agent():
    """A time named by the untrusted side must not decide which scheduled
    constraints are active, nor the timestamp the kernel signs."""
    service = GovernanceService()
    response = service.handle({
        "method": "evaluate",
        "proposal": _proposal().model_dump(mode="json"),
        "intents": [_intent().model_dump(mode="json")],
        "world_state": _world().model_dump(mode="json"),
        "current_time": "1999-01-01T00:00:00+00:00",
    })
    decision = GovernanceDecision.model_validate(response["decision"])
    assert decision.evaluated_at.year == utcnow().year


# --- every response is correlated to its own request ------------------------

def test_call_fails_closed_on_a_mismatched_response_id():
    """A response that does not answer this request means the stream is offset —
    the client must refuse it and refuse to keep using the channel."""
    class _Offset:
        def readline(self):
            return json.dumps({"ok": True, "public_key_hex": "00" * 32, "id": 999999}) + "\n"

    client = SubprocessGovernanceClient()
    try:
        client._proc.stdout = _Offset()
        with pytest.raises(GovernanceClientError):
            client._call({"method": "get_public_key"})
        with pytest.raises(GovernanceClientError):
            client._call({"method": "get_public_key"})
    finally:
        client.close()


def test_concurrent_evaluations_never_cross_responses():
    """Concurrent callers share one client (every FastAPI route is a sync ``def``
    run in an anyio worker thread). A crossed response would hand one caller a
    validly-signed authorization for somebody else's proposal.

    A barrier releases every worker into the channel at once, which is what makes
    an unsynchronised write/read pair cross rather than merely being able to."""
    workers, rounds = 8, 10
    barrier = threading.Barrier(workers)

    with SubprocessGovernanceClient() as client:
        def evaluate(worker):
            answers = []
            for r in range(rounds):
                barrier.wait(timeout=60)
                proposal = _proposal(f"prop_{worker}_{r}")
                try:
                    decision = client.evaluate_proposal(
                        proposal=proposal, intents=[_intent()], world_state=_world()
                    )
                    answers.append((proposal.id, decision.proposal_id))
                except Exception as exc:  # recorded, so every worker still reaches
                    answers.append((proposal.id, repr(exc)))  # the next barrier
            return answers

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = [pair for answers in pool.map(evaluate, range(workers)) for pair in answers]

    assert len(results) == workers * rounds
    for sent, answered in results:
        assert answered == sent


# --- bounded input, non-leaking errors, protected framing -------------------

def test_serve_stdio_rejects_an_oversized_request_and_resyncs():
    """The untrusted side cannot drive the governance authority to OOM: an
    over-long line is rejected without being buffered, and the next well-formed
    request is still answered."""
    oversized = json.dumps({"method": "get_public_key", "pad": "a" * 4000})
    small = json.dumps({"method": "get_public_key"})
    out = io.StringIO()
    serve_stdio(io.StringIO(oversized + "\n" + small + "\n"), out, max_request_chars=256)
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(responses) == 2
    assert responses[0]["ok"] is False
    assert responses[1]["ok"] is True


def test_default_request_size_is_bounded():
    default = inspect.signature(serve_stdio).parameters["max_request_chars"].default
    assert default == kernel_server.MAX_REQUEST_CHARS
    assert 0 < kernel_server.MAX_REQUEST_CHARS <= 16 * 1024 * 1024


def test_boundary_errors_do_not_leak_kernel_internals():
    """Errors cross as a stable shape; pydantic field names, exception types and
    filesystem paths stay on the kernel side."""
    response = GovernanceService().handle({
        "method": "evaluate",
        "proposal": {"id": "p"},
        "intents": [],
        "world_state": {},
    })
    assert response["ok"] is False
    error = response["error"]
    assert "ValidationError" not in error
    assert "StrategyProposal" not in error
    assert "intent_id" not in error


def test_serve_stdio_takes_stdout_off_the_protocol_channel(monkeypatch):
    """A stray ``print`` anywhere in the kernel must not be able to inject a line
    into the response stream, so the child keeps the real stdout for framing and
    points ``sys.stdout`` at stderr."""
    protocol = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", protocol)
    monkeypatch.setattr(sys, "stderr", stderr)

    serve_stdio(io.StringIO(json.dumps({"method": "get_public_key"}) + "\n"))

    assert sys.stdout is stderr
    assert json.loads(protocol.getvalue())["ok"] is True
