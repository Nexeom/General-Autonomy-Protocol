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
import logging
import sys
from typing import Optional, TextIO

from pydantic import ValidationError

from gap_kernel.crypto.signing import PublicKeyRegistry
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import StrategyProposal
from gap_kernel.models.world import WorldModel

logger = logging.getLogger("gap_kernel.service")

# The agent side frames one request per line. A longer line is refused without
# being buffered — otherwise the side this boundary exists to contain can drive
# the governance authority out of memory.
MAX_REQUEST_CHARS = 4 * 1024 * 1024

# Errors cross as stable codes. The detail — exception type, pydantic field
# names, filesystem paths — is logged on the kernel side and never sent to the
# agent side, which must not learn the kernel's internals from its failures.
ERROR_UNKNOWN_METHOD = "unknown_method"
ERROR_INVALID_REQUEST = "invalid_request"
ERROR_REQUEST_TOO_LARGE = "request_too_large"
ERROR_INTERNAL = "internal_error"


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
    """Wraps a GovernanceKernel and exposes evaluate, the public key, and
    READ-ONLY views of the action-type registry.

    Nothing here changes governance configuration. The registry arrives inside
    the signed Applicability Profile, so a write across this boundary would be an
    unsigned change to the policy set, made by the exact side the boundary exists
    to contain — an agent could register the type its own forbidden action needs
    and turn a REJECT into a signed APPROVE."""

    def __init__(self, kernel: Optional[GovernanceKernel] = None):
        self._kernel = kernel or GovernanceKernel()

    @property
    def public_key_hex(self) -> str:
        return self._kernel.public_key_hex

    def handle(self, request: dict) -> dict:
        """Map a request dict to a response dict. Never raises across the boundary.

        The response echoes the request's ``id`` when one is present, so a client
        sharing the channel between threads can prove a response answers its own
        request rather than someone else's."""
        if not isinstance(request, dict):
            return {"ok": False, "error": ERROR_INVALID_REQUEST}
        response = self._dispatch(request)
        request_id = request.get("id")
        if request_id is not None:
            response["id"] = request_id
        return response

    def _dispatch(self, request: dict) -> dict:
        method = request.get("method")
        try:
            if method == "get_public_key":
                return {"ok": True, "public_key_hex": self._kernel.public_key_hex}
            if method == "evaluate":
                proposal = StrategyProposal.model_validate(request["proposal"])
                intents = [IntentVector.model_validate(i) for i in request["intents"]]
                world = WorldModel.model_validate(request["world_state"])
                # No clock crosses the boundary: the kernel reads its own, since a
                # caller-named time is a caller-chosen set of active constraints.
                decision = self._kernel.evaluate_proposal(
                    proposal=proposal,
                    intents=intents,
                    world_state=world,
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
            logger.warning("governance service refused an unknown method: %r", method)
            return {"ok": False, "error": ERROR_UNKNOWN_METHOD}
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            logger.warning("governance service rejected a malformed %r request: %s", method, exc)
            return {"ok": False, "error": ERROR_INVALID_REQUEST}
        except Exception:  # boundary: surface errors as data, never crash the channel
            logger.exception("governance service failed while handling method %r", method)
            return {"ok": False, "error": ERROR_INTERNAL}


def _claim_stdout_for_protocol() -> TextIO:
    """Take exclusive ownership of the real stdout for the protocol framing and
    point ``sys.stdout`` at stderr, so a stray ``print`` anywhere in the kernel
    cannot inject a line into the response stream."""
    protocol_stream = sys.stdout
    sys.stdout = sys.stderr
    return protocol_stream


def _read_request_line(stream_in: TextIO, max_chars: int) -> tuple[str, bool]:
    """Read one newline-terminated request, never buffering more than ``max_chars``.

    Returns ``(line, oversize)``. The remainder of an oversized line is discarded
    in bounded chunks so the following request still frames correctly."""
    line = stream_in.readline(max_chars + 1)
    if len(line) <= max_chars or line.endswith("\n"):
        return line, False
    while True:
        rest = stream_in.readline(max_chars)
        if not rest or rest.endswith("\n"):
            return "", True


def serve_stdio(
    stream_in: Optional[TextIO] = None,
    stream_out: Optional[TextIO] = None,
    kernel: Optional[GovernanceKernel] = None,
    max_request_chars: int = MAX_REQUEST_CHARS,
) -> None:
    """Run the service over newline-delimited JSON on stdin/stdout."""
    stream_in = stream_in or sys.stdin
    stream_out = stream_out or _claim_stdout_for_protocol()
    service = GovernanceService(kernel)
    while True:
        line, oversize = _read_request_line(stream_in, max_request_chars)
        if oversize:
            logger.warning("governance service refused a request over %d chars", max_request_chars)
            response = {"ok": False, "error": ERROR_REQUEST_TOO_LARGE}
        elif not line:
            return
        else:
            line = line.strip()
            if not line:
                continue
            try:
                response = service.handle(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("governance service received a non-JSON request: %s", exc)
                response = {"ok": False, "error": ERROR_INVALID_REQUEST}
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
    # Claim the protocol stream before anything else runs, so nothing on the way
    # to the serve loop can write to it either.
    _protocol_stream = _claim_stdout_for_protocol()
    serve_stdio(stream_out=_protocol_stream, kernel=_kernel_from_argv())
