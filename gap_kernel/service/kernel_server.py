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
from typing import Optional, TextIO

from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import StrategyProposal
from gap_kernel.models.world import WorldModel


class GovernanceService:
    """Wraps a GovernanceKernel and exposes only evaluate + public-key methods."""

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
                decision = self._kernel.evaluate_proposal(
                    proposal=proposal,
                    intents=intents,
                    world_state=world,
                    action_type_id=request.get("action_type_id"),
                )
                return {"ok": True, "decision": decision.model_dump(mode="json")}
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


if __name__ == "__main__":
    serve_stdio()
