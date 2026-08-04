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

The same reasoning applies to the two things a floor is worthless without: an
independently-deployed trust root (or the floor is verified against a key its own
supplier chose) and durable ledgers (or replay protection lasts until the next
restart). Both are required by default here, each with one explicitly named
prototype escape hatch.
"""

from __future__ import annotations

import os
from typing import Optional

from gap_kernel.client.governance_client import SubprocessGovernanceClient
from gap_kernel.crypto.signing import PublicKeyRegistry
from gap_kernel.errors import GovernanceConfigError
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile
from gap_kernel.governance.self_evolution import SelfEvolutionMonitor
from gap_kernel.governance.sir import StructuredIntentResolver
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.world import WorldModel
from gap_kernel.service.kernel_server import (
    TrustRoot,
    dump_governed_config,
    load_trust_root,
)
from gap_kernel.strategy.cga_loop import CGALoop
from gap_kernel.verification.execution_ledger import ExecutionLedger
from gap_kernel.verification.oob_ledger import OOBLedger

OOB_LEDGER_FILENAME = "oob_ledger.db"
EXECUTION_LEDGER_FILENAME = "execution_ledger.db"


def _is_ephemeral(ledger) -> bool:
    """True when a ledger would not survive the process that created it.

    A ledger the caller never supplied is ephemeral too: the Execution Fabric's
    own default is ``:memory:``.
    """
    return ledger is None or getattr(ledger, "db_path", ":memory:") == ":memory:"


def _resolve_ledgers(
    ledger_dir: Optional[str],
    oob_ledger: Optional[OOBLedger],
    execution_ledger: Optional[ExecutionLedger],
    allow_ephemeral_ledgers: bool,
):
    """Back both replay ledgers with files under ``ledger_dir``, and refuse a
    governed deployment whose replay protection would not survive a restart."""
    if ledger_dir is not None:
        os.makedirs(ledger_dir, exist_ok=True)
        if oob_ledger is None:
            oob_ledger = OOBLedger(os.path.join(ledger_dir, OOB_LEDGER_FILENAME))
        if execution_ledger is None:
            execution_ledger = ExecutionLedger(
                os.path.join(ledger_dir, EXECUTION_LEDGER_FILENAME)
            )
    if not allow_ephemeral_ledgers:
        for what, ledger in (
            ("execution", execution_ledger),
            ("out-of-band approval", oob_ledger),
        ):
            if _is_ephemeral(ledger):
                raise GovernanceConfigError(
                    f"A governed deployment requires a durable {what} ledger: an "
                    f"in-memory ledger loses every record of what has already been "
                    f"executed on restart, so a spent authorization becomes "
                    f"replayable. Pass ledger_dir=..., or "
                    f"allow_ephemeral_ledgers=True for prototyping only."
                )
    return oob_ledger, execution_ledger


def build_governed_deployment(
    *,
    applicability_profile: ApplicabilityProfile,
    world_model: WorldModel,
    profile_key_registry: Optional[PublicKeyRegistry] = None,
    evidence_issuers: Optional[PublicKeyRegistry] = None,
    ledger_dir: Optional[str] = None,
    require_independent_trust_root: bool = True,
    allow_ephemeral_ledgers: bool = False,
    approver_registry: Optional[PublicKeyRegistry] = None,
    approver_max_levels: Optional[dict] = None,
    oob_ledger: Optional[OOBLedger] = None,
    execution_ledger: Optional[ExecutionLedger] = None,
    intent_resolver: Optional[StructuredIntentResolver] = None,
    integrity_monitor: Optional[GovernanceIntegrityMonitor] = None,
    self_evolution_monitor: Optional[SelfEvolutionMonitor] = None,
    kill_switch: Optional[KillSwitch] = None,
    strategy_generator=None,
    max_attempts: int = 3,
    isolated: bool = True,
) -> CGALoop:
    """Assemble a fail-closed governed deployment and return its CGA loop.

    Requires the industry-specific regulatory floor (``applicability_profile``),
    an independently-deployed trust root, and a durable ``ledger_dir``. Wires the
    universal safety primitives:

      - the kernel verifies the signed floor against the trust root's keys and
        runs in governed mode (strict action typing on);
      - the kernel verifies Signed Evidence Attestations against the trust root's
        ``evidence_issuers``, and against ``evidence_issuers`` here only when no
        trust root is in force. A deployment that names none accepts no
        attestation, so every world-model-backed constraint is unevaluable and
        therefore violated — fail-closed, and loud in the log at construction.
        This hardens the TWO evaluators that read the world model
        (``gdpr_consent_required``, ``no_contact_outside_hours``); the other
        seven rule on agent-authored action parameters and are unaffected;
      - the trust root is resolved from ``GAP_TRUST_ROOT`` — a path the DEPLOYER
        owns — rather than from ``profile_key_registry``, which is authored by
        the same process that authors the profile and therefore verifies any
        profile that process chooses. The isolated kernel re-resolves it from its
        own environment, and the client pins the child's identity to the public
        key the trust root names. Pass
        ``require_independent_trust_root=False`` — the named prototype escape
        hatch — to fall back to ``profile_key_registry`` and a per-process kernel
        key. This bounds a caller who can only supply governance *data*; it does
        not bound one who already has code execution in this process, who can
        set the environment variable too;
      - replay protection is DURABLE: ``ledger_dir`` backs the ExecutionLedger
        (single-use decisions at every level) and the OOB ledger (L2+ human
        approvals) with files, so a restart does not reset what has already been
        executed. ``allow_ephemeral_ledgers=True`` — the named prototype escape
        hatch — permits ``:memory:``;
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

    Raises ``GovernanceConfigError`` if the regulatory floor, the trust root, or
    a durable ledger is missing.
    """
    if applicability_profile is None:
        raise GovernanceConfigError(
            "A governed deployment requires an Applicability Profile (the "
            "regulatory floor). The floor's content is industry-specific; "
            "supplying one is mandatory."
        )

    # Resolved first: an unusable trust root must stop the deployment before any
    # kernel process exists. TrustRootError is a GovernanceConfigError.
    trust_root: Optional[TrustRoot] = (
        load_trust_root() if require_independent_trust_root else None
    )

    oob_ledger, execution_ledger = _resolve_ledgers(
        ledger_dir, oob_ledger, execution_ledger, allow_ephemeral_ledgers
    )

    # Corrigibility is universal: a governed deployment always has a kill-switch,
    # shared by reference between the fabric and the loop.
    kill_switch = kill_switch or KillSwitch()

    if isolated:
        # Default: the governed kernel runs in a separate OS process. Only the
        # signed profile crosses via a temp file — with a trust root in force the
        # registry sent alongside it is deliberately EMPTY, so a child that
        # somehow failed to resolve its own trust root has nothing to verify
        # against and fails closed rather than trusting what the parent sent.
        kernel = SubprocessGovernanceClient(
            governed_config=dump_governed_config(
                applicability_profile,
                PublicKeyRegistry() if trust_root is not None
                else (profile_key_registry or PublicKeyRegistry()),
                PublicKeyRegistry() if trust_root is not None
                else (evidence_issuers or PublicKeyRegistry()),
            ),
            require_trust_root=require_independent_trust_root,
        )
    else:
        signing_key_hex = public_key_hex = None
        registry = profile_key_registry
        issuers = evidence_issuers
        if trust_root is not None:
            registry = trust_root.profile_key_registry()
            issuers = trust_root.evidence_issuer_registry()
            signing_key_hex, public_key_hex = trust_root.load_kernel_identity()
        kernel = GovernanceKernel(
            governed=True,
            applicability_profile=applicability_profile,
            profile_key_registry=registry,
            evidence_issuers=issuers,
            signing_key_hex=signing_key_hex,
            public_key_hex=public_key_hex,
        )
    fabric = ExecutionFabric(
        world_model,
        kernel_public_key_hex=kernel.public_key_hex,  # signature verification on
        public_key_registry=approver_registry,
        approver_max_levels=approver_max_levels,
        oob_ledger=oob_ledger,
        execution_ledger=execution_ledger,
        kill_switch=kill_switch,
    )
    return CGALoop(
        kernel,
        fabric,
        strategy_generator=strategy_generator,
        max_attempts=max_attempts,
        intent_resolver=intent_resolver or StructuredIntentResolver(),
        integrity_monitor=integrity_monitor or GovernanceIntegrityMonitor(),
        self_evolution_monitor=self_evolution_monitor or SelfEvolutionMonitor(),
        governed=True,
        kill_switch=kill_switch,
    )


__all__ = ["build_governed_deployment", "GovernanceConfigError", "AuthorizationLevel"]
