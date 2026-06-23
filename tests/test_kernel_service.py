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


# --- governed kernel out of process (default isolation, G-2) ----------------

from datetime import timedelta  # noqa: E402

from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair  # noqa: E402
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile  # noqa: E402
from gap_kernel.models.governance import ActionTypeSpec  # noqa: E402
from gap_kernel.models.intent import Constraint, ConstraintType  # noqa: E402
from gap_kernel.service.kernel_server import dump_governed_config  # noqa: E402

_KID = "regulatory_authority"


def _signed_governed_config():
    priv, pub = generate_keypair()
    registry = PublicKeyRegistry({_KID: pub})
    profile = ApplicabilityProfile(
        profile_id="prof",
        tier1_constraints=[Constraint(name="cost_ceiling", type=ConstraintType.HARD,
                                      description="Floor $100.00")],
        issued_at=datetime(2026, 1, 1),
    )
    return dump_governed_config(sign_profile(profile, priv, _KID), registry)


def test_subprocess_governed_kernel_enforces_strict_typing_out_of_process():
    """The signed floor crosses to the child, which runs GOVERNED: strict action
    typing rejects a proposal with no action_type_id — proving the governed config
    is applied across the boundary, not just the in-process path."""
    with SubprocessGovernanceClient(governed_config=_signed_governed_config()) as client:
        assert not hasattr(client, "_signing_key_hex")     # still no key on the agent side
        rejected = client.evaluate_proposal(
            proposal=_proposal(), intents=[_intent()], world_state=_world()
        )
        assert rejected.verdict == GovernanceVerdict.REJECTED  # strict typing on
        approved = client.evaluate_proposal(
            proposal=_proposal(), intents=[_intent()], world_state=_world(),
            action_type_id="task_execution",
        )
        assert approved.verdict == GovernanceVerdict.APPROVED

    # Contrast: an OPEN child (no governed config) approves the untyped proposal.
    with SubprocessGovernanceClient() as open_client:
        d = open_client.evaluate_proposal(
            proposal=_proposal(), intents=[_intent()], world_state=_world()
        )
        assert d.verdict == GovernanceVerdict.APPROVED


def test_subprocess_rejects_a_tampered_profile_fail_closed():
    """A tampered signed profile is rejected by the child kernel on load, so the
    client cannot even hand-shake — fail closed, no governance runs."""
    config = _signed_governed_config()
    config["profile"]["signature"] = "00" * 64  # break the signature
    with pytest.raises(GovernanceClientError):
        SubprocessGovernanceClient(governed_config=config)


def test_action_type_registry_proxied_across_the_boundary():
    """The action-type registry (a governance-config surface) is reachable through
    the boundary, so an isolated deployment is a complete drop-in."""
    with SubprocessGovernanceClient() as client:
        assert "task_execution" in client.get_registered_action_types()
        assert client.get_action_type("nope") is None
        client.register_action_type(
            ActionTypeSpec(type_id="custom_x", description="a custom type"), "admin"
        )
        got = client.get_action_type("custom_x")
        assert got is not None and got.registered_by == "admin"


def test_failed_construction_leaves_no_temp_file():
    """A construction that fails — Popen exec error, or a tampered profile the
    child rejects — must deterministically clean up its temp config file, not
    leak one per failure."""
    import glob
    import os
    import tempfile as _tf

    pattern = os.path.join(_tf.gettempdir(), "gap_gov_*.json")
    before = set(glob.glob(pattern))

    # (a) Popen fails (bad executable) after the temp file was written.
    with pytest.raises(Exception):
        SubprocessGovernanceClient(
            python_executable="/nonexistent/python_xyz",
            governed_config=_signed_governed_config(),
        )
    # (b) handshake fails closed on a tampered profile (child exits on load).
    cfg = _signed_governed_config()
    cfg["profile"]["signature"] = "00" * 64
    with pytest.raises(GovernanceClientError):
        SubprocessGovernanceClient(governed_config=cfg)

    import gc
    gc.collect()
    assert set(glob.glob(pattern)) <= before  # no net-new leaked temp files
