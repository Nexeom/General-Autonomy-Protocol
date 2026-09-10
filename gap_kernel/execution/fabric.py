"""
Execution Fabric — Layer 3 of the GAP architecture.

Receives approved strategies and dispatches them to tools, APIs, agents,
or human task queues. Reports structured outcomes back to the World Model.

Behavioral Contract:
- Accepts only strategies that carry a valid GovernanceDecision with verdict=APPROVED
- Never executes without governance approval
- Reports structured outcomes back to the World Model
- Handles execution-level retries (e.g., API timeout), not strategy-level retries
"""

import hashlib
import json
import time
from typing import Callable, Dict, Optional

from pydantic import TypeAdapter

from gap_kernel._time import ensure_utc, utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, verify as verify_signature
from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.models.execution import ExecutionResult
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
    canonical_decision_payload,
)
from gap_kernel.models.strategy import (
    PlannedAction,
    StrategyProposal,
    compute_proposal_digest,
)
from gap_kernel.models.world import WorldModel
from gap_kernel.verification.execution_ledger import (
    ExecutionLedger,
    ExecutionReplayError,
    ExecutionRow,
)
from gap_kernel.verification.oob_ledger import OOBLedger, ReplayError

# Authorization levels that require OOB verification
_OOB_REQUIRED_LEVELS = {
    AuthorizationLevel.L2,
    AuthorizationLevel.L3,
    AuthorizationLevel.L4,
}

# Ordinal rank for authorization levels (for per-approver ceiling comparison).
_AUTH_RANK = {
    AuthorizationLevel.L0: 0,
    AuthorizationLevel.L1: 1,
    AuthorizationLevel.L2: 2,
    AuthorizationLevel.L3: 3,
    AuthorizationLevel.L4: 4,
}


class ExecutionError(Exception):
    """Raised when an action fails to execute."""
    pass


class OOBVerificationError(ExecutionError):
    """Raised when Out-of-Band Authority Verification fails for L2+ actions."""
    pass


class KillSwitchEngaged(ExecutionError):
    """Raised when execution is attempted while the corrigibility kill-switch is
    engaged (SA-4). Subclasses :class:`ExecutionError` so existing
    ``except ExecutionError`` handlers still catch a halt, while callers that
    want to distinguish a deliberate halt from an ordinary failure can catch
    this type specifically."""
    pass


class ReplayExecutionError(ExecutionError):
    """Raised when a decision that has already been executed is presented again.

    A decision is a single-use authorization. Every other guard in the fabric is
    stateless and passes identically on each replay, so this is the only one that
    can tell a first execution from a fourth."""
    pass


# Sentinel completion key recording that this nonce's L2+ human approval has been
# reserved. Kept in the same per-nonce table as the action keys so the reservation
# settles atomically with the execution it belongs to; it can never collide with a
# real action key, which is always "<index>:<sha256>".
_OOB_RESERVATION_KEY = "__oob_reservation__"

# Match ExecutionResult's JSON serialization without restricting embedded
# executors to JSON-native Python values (datetime, UUID and Decimal are valid).
_RECEIPT_SERIALIZER = TypeAdapter(dict)


def _action_idempotency_key(index: int, action: PlannedAction) -> str:
    """Stable per-action key for resume. Binds the action's position in the plan
    AND its content, so a resumed execution can only skip the exact action that
    already succeeded — never a different one that happens to share a slot."""
    payload = json.dumps(action.model_dump(mode="json"), sort_keys=True, default=str)
    return f"{index}:{hashlib.sha256(payload.encode()).hexdigest()}"


class ExecutionFabric:
    """
    Dispatches approved strategies. For the kernel prototype,
    this uses mock executors. In production, this would integrate
    with CRM APIs, email systems, etc.
    """

    def _register_default_executors(self) -> None:
        """Register mock executors for prototype action types."""
        self._executors["send_email"] = self._mock_send_email
        self._executors["send_sms"] = self._mock_send_sms
        self._executors["query_crm"] = self._mock_query_crm
        self._executors["route_to_human"] = self._mock_route_to_human
        self._executors["automated_outreach"] = self._mock_automated_outreach
        self._executors["direct_call"] = self._mock_direct_call
        self._executors["update_record"] = self._mock_update_record

    def register_executor(
        self, action_type: str, executor: Callable
    ) -> None:
        """Register a custom executor for an action type."""
        self._executors[action_type] = executor

    def __init__(
        self,
        world_model: WorldModel,
        oob_ledger: Optional[OOBLedger] = None,
        public_key_registry: Optional[PublicKeyRegistry] = None,
        kernel_public_key_hex: Optional[str] = None,
        allow_unsigned_decisions: bool = False,
        approver_max_levels: Optional[Dict[str, AuthorizationLevel]] = None,
        kill_switch: Optional[KillSwitch] = None,
        execution_ledger: Optional[ExecutionLedger] = None,
        kernel_key_registry: Optional[PublicKeyRegistry] = None,
        before_dispatch: Optional[Callable[[PlannedAction], None]] = None,
    ):
        self.world_model = world_model
        self._executors: Dict[str, Callable] = {}
        # Corrigibility: when this human-controlled kill-switch is engaged, no
        # action is dispatched (checked first, fail closed).
        self._kill_switch = kill_switch
        # Trusted deployment hook, never proposal-supplied. A raised exception
        # stops the batch before this action and preserves prior completions.
        self._before_dispatch = before_dispatch
        # Persistent replay protection + approver-key trust boundary for OOB.
        # Defaults are process-local; production injects shared, durable stores.
        self._oob_ledger = oob_ledger if oob_ledger is not None else OOBLedger()
        # Replay authority for the decision itself, at EVERY authorization level.
        # The OOB ledger only covers the L2+ human-approval gates, which leaves
        # L0/L1 — the routine autonomous path — with no record of what has already
        # been executed at all.
        self._execution_ledger = (
            execution_ledger if execution_ledger is not None else ExecutionLedger()
        )
        self._public_key_registry = (
            public_key_registry if public_key_registry is not None else PublicKeyRegistry()
        )
        # Optional trust store of Governance Kernel public keys, resolved by the
        # decision's `kernel_public_key_id` — which is inside the signed payload,
        # so which key signed is authenticated rather than rewritable metadata.
        # An unknown key id fails closed.
        self._kernel_key_registry = kernel_key_registry
        # Kernel public key (Fix 2) — the fabric verifies that every decision was
        # signed by the trusted Governance Kernel before executing. Fail closed
        # by default: if no key is configured the fabric REFUSES to execute,
        # unless `allow_unsigned_decisions=True` is passed as an explicit prototype
        # escape hatch. An unverifiable decision is never trusted by omission.
        self._kernel_public_key_hex = kernel_public_key_hex
        self._allow_unsigned_decisions = allow_unsigned_decisions
        # Optional per-approver authority ceiling (Fix 4): the maximum
        # AuthorizationLevel each approver key id may release. When provided, an
        # approver may not authorize above their ceiling, and an approver absent
        # from the map cannot authorize at all (fail closed).
        self._approver_max_levels = approver_max_levels
        self._register_default_executors()

    def execute(
        self,
        proposal: StrategyProposal,
        governance_decision: GovernanceDecision,
    ) -> ExecutionResult:
        """
        Execute an approved strategy proposal.

        GUARD: A human-engaged kill-switch halts all execution (corrigibility).
        GUARD: The decision must authorize THIS proposal.
        GUARD: The decision must be signed by the trusted Governance Kernel.
        GUARD: Never execute without governance approval.
        GUARD: The decision must be unexpired and not already executed (its
               signed nonce is claimed in the ExecutionLedger before dispatch).
        GUARD: L2+ requires Out-of-Band Authority Verification, reserved BEFORE
               dispatch so the same human approval cannot be spent twice.

        A dispatch failure leaves the execution resumable while its authority
        remains valid: a retry skips actions recorded as completed. Tools need
        their own idempotency for effects whose response or completion record
        was lost. Authority is rechecked before every new action.
        """
        # Corrigibility halt (checked first): a halt overrides everything.
        if self._kill_switch is not None:
            if self._kill_switch.is_engaged() or any(
                self._kill_switch.is_engaged(a.target) for a in proposal.actions
            ):
                raise KillSwitchEngaged(
                    f"Execution halted for proposal {proposal.id}: the kill-switch "
                    f"is engaged. No action will be dispatched."
                )

        # Bind execution to the evaluated proposal (Fix 2): a decision authorizes
        # the specific proposal it was rendered for. Executing a different payload
        # under someone else's approval is a confused-deputy attack.
        if proposal.id != governance_decision.proposal_id:
            raise ExecutionError(
                f"Decision {governance_decision.id} authorizes proposal "
                f"'{governance_decision.proposal_id}', not '{proposal.id}'."
            )
        # Content binding: the proposal's actions must match what was evaluated —
        # a same-id proposal with mutated content cannot ride a prior decision.
        if governance_decision.proposal_digest is not None:
            if compute_proposal_digest(proposal) != governance_decision.proposal_digest:
                raise ExecutionError(
                    f"Decision {governance_decision.id} authorizes a different "
                    f"version of proposal '{proposal.id}' (content digest mismatch)."
                )

        # Structural boundary (Fix 2): trust only decisions the kernel signed.
        self._verify_decision_signature(governance_decision)

        if governance_decision.verdict != GovernanceVerdict.APPROVED:
            raise ExecutionError(
                f"Cannot execute proposal {proposal.id}: "
                f"governance verdict is {governance_decision.verdict.value}, "
                f"not approved."
            )

        # Freshness + single-use fields. Checked before anything is claimed or
        # reserved so a stale or unbound decision never reaches the ledgers.
        self._verify_single_use_fields(governance_decision)

        # OOB Authority Verification for L2+ authorization gates (cryptographic
        # checks only — the approval is *reserved* below, before dispatch).
        self._verify_oob_authority(governance_decision)

        # Claim the decision's nonce. This is the replay authority: a nonce that
        # already ran to completion is refused here, at every authorization
        # level. A nonce whose previous attempt failed is RESUMED instead, and
        # carries forward which of its actions already succeeded.
        row = self._begin_execution(proposal, governance_decision)

        # Reserve the human approval BEFORE dispatch (TOCTOU): checking it, then
        # dispatching, then consuming leaves a window in which a second execution
        # spends the same approval. The reservation is recorded against the nonce
        # only once it has actually succeeded, so a resumed execution re-spends
        # nothing — and an attempt whose reservation FAILED is not waved through
        # on its next try.
        # A claim held by this call must be settled even if the attempt aborts by
        # raising: an unsettled row keeps its in-flight lease, which would lock a
        # still-valid authorization until the lease expired instead of leaving it
        # immediately retryable.
        try:
            if row is None:
                self._reserve_oob_authority(governance_decision)
            elif _OOB_RESERVATION_KEY not in row.completed_actions:
                self._reserve_oob_authority(governance_decision)
                self._execution_ledger.record_action(row.nonce, _OOB_RESERVATION_KEY)
        except BaseException:
            if row is not None:
                self._execution_ledger.finish(row.nonce, success=False)
            raise

        start_time = time.monotonic()
        completed = []
        failed = []
        state_changes = []
        already_done = row.completed_actions if row is not None else frozenset()

        try:
            prior_results = self._execution_ledger.action_results(row.nonce) if row is not None else {}
            for index, action in enumerate(proposal.actions):
                key = _action_idempotency_key(index, action)
                if key in already_done:
                    # The side effect happened under this same authorization on an
                    # earlier attempt. Report it as completed, but do NOT re-dispatch
                    # it and do NOT re-apply its world-state change.
                    receipt = prior_results.get(key)
                    completed.append({**receipt, "skipped": True} if receipt is not None
                                     else self._already_completed(action))
                    continue
                try:
                    # A valid batch start does not authorize a later action
                    # after consent, human authority, or the decision expires.
                    # The service hook checks its current policy/evidence; the
                    # final clock checks also cover time spent in that hook.
                    if self._before_dispatch is not None:
                        self._before_dispatch(action)
                    self._verify_single_use_fields(governance_decision)
                    self._verify_oob_authority(governance_decision)
                    if self._kill_switch is not None and (
                        self._kill_switch.is_engaged()
                        or self._kill_switch.is_engaged(action.target)
                    ):
                        raise KillSwitchEngaged(
                            f"Execution halted for proposal {proposal.id}: the "
                            "kill-switch is engaged. No further action will be dispatched."
                        )
                except BaseException as exc:
                    failed.append({
                        "action_type": action.action_type,
                        "target": action.target,
                        "success": False,
                        "error": str(exc) or type(exc).__name__,
                        "failure_stage": "before_dispatch",
                        "duration": 0.0,
                    })
                    raise
                try:
                    result = self._dispatch_action(action)
                except BaseException as exc:
                    # Termination can happen after an external effect but before
                    # the executor returns. Propagate it after recording that the
                    # current action's outcome needs reconciliation.
                    failed.append({
                        "action_type": action.action_type,
                        "target": action.target,
                        "success": False,
                        "error": str(exc) or type(exc).__name__,
                        "failure_stage": "dispatch",
                        "outcome_unknown": True,
                        "duration": 0.0,
                    })
                    raise
                if result["success"]:
                    if row is not None:
                        try:
                            receipt = _RECEIPT_SERIALIZER.dump_python(result, mode="json")
                            self._execution_ledger.record_action(row.nonce, key, result=receipt)
                        except BaseException as exc:
                            # The tool already ran, but its durable completion
                            # cannot be assumed. Keep this uncertainty in the
                            # outcome journal instead of settling an empty failure.
                            failed.append({
                                "action_type": action.action_type,
                                "target": action.target,
                                "success": False,
                                "error": str(exc) or type(exc).__name__,
                                "failure_stage": "receipt_persistence",
                                "outcome_unknown": True,
                                "duration": result["duration"],
                            })
                            if isinstance(exc, Exception):
                                raise ExecutionError(
                                    "Tool returned successfully but its completion receipt "
                                    "could not be persisted; outcome requires recovery."
                                ) from exc
                            raise
                    completed.append(result)
                    # Update world model with outcome
                    changes = self._apply_state_changes(action, result)
                    state_changes.extend(changes)
                else:
                    failed.append(result)
        except BaseException:
            if row is not None:
                interrupted = ExecutionResult(
                    proposal_id=proposal.id, actions_completed=completed,
                    actions_failed=failed, success=False,
                    world_state_changes=state_changes, executed_at=utcnow(),
                    execution_duration_seconds=round(time.monotonic() - start_time, 3),
                )
                interrupted_payload = interrupted.model_dump(mode="json")
                if any(item.get("outcome_unknown") for item in failed):
                    interrupted_payload["outcome_unknown"] = True
                self._execution_ledger.finish(
                    row.nonce, success=False,
                    result=interrupted_payload,
                    decision=governance_decision.model_dump(mode="json"),
                )
            raise

        elapsed = time.monotonic() - start_time
        success = len(failed) == 0

        outcome = ExecutionResult(
            proposal_id=proposal.id,
            actions_completed=completed,
            actions_failed=failed,
            success=success,
            world_state_changes=state_changes,
            executed_at=utcnow(),
            execution_duration_seconds=round(elapsed, 3),
        )
        # Store the outcome atomically with settlement, so an audit/response
        # failure after this point cannot erase the durable execution result.
        if row is not None:
            self._execution_ledger.finish(
                row.nonce, success=success, result=outcome.model_dump(mode="json"),
                decision=governance_decision.model_dump(mode="json"),
            )
        return outcome

    @staticmethod
    def _already_completed(action: PlannedAction) -> dict:
        """The result entry for an action a previous attempt already completed."""
        return {
            "action_type": action.action_type,
            "target": action.target,
            "success": True,
            "data": {"status": "already_completed"},
            "duration": 0.0,
            "skipped": True,
        }

    def _begin_execution(
        self, proposal: StrategyProposal, decision: GovernanceDecision
    ) -> Optional[ExecutionRow]:
        """Claim (or resume) this decision's nonce in the ExecutionLedger.

        Returns ``None`` only in the unsigned-decision escape hatch, where the
        decision carries no authenticated identity to key replay on (see
        ``_verify_single_use_fields``).
        """
        if decision.nonce is None:
            return None
        try:
            return self._execution_ledger.begin(
                decision.nonce,
                decision_id=decision.id,
                proposal_id=proposal.id,
                decision=decision.model_dump(mode="json"),
            )
        except ExecutionReplayError as exc:
            raise ReplayExecutionError(str(exc)) from exc

    def _verify_single_use_fields(self, decision: GovernanceDecision) -> None:
        """A decision authorizes ONE execution, for a bounded time.

        Fail closed: a decision the fabric authenticates must carry the nonce and
        expiry the kernel signs into it. Without them replay protection is
        unevaluable, which is a violation, not a pass. The sole exception is the
        `allow_unsigned_decisions` prototype escape hatch, where no field of the
        decision is authenticated in the first place and the fabric has already
        been told so explicitly.
        """
        if decision.nonce is None or decision.expires_at is None:
            if (
                self._kernel_public_key_hex is None
                and self._kernel_key_registry is None
                and self._allow_unsigned_decisions
            ):
                return
            raise ExecutionError(
                f"Decision {decision.id} carries no single-use binding (nonce and "
                f"expires_at); replay protection cannot be evaluated, so it is "
                f"refused."
            )
        if utcnow() >= ensure_utc(decision.expires_at):
            raise ExecutionError(
                f"Decision {decision.id} expired at "
                f"{ensure_utc(decision.expires_at).isoformat()}; refusing to execute "
                f"a stale authorization."
            )

    def _reserve_oob_authority(self, decision: GovernanceDecision) -> None:
        """Spend an L2+ OOB authorization in the persistent replay ledger.

        Called BEFORE dispatch. The ledger's PRIMARY KEY makes the claim atomic,
        so this is both the "has it been used" test and the consumption — there
        is no window between the two for a second execution to slip through.
        """
        if decision.authorization_level not in _OOB_REQUIRED_LEVELS:
            return
        if not decision.human_approval_signature:
            return
        try:
            self._oob_ledger.record_use(
                decision.id,
                decision.human_approval_signature,
                decision.human_approver_public_key_id or "",
                execution_nonce=decision.nonce,
            )
        except ReplayError as exc:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB authorization has already been used "
                f"(non-replayable)."
            ) from exc

    def _resolve_kernel_public_key(self, decision: GovernanceDecision) -> Optional[str]:
        """The public key this decision must verify against.

        An explicitly configured key wins. Otherwise, when a kernel key registry
        is supplied, the key is resolved from the decision's
        ``kernel_public_key_id`` — a field inside the signed payload, so which
        kernel signed is authenticated rather than rewritable metadata. Naming a
        different registered kernel gains an attacker nothing: they still cannot
        produce a signature for a key they do not hold. An absent or unregistered
        key id fails closed.
        """
        if self._kernel_public_key_hex is not None:
            return self._kernel_public_key_hex
        if self._kernel_key_registry is None:
            return None
        if not decision.kernel_public_key_id:
            raise ExecutionError(
                f"Decision {decision.id} names no signing kernel "
                f"(kernel_public_key_id); the verifying key cannot be resolved."
            )
        public_key_hex = self._kernel_key_registry.get(decision.kernel_public_key_id)
        if not public_key_hex:
            raise ExecutionError(
                f"Decision {decision.id} signing kernel "
                f"'{decision.kernel_public_key_id}' is not registered."
            )
        return public_key_hex

    def _verify_decision_signature(self, decision: GovernanceDecision) -> None:
        """Verify the decision was signed by the trusted Governance Kernel (Fix 2).

        Fail closed: with no kernel public key configured or resolvable, execution
        is refused unless the fabric was constructed with
        `allow_unsigned_decisions=True` (an explicit prototype escape hatch). An
        unverifiable decision is never trusted by omission.
        """
        kernel_public_key_hex = self._resolve_kernel_public_key(decision)
        if kernel_public_key_hex is None:
            if self._allow_unsigned_decisions:
                return
            raise ExecutionError(
                f"Decision {decision.id} cannot be verified: no Governance Kernel "
                f"public key is configured. Refusing to execute an unverifiable "
                f"decision (pass allow_unsigned_decisions=True only for prototypes)."
            )
        if not decision.decision_signature:
            raise ExecutionError(
                f"Decision {decision.id} is unsigned; refusing to execute "
                f"(a valid Governance Kernel signature is required)."
            )
        if not verify_signature(
            kernel_public_key_hex,
            canonical_decision_payload(decision),
            decision.decision_signature,
        ):
            raise ExecutionError(
                f"Decision {decision.id} signature is invalid — it was not "
                f"produced by the trusted Governance Kernel (possible forgery)."
            )

    @staticmethod
    def _oob_signed_message(decision: GovernanceDecision) -> str:
        """The canonical message a human approver signs.

        A delimiter-safe JSON object binding the approval to this specific
        decision, the proposal it authorizes, the authorization level, the
        approver key id, approval timestamp, and expiry — so a captured signature is not
        transferable to a different decision, proposal, level, or approver.
        """
        return json.dumps(
            {
                "_domain": "gap.oob_approval.v2",
                "decision_id": decision.id,
                "proposal_id": decision.proposal_id,
                "authorization_level": (
                    decision.authorization_level.value
                    if decision.authorization_level
                    else None
                ),
                "approver_key_id": decision.human_approver_public_key_id,
                "approved_at": (
                    decision.human_approval_timestamp.isoformat()
                    if decision.human_approval_timestamp
                    else None
                ),
                "valid_until": (
                    decision.human_approval_valid_until.isoformat()
                    if decision.human_approval_valid_until
                    else None
                ),
            },
            sort_keys=True,
        )

    def _verify_oob_authority(self, decision: GovernanceDecision) -> None:
        """
        Out-of-Band Authority Verification for L2+ authorization gates (Fix 4).

        For L2 and above, the human approver must have signed this specific
        Decision Record ID over an agent-independent channel. This verifies, in
        order (fail closed at every step):
        1. a signature, approver key id and signed approval times are present;
        2. the approval has not expired and its timing is within the decision;
        3. the approver's public key is registered (known authority), and the
           approver is permitted to authorize at this level (per-key ceiling);
        4. the signature cryptographically verifies over the canonical message
            (decision id, proposal, level, approver, approval time, expiry).

        Non-replayability is NOT tested here: it is enforced by the atomic
        reservation in :meth:`_reserve_oob_authority`, taken before dispatch. A
        separate "has it been used?" read followed by a later consume is exactly
        the check-then-act window that lets one approval be spent twice.
        """
        if decision.authorization_level not in _OOB_REQUIRED_LEVELS:
            return  # L0 and L1 do not require OOB verification

        # 1. Required cryptographic fields must be present.
        if not decision.human_approval_signature or not decision.human_approver_public_key_id:
            raise OOBVerificationError(
                f"Decision {decision.id} requires Out-of-Band Authority Verification "
                f"at {decision.authorization_level.value}: a human approval signature "
                f"and approver key id are required."
            )
        if not decision.human_approval_valid_until:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval is missing an expiry "
                f"(human_approval_valid_until)."
            )
        if not decision.human_approval_timestamp:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval is missing its signed "
                "human_approval_timestamp."
            )

        approved_at = decision.human_approval_timestamp
        valid_until = decision.human_approval_valid_until
        if any(value.tzinfo is None or value.utcoffset() is None
               for value in (approved_at, valid_until)):
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval timestamps require a timezone."
            )

        # 2. Freshness — the approval must not be expired.
        now = utcnow()
        if now >= valid_until:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval expired at "
                f"{decision.human_approval_valid_until.isoformat()}."
            )
        if approved_at > now:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval timestamp is in the future."
            )
        if approved_at < ensure_utc(decision.evaluated_at) or approved_at >= valid_until:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval timestamp is outside its "
                "decision and approval validity interval."
            )
        if decision.expires_at is not None and valid_until > ensure_utc(decision.expires_at):
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval expiry exceeds the decision expiry."
            )

        # 3. Resolve the approver's public key. An unknown key id fails closed.
        public_key_hex = self._public_key_registry.get(
            decision.human_approver_public_key_id
        )
        if not public_key_hex:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approver key "
                f"'{decision.human_approver_public_key_id}' is not registered."
            )

        # 3b. Per-key authority ceiling — an approver may not release an action
        #     above their permitted level (tier-commensurate identity assurance).
        if self._approver_max_levels is not None:
            max_level = self._approver_max_levels.get(
                decision.human_approver_public_key_id
            )
            if max_level is None:
                raise OOBVerificationError(
                    f"Decision {decision.id} approver "
                    f"'{decision.human_approver_public_key_id}' has no authority ceiling."
                )
            if _AUTH_RANK[decision.authorization_level] > _AUTH_RANK[max_level]:
                raise OOBVerificationError(
                    f"Decision {decision.id} at {decision.authorization_level.value} "
                    f"exceeds approver '{decision.human_approver_public_key_id}' "
                    f"ceiling of {max_level.value}."
                )

        # 4. Cryptographically verify the signature over the canonical message
        #    (binds decision id, proposal, level, approver, timestamp, and expiry).
        if not verify_signature(
            public_key_hex,
            self._oob_signed_message(decision),
            decision.human_approval_signature,
        ):
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval signature is invalid."
            )

    def _dispatch_action(self, action: PlannedAction) -> dict:
        """Dispatch a single action to its registered executor."""
        executor = self._executors.get(action.action_type)
        if executor is None:
            return {
                "action_type": action.action_type,
                "target": action.target,
                "success": False,
                "error": f"No executor registered for action type: {action.action_type}",
                "duration": 0.0,
            }

        start = time.monotonic()
        try:
            result_data = executor(action)
            elapsed = time.monotonic() - start
            return {
                "action_type": action.action_type,
                "target": action.target,
                "success": True,
                "data": result_data,
                "duration": round(elapsed, 3),
            }
        except Exception as e:
            elapsed = time.monotonic() - start
            return {
                "action_type": action.action_type,
                "target": action.target,
                "success": False,
                "error": str(e),
                "duration": round(elapsed, 3),
            }

    def _apply_state_changes(
        self, action: PlannedAction, result: dict
    ) -> list:
        """Apply execution results back to the world model."""
        changes = []
        target_id = action.target
        entity = self.world_model.entities.get(target_id)

        if entity:
            # Mark entity as contacted / updated
            if action.action_type in ("send_email", "route_to_human", "automated_outreach"):
                entity.properties["last_contacted"] = utcnow().isoformat()
                entity.properties["contact_method"] = action.action_type
                entity.last_updated = utcnow()
                changes.append({
                    "entity_id": target_id,
                    "field": "last_contacted",
                    "new_value": entity.properties["last_contacted"],
                    "source": action.action_type,
                })

        return changes

    # --- Mock Executors (Prototype) ---

    def _mock_send_email(self, action: PlannedAction) -> dict:
        return {"status": "sent", "message_id": f"msg_{action.target}_email"}

    def _mock_send_sms(self, action: PlannedAction) -> dict:
        return {"status": "sent", "message_id": f"msg_{action.target}_sms"}

    def _mock_query_crm(self, action: PlannedAction) -> dict:
        target = action.target
        entity = self.world_model.entities.get(target)
        if entity:
            return {"found": True, "properties": entity.properties}
        return {"found": False, "properties": {}}

    def _mock_route_to_human(self, action: PlannedAction) -> dict:
        return {
            "status": "routed",
            "queue": action.parameters.get("queue", "default"),
            "context_attached": True,
        }

    def _mock_automated_outreach(self, action: PlannedAction) -> dict:
        return {"status": "sent", "channel": "automated"}

    def _mock_direct_call(self, action: PlannedAction) -> dict:
        return {"status": "initiated", "call_id": f"call_{action.target}"}

    def _mock_update_record(self, action: PlannedAction) -> dict:
        target = action.target
        entity = self.world_model.entities.get(target)
        if entity:
            updates = action.parameters.get("updates", {})
            entity.properties.update(updates)
            entity.last_updated = utcnow()
            return {"status": "updated", "fields": list(updates.keys())}
        return {"status": "not_found"}
