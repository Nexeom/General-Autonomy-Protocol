"""Governance clients — the agent-side handle to the governance kernel (Fix 2).

The strategy/reconciler layers consume a client, never the GovernanceKernel
class. A client exposes exactly two capabilities — request an evaluation and
read the kernel's public key — and is a drop-in for the kernel in CGALoop
(both provide ``evaluate_proposal`` + ``public_key_hex``).

  * ``SubprocessGovernanceClient`` is the genuine structural boundary (G-2): the
    kernel — with its private signing key and policy registry — runs in a
    SEPARATE OS process, and the agent side holds only the public key and a stdio
    channel. Use it wherever forgery resistance must be enforced.
  * ``InProcessGovernanceClient`` is a same-process CONVENIENCE, **not** an
    isolation boundary: a hostile co-resident agent can always reach co-located
    objects by reflection. Use it for embedding/testing only.
"""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

from gap_kernel.models.governance import GovernanceDecision
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.service.kernel_server import GovernanceService


class GovernanceClientError(Exception):
    """Raised when the governance service returns an error across the boundary."""


def _evaluate_request(
    proposal: StrategyProposal,
    intents: List[IntentVector],
    world_state: WorldModel,
    action_type_id: Optional[str],
    current_time: Optional[datetime] = None,
) -> dict:
    return {
        "method": "evaluate",
        "proposal": proposal.model_dump(mode="json"),
        "intents": [i.model_dump(mode="json") for i in intents],
        "world_state": world_state.model_dump(mode="json"),
        "action_type_id": action_type_id,
        "current_time": current_time.isoformat() if current_time else None,
    }


class InProcessGovernanceClient:
    """A same-process CONVENIENCE client over a GovernanceService.

    This is **not** an isolation boundary (see the module docstring): a determined
    co-resident agent can reach the kernel by reflection regardless of how this
    object is shaped. It captures only the service's bound ``handle`` and public
    key — it stores no ``service`` or kernel attribute — but that is defence in
    depth, not isolation. For enforced forgery resistance use
    ``SubprocessGovernanceClient``.
    """

    def __init__(self, service: GovernanceService):
        self._handle = service.handle  # bound method; no stored service/kernel attr
        self._public_key_hex = service.public_key_hex

    @property
    def public_key_hex(self) -> str:
        return self._public_key_hex

    def evaluate_proposal(
        self,
        proposal: StrategyProposal,
        intents: List[IntentVector],
        world_state: WorldModel,
        current_time: Optional[datetime] = None,
        action_type_id: Optional[str] = None,
    ) -> GovernanceDecision:
        response = self._handle(
            _evaluate_request(proposal, intents, world_state, action_type_id, current_time)
        )
        if not response.get("ok"):
            raise GovernanceClientError(response.get("error"))
        return GovernanceDecision.model_validate(response["decision"])


class SubprocessGovernanceClient:
    """A client that runs the Governance Kernel in a separate OS process.

    The agent side holds only the public key and a stdio request channel — the
    kernel, its private signing key, and its policy registry live entirely in the
    child process and are unreachable from here (no in-process import path).
    """

    def __init__(self, python_executable: Optional[str] = None, timeout: float = 30.0):
        self._timeout = timeout
        # readline() cannot be interrupted portably (no select() on Windows pipes),
        # so reads run on a single-worker thread guarded by a timeout.
        self._reader = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._proc = subprocess.Popen(
            [python_executable or sys.executable, "-m", "gap_kernel.service.kernel_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._public_key_hex = self._call({"method": "get_public_key"})["public_key_hex"]

    @property
    def public_key_hex(self) -> str:
        return self._public_key_hex

    def _call(self, request: dict) -> dict:
        if self._proc.poll() is not None:
            raise GovernanceClientError("governance service process is not running")
        try:
            self._proc.stdin.write(json.dumps(request) + "\n")
            self._proc.stdin.flush()
        except OSError as exc:
            raise GovernanceClientError(f"cannot reach governance service: {exc}") from exc

        # Bounded read — fail closed (and kill the child) on a hang.
        future = self._reader.submit(self._proc.stdout.readline)
        try:
            line = future.result(timeout=self._timeout)
        except concurrent.futures.TimeoutError:
            self._proc.kill()
            raise GovernanceClientError("governance service timed out")
        if not line:
            raise GovernanceClientError("no response from governance service")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GovernanceClientError(
                f"malformed response from governance service: {exc}"
            ) from exc
        if not response.get("ok"):
            raise GovernanceClientError(response.get("error"))
        return response

    def evaluate_proposal(
        self,
        proposal: StrategyProposal,
        intents: List[IntentVector],
        world_state: WorldModel,
        current_time: Optional[datetime] = None,
        action_type_id: Optional[str] = None,
    ) -> GovernanceDecision:
        response = self._call(
            _evaluate_request(proposal, intents, world_state, action_type_id, current_time)
        )
        return GovernanceDecision.model_validate(response["decision"])

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._reader.shutdown(wait=False)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        # Defensive cleanup if the caller forgot close()/the context manager.
        try:
            if getattr(self, "_proc", None) is not None:
                self.close()
        except Exception:
            pass
