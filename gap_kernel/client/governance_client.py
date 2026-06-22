"""Governance clients — the agent-side handle to the governance boundary (Fix 2).

The strategy/reconciler layers consume a client, never the GovernanceKernel
class. A client exposes exactly two capabilities — request an evaluation and
read the kernel's public key — and is a drop-in for the kernel in CGALoop
(both provide ``evaluate_proposal`` + ``public_key_hex``).

  * InProcessGovernanceClient — talks to a GovernanceService in the same process
    through its narrow dict API; holds no reference to the kernel object.
  * SubprocessGovernanceClient — runs the kernel in a SEPARATE process; the agent
    side holds only the public key and a request channel, never the kernel, its
    private key, or its policy registry.
"""

from __future__ import annotations

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
) -> dict:
    return {
        "method": "evaluate",
        "proposal": proposal.model_dump(mode="json"),
        "intents": [i.model_dump(mode="json") for i in intents],
        "world_state": world_state.model_dump(mode="json"),
        "action_type_id": action_type_id,
    }


class InProcessGovernanceClient:
    """A client that consumes a GovernanceService directly (same process).

    Demonstrates the boundary shape without subprocess overhead: the agent side
    interacts only through the request/response API and never touches the kernel.
    """

    def __init__(self, service: GovernanceService):
        self._service = service
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
        response = self._service.handle(
            _evaluate_request(proposal, intents, world_state, action_type_id)
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

    def __init__(self, python_executable: Optional[str] = None):
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
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise GovernanceClientError("no response from governance service")
        response = json.loads(line)
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
            _evaluate_request(proposal, intents, world_state, action_type_id)
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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
