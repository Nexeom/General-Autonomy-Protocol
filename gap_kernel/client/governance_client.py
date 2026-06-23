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
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from typing import Dict, List, Optional

from gap_kernel.models.governance import ActionTypeSpec, GovernanceDecision
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

    # Action-type registry proxy (governance-config surface across the boundary).
    def get_registered_action_types(self) -> Dict[str, ActionTypeSpec]:
        response = self._handle({"method": "list_action_types"})
        if not response.get("ok"):
            raise GovernanceClientError(response.get("error"))
        return {k: ActionTypeSpec.model_validate(v) for k, v in response["action_types"].items()}

    def get_action_type(self, type_id: str) -> Optional[ActionTypeSpec]:
        response = self._handle({"method": "get_action_type", "type_id": type_id})
        if not response.get("ok"):
            raise GovernanceClientError(response.get("error"))
        spec = response["action_type"]
        return ActionTypeSpec.model_validate(spec) if spec else None

    def register_action_type(self, spec: ActionTypeSpec, registered_by: str) -> ActionTypeSpec:
        response = self._handle({
            "method": "register_action_type",
            "spec": spec.model_dump(mode="json"),
            "registered_by": registered_by,
        })
        if not response.get("ok"):
            raise GovernanceClientError(response.get("error"))
        return ActionTypeSpec.model_validate(response["action_type"])


class SubprocessGovernanceClient:
    """A client that runs the Governance Kernel in a separate OS process.

    The agent side holds only the public key and a stdio request channel — the
    kernel, its private signing key, and its policy registry live entirely in the
    child process and are unreachable from here (no in-process import path).
    """

    def __init__(
        self,
        python_executable: Optional[str] = None,
        timeout: float = 30.0,
        governed_config: Optional[dict] = None,
    ):
        self._timeout = timeout
        # readline() cannot be interrupted portably (no select() on Windows pipes),
        # so reads run on a single-worker thread guarded by a timeout.
        self._reader = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Set these first so the attributes always exist even if construction
        # fails partway — close()/__del__ rely on them for cleanup.
        self._config_path: Optional[str] = None
        self._proc = None
        argv = [python_executable or sys.executable, "-m", "gap_kernel.service.kernel_server"]
        # Any failure after this point (Popen exec error, or a child that fails
        # closed on a tampered profile so the handshake errors) must still clean
        # up the temp file and reader thread — so wrap construction and close()
        # on error before re-raising.
        try:
            # To run the child kernel GOVERNED, hand it the signed profile +
            # registry via a temp file (public-key-only; no secret crosses the
            # boundary). The child re-verifies the profile signature and fails
            # closed on tamper.
            if governed_config is not None:
                fd, self._config_path = tempfile.mkstemp(suffix=".json", prefix="gap_gov_")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(governed_config, fh)
                argv.append(self._config_path)
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self._public_key_hex = self._call({"method": "get_public_key"})["public_key_hex"]
        except BaseException:
            self.close()  # idempotent; reaps any child + unlinks the temp file
            raise

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

    # Action-type registry proxy (governance-config surface across the boundary).
    def get_registered_action_types(self) -> Dict[str, ActionTypeSpec]:
        response = self._call({"method": "list_action_types"})
        return {k: ActionTypeSpec.model_validate(v) for k, v in response["action_types"].items()}

    def get_action_type(self, type_id: str) -> Optional[ActionTypeSpec]:
        response = self._call({"method": "get_action_type", "type_id": type_id})
        spec = response["action_type"]
        return ActionTypeSpec.model_validate(spec) if spec else None

    def register_action_type(self, spec: ActionTypeSpec, registered_by: str) -> ActionTypeSpec:
        response = self._call({
            "method": "register_action_type",
            "spec": spec.model_dump(mode="json"),
            "registered_by": registered_by,
        })
        return ActionTypeSpec.model_validate(response["action_type"])

    def close(self) -> None:
        """Idempotent; tolerant of a partially-constructed client (``_proc`` may be
        None if Popen never succeeded), so it always reaps the child and unlinks
        the temp config file."""
        proc = getattr(self, "_proc", None)
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        reader = getattr(self, "_reader", None)
        if reader is not None:
            reader.shutdown(wait=False)
        if getattr(self, "_config_path", None) is not None:
            try:
                os.unlink(self._config_path)
            except OSError:
                pass
            self._config_path = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        # Defensive cleanup if the caller forgot close()/the context manager.
        # close() is fully null-tolerant, so call it unconditionally (also reaps a
        # temp file left by a Popen that failed before _proc was assigned).
        try:
            self.close()
        except Exception:
            pass
