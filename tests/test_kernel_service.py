"""Phase E completion — out-of-process Governance Kernel service + clients (Fix 2).

The kernel runs behind a constrained request/response API; the agent-side client
holds only the public key and a request channel, never the kernel object, its
private key, or its registry. A client is a drop-in for the kernel in CGALoop.
"""

from datetime import datetime

import pytest

from gap_kernel.client.governance_client import (
    GovernanceClientError,
    InProcessGovernanceClient,
    SubprocessGovernanceClient,
)
from gap_kernel.crypto.signing import verify
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.models.governance import (
    GovernanceVerdict,
    canonical_decision_payload,
)
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.service.kernel_server import GovernanceService
from gap_kernel.strategy.cga_loop import CGALoop


def _proposal(pid="prop_s"):
    return StrategyProposal(
        id=pid, intent_id="i1", attempt_number=1, plan_description="p",
        actions=[PlannedAction(action_type="query_crm", target="t1", parameters={}, risk_score=1)],
        estimated_cost=0.01, rationale="r", generated_at=datetime.utcnow(),
    )


def _intent():
    return IntentVector(id="i1", objective="o", priority=50, hard_constraints=[],
                        soft_constraints=[], created_by="t", created_at=datetime.utcnow())


def _world():
    return WorldModel(entities={}, last_reconciled=datetime.utcnow())


# --- service ----------------------------------------------------------------

def test_service_evaluate_returns_signed_decision():
    service = GovernanceService()
    resp = service.handle({
        "method": "evaluate",
        "proposal": _proposal().model_dump(mode="json"),
        "intents": [_intent().model_dump(mode="json")],
        "world_state": _world().model_dump(mode="json"),
    })
    assert resp["ok"] is True
    from gap_kernel.models.governance import GovernanceDecision
    decision = GovernanceDecision.model_validate(resp["decision"])
    assert decision.verdict == GovernanceVerdict.APPROVED
    assert verify(service.public_key_hex, canonical_decision_payload(decision), decision.decision_signature)


def test_service_unknown_method_is_error_not_crash():
    assert GovernanceService().handle({"method": "delete_policies"})["ok"] is False


# --- in-process client ------------------------------------------------------

def test_in_process_client_produces_verifiable_decisions():
    client = InProcessGovernanceClient(GovernanceService())
    decision = client.evaluate_proposal(
        proposal=_proposal(), intents=[_intent()], world_state=_world()
    )
    assert verify(client.public_key_hex, canonical_decision_payload(decision), decision.decision_signature)


def test_inprocess_client_drops_service_reference():
    """Defence in depth — the in-process client stores no service/kernel attribute.

    NOTE: the in-process client is a convenience, NOT an isolation boundary (a
    co-resident agent can still reach the kernel by reflection). The genuine
    boundary is SubprocessGovernanceClient, asserted below.
    """
    client = InProcessGovernanceClient(GovernanceService())
    assert not hasattr(client, "_service")
    assert not hasattr(client, "_signing_key_hex")


def test_cga_loop_runs_with_in_process_client():
    """A client is a drop-in for the kernel in the loop."""
    client = InProcessGovernanceClient(GovernanceService())
    fabric = ExecutionFabric(_world(), kernel_public_key_hex=client.public_key_hex)
    loop = CGALoop(client, fabric)
    result = loop.run(intent=_intent(), drift_event={}, world_state=_world())
    assert result.final_verdict in {"approved", "escalated"}


# --- subprocess client (genuinely out-of-process) --------------------------

def test_subprocess_client_evaluates_across_a_process_boundary():
    from gap_kernel.governance.kernel import GovernanceKernel

    with SubprocessGovernanceClient() as client:
        assert client.public_key_hex  # handshake succeeded
        # Genuine boundary: the agent-side process holds no kernel object, no
        # signing key, no service — only a channel and the public key.
        assert not hasattr(client, "_signing_key_hex")
        assert not hasattr(client, "_service")
        assert not any(isinstance(v, GovernanceKernel) for v in vars(client).values())
        decision = client.evaluate_proposal(
            proposal=_proposal(), intents=[_intent()], world_state=_world()
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        # The decision is signed by the kernel in the child process.
        assert verify(
            client.public_key_hex,
            canonical_decision_payload(decision),
            decision.decision_signature,
        )
        # And it executes through a fabric wired with the service's public key.
        fabric = ExecutionFabric(_world(), kernel_public_key_hex=client.public_key_hex)
        assert fabric.execute(_proposal(), decision).success is True


def test_subprocess_client_wraps_malformed_response():
    """A non-JSON response surfaces as the single documented error type."""
    import io
    from gap_kernel.client.governance_client import GovernanceClientError

    client = SubprocessGovernanceClient()
    try:
        client._proc.stdout = io.StringIO("not json\n")  # simulate a corrupt channel
        with pytest.raises(GovernanceClientError, match="malformed response"):
            client._call({"method": "get_public_key"})
    finally:
        client.close()


def test_subprocess_client_call_times_out():
    """A hung child fails closed instead of deadlocking the agent."""
    import time
    from gap_kernel.client.governance_client import GovernanceClientError

    class _Hang:
        def readline(self):
            time.sleep(1.0)
            return ""

    client = SubprocessGovernanceClient()  # default timeout for the handshake
    try:
        client._timeout = 0.1  # subsequent calls fail fast
        client._proc.stdout = _Hang()
        with pytest.raises(GovernanceClientError, match="timed out"):
            client._call({"method": "get_public_key"})
    finally:
        client.close()
