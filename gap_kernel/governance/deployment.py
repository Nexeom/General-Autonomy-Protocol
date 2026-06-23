"""Governed deployment assembly — the default-safe, fail-closed entry point.

The remediation gives GAP two postures:

  * **Open / prototype** (the raw `GovernanceKernel` / `ExecutionFabric` / `CGALoop`
    constructors) — permissive defaults for embedding and tests.
  * **Governed** (this factory) — a production posture that REQUIRES the
    industry-specific regulatory floor (a signed Applicability Profile) and turns
    on the universal safety primitives: kernel-signature verification, strict
    action typing, and the SIR intent-transfer gate, with GIM observation wired.

The distinction is deliberate: the floor's *content* is industry/jurisdiction
specific (HIPAA, GDPR, financial conduct, …) so it cannot be a universal default —
but *requiring* a floor is universal. ``build_governed_deployment`` fails closed
when the industry floor is absent, rather than running open.
"""

from __future__ import annotations

from typing import Optional

from gap_kernel.client.governance_client import SubprocessGovernanceClient
from gap_kernel.crypto.signing import PublicKeyRegistry
from gap_kernel.errors import GovernanceConfigError
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile
from gap_kernel.governance.sir import StructuredIntentResolver
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.world import WorldModel
from gap_kernel.service.kernel_server import dump_governed_config
from gap_kernel.strategy.cga_loop import CGALoop
from gap_kernel.verification.oob_ledger import OOBLedger


def build_governed_deployment(
    *,
    applicability_profile: ApplicabilityProfile,
    profile_key_registry: PublicKeyRegistry,
    world_model: WorldModel,
    approver_registry: Optional[PublicKeyRegistry] = None,
    approver_max_levels: Optional[dict] = None,
    oob_ledger: Optional[OOBLedger] = None,
    intent_resolver: Optional[StructuredIntentResolver] = None,
    integrity_monitor: Optional[GovernanceIntegrityMonitor] = None,
    kill_switch: Optional[KillSwitch] = None,
    strategy_generator=None,
    max_attempts: int = 3,
    isolated: bool = True,
) -> CGALoop:
    """Assemble a fail-closed governed deployment and return its CGA loop.

    Requires the industry-specific regulatory floor (``applicability_profile`` +
    ``profile_key_registry``). Wires the universal safety primitives:

      - the kernel verifies the signed floor and runs in governed mode (strict
        action typing on);
      - by default (``isolated=True``) the kernel — with its private signing key —
        runs OUT OF PROCESS behind ``SubprocessGovernanceClient``; the agent side
        holds only the public key and a request channel, so it cannot read or
        forge governance even by reflection. Pass ``isolated=False`` to run the
        kernel in-process (embedding / tests), which is a convenience, not an
        isolation boundary;
      - the Execution Fabric verifies the kernel's signature on every decision
        (fail closed) and enforces OOB approval for L2+ against ``approver_registry``
        (with an optional per-approver ceiling);
      - the CGA loop runs in governed mode (the SIR gate is mandatory) with GIM
        observation wired;
      - a corrigibility ``KillSwitch`` (shared by the fabric and the loop) is
        always present. Engaging it halts execution and planning; corrigibility
        is universal, so a governed deployment is never without one.

    The kill-switch is reachable on the returned loop as ``loop.kill_switch`` so
    a human authority can ``engage()`` / ``disengage()`` it out of band. When
    ``isolated``, the kernel runs in a subprocess that must be reaped: use the
    returned loop as a context manager — ``with build_governed_deployment(...) as
    loop:`` — or call ``loop.close()`` on shutdown (a no-op when not isolated).

    Raises ``GovernanceConfigError`` if the regulatory floor is missing.
    """
    if applicability_profile is None:
        raise GovernanceConfigError(
            "A governed deployment requires an Applicability Profile (the "
            "regulatory floor). The floor's content is industry-specific; "
            "supplying one is mandatory."
        )

    # Corrigibility is universal: a governed deployment always has a kill-switch,
    # shared by reference between the fabric and the loop.
    kill_switch = kill_switch or KillSwitch()

    if isolated:
        # Default: the governed kernel runs in a separate OS process. The signed
        # profile + (public-key-only) registry cross via a temp file; the child
        # re-verifies the signature and fails closed on tamper.
        kernel = SubprocessGovernanceClient(
            governed_config=dump_governed_config(
                applicability_profile, profile_key_registry
            )
        )
    else:
        kernel = GovernanceKernel(
            governed=True,
            applicability_profile=applicability_profile,
            profile_key_registry=profile_key_registry,
        )
    fabric = ExecutionFabric(
        world_model,
        kernel_public_key_hex=kernel.public_key_hex,  # signature verification on
        public_key_registry=approver_registry,
        approver_max_levels=approver_max_levels,
        oob_ledger=oob_ledger,
        kill_switch=kill_switch,
    )
    return CGALoop(
        kernel,
        fabric,
        strategy_generator=strategy_generator,
        max_attempts=max_attempts,
        intent_resolver=intent_resolver or StructuredIntentResolver(),
        integrity_monitor=integrity_monitor or GovernanceIntegrityMonitor(),
        governed=True,
        kill_switch=kill_switch,
    )


__all__ = ["build_governed_deployment", "GovernanceConfigError", "AuthorizationLevel"]
