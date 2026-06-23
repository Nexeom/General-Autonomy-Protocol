"""Governance Kernel service — the kernel behind a constrained request/response API.

This is the structural half of Fix 2 (G-2): the Governance Kernel — with its
private signing key and its policy/registry state — runs as a service reached
ONLY through this narrow API (evaluate a proposal, fetch the public key). A
governed agent on the other side of the boundary never holds the kernel object,
its private key, or its registry, so it cannot read, modify, or forge governance
— it can only request a decision and verify the signature.

The service is transport-agnostic (``handle`` maps a request dict to a response
dict); ``serve_stdio`` runs it as a subprocess over newline-delimited JSON, and
``GovernanceClient`` implementations (gap_kernel/client) consume it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Optional, TextIO

from gap_kernel.crypto.signing import PublicKeyRegistry
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile
from gap_kernel.models.governance import ActionTypeSpec
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import StrategyProposal
from gap_kernel.models.world import WorldModel


def dump_governed_config(
    applicability_profile: ApplicabilityProfile,
    profile_key_registry: PublicKeyRegistry,
) -> dict:
    """Serialize the config a subprocess kernel needs to run GOVERNED — the signed
    Applicability Profile plus the (public-key-only) profile key registry. No
    private key or secret crosses the boundary; the subprocess re-verifies the
    profile signature on load and fails closed if it has been tampered with."""
    return {
        "profile": applicability_profile.model_dump(mode="json"),
        "registry": profile_key_registry.as_dict(),
    }


def kernel_from_governed_config(config: dict) -> GovernanceKernel:
    """Construct a governed GovernanceKernel from a ``dump_governed_config`` dict.

    The kernel verifies the profile signature against the supplied registry and
    raises (fail closed) on an unsigned / tampered / unknown-key profile."""
    profile = ApplicabilityProfile.model_validate(config["profile"])
    registry = PublicKeyRegistry(config.get("registry") or {})
    return GovernanceKernel(
        governed=True,
        applicability_profile=profile,
        profile_key_registry=registry,
    )


class GovernanceService:
    """Wraps a GovernanceKernel and exposes only evaluate + public-key methods,
    plus the action-type registry operations (a governance-config surface that
    must also cross the boundary so an isolated deployment is a complete drop-in)."""

    def __init__(self, kernel: Optional[GovernanceKernel] = None):
        self._kernel = kernel or GovernanceKernel()

    @property
    def public_key_hex(self) -> str:
        return self._kernel.public_key_hex

    def handle(self, request: dict) -> dict:
        """Map a request dict to a response dict. Never raises across the boundary."""
        method = request.get("method")
        try:
            if method == "get_public_key":
                return {"ok": True, "public_key_hex": self._kernel.public_key_hex}
            if method == "evaluate":
                proposal = StrategyProposal.model_validate(request["proposal"])
                intents = [IntentVector.model_validate(i) for i in request["intents"]]
                world = WorldModel.model_validate(request["world_state"])
                current_time = request.get("current_time")
                if current_time:
                    current_time = datetime.fromisoformat(current_time)
                decision = self._kernel.evaluate_proposal(
                    proposal=proposal,
                    intents=intents,
                    world_state=world,
                    current_time=current_time,
                    action_type_id=request.get("action_type_id"),
                )
                return {"ok": True, "decision": decision.model_dump(mode="json")}
            if method == "list_action_types":
                return {"ok": True, "action_types": {
                    k: v.model_dump(mode="json")
                    for k, v in self._kernel.get_registered_action_types().items()
                }}
            if method == "get_action_type":
                spec = self._kernel.get_action_type(request["type_id"])
                return {"ok": True, "action_type": spec.model_dump(mode="json") if spec else None}
            if method == "register_action_type":
                spec = ActionTypeSpec.model_validate(request["spec"])
                registered = self._kernel.register_action_type(spec, request["registered_by"])
                return {"ok": True, "action_type": registered.model_dump(mode="json")}
            return {"ok": False, "error": f"unknown method: {method!r}"}
        except Exception as exc:  # boundary: surface errors as data, never crash the channel
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def serve_stdio(
    stream_in: Optional[TextIO] = None,
    stream_out: Optional[TextIO] = None,
    kernel: Optional[GovernanceKernel] = None,
) -> None:
    """Run the service over newline-delimited JSON on stdin/stdout."""
    stream_in = stream_in or sys.stdin
    stream_out = stream_out or sys.stdout
    service = GovernanceService(kernel)
    for line in stream_in:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = service.handle(request)
        except Exception as exc:
            response = {"ok": False, "error": f"bad request: {exc}"}
        stream_out.write(json.dumps(response) + "\n")
        stream_out.flush()


def _kernel_from_argv() -> Optional[GovernanceKernel]:
    """If a governed-config file path was passed as argv[1], build a governed
    kernel from it; otherwise return None (an open kernel is used)."""
    if len(sys.argv) > 1 and sys.argv[1]:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            return kernel_from_governed_config(json.load(fh))
    return None


if __name__ == "__main__":
    serve_stdio(kernel=_kernel_from_argv())
