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

import json
import time
from datetime import datetime
from typing import Callable, Dict, Optional

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
    ):
        self.world_model = world_model
        self._executors: Dict[str, Callable] = {}
        # Corrigibility: when this human-controlled kill-switch is engaged, no
        # action is dispatched (checked first, fail closed).
        self._kill_switch = kill_switch
        # Persistent replay protection + approver-key trust boundary for OOB.
        # Defaults are process-local; production injects shared, durable stores.
        self._oob_ledger = oob_ledger if oob_ledger is not None else OOBLedger()
        self._public_key_registry = (
            public_key_registry if public_key_registry is not None else PublicKeyRegistry()
        )
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
        GUARD: L2+ requires Out-of-Band Authority Verification.
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

        # OOB Authority Verification for L2+ authorization gates (verify only —
        # the approval is consumed after a successful dispatch, below, so a
        # transient execution failure does not burn a valid human approval).
        self._verify_oob_authority(governance_decision)

        start_time = time.monotonic()
        completed = []
        failed = []
        state_changes = []

        for action in proposal.actions:
            result = self._dispatch_action(action)
            if result["success"]:
                completed.append(result)
                # Update world model with outcome
                changes = self._apply_state_changes(action, result)
                state_changes.extend(changes)
            else:
                failed.append(result)

        elapsed = time.monotonic() - start_time
        success = len(failed) == 0

        # Consume the OOB authorization only after a successful dispatch, so a
        # transient execution failure leaves a valid human approval usable for a
        # legitimate retry rather than permanently burning it.
        if success:
            self._consume_oob_authority(governance_decision)

        return ExecutionResult(
            proposal_id=proposal.id,
            actions_completed=completed,
            actions_failed=failed,
            success=success,
            world_state_changes=state_changes,
            executed_at=datetime.utcnow(),
            execution_duration_seconds=round(elapsed, 3),
        )

    def _consume_oob_authority(self, decision: GovernanceDecision) -> None:
        """Record-use an L2+ OOB authorization in the persistent replay ledger."""
        if decision.authorization_level not in _OOB_REQUIRED_LEVELS:
            return
        if not decision.human_approval_signature:
            return
        try:
            self._oob_ledger.record_use(
                decision.id,
                decision.human_approval_signature,
                decision.human_approver_public_key_id or "",
            )
        except ReplayError as exc:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB authorization has already been used "
                f"(non-replayable)."
            ) from exc

    def _verify_decision_signature(self, decision: GovernanceDecision) -> None:
        """Verify the decision was signed by the trusted Governance Kernel (Fix 2).

        Fail closed: with no kernel public key configured, execution is refused
        unless the fabric was constructed with `allow_unsigned_decisions=True`
        (an explicit prototype escape hatch). An unverifiable decision is never
        trusted by omission.
        """
        if self._kernel_public_key_hex is None:
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
            self._kernel_public_key_hex,
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
        approver key id, and the expiry — so a captured signature is not
        transferable to a different decision, proposal, level, or approver.
        """
        return json.dumps(
            {
                "_domain": "gap.oob_approval.v1",
                "decision_id": decision.id,
                "proposal_id": decision.proposal_id,
                "authorization_level": (
                    decision.authorization_level.value
                    if decision.authorization_level
                    else None
                ),
                "approver_key_id": decision.human_approver_public_key_id,
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
        1. a signature and approver key id are present;
        2. the approval has not expired (freshness);
        3. the approver's public key is registered (known authority), and the
           approver is permitted to authorize at this level (per-key ceiling);
        4. the signature cryptographically verifies over the canonical message
           (decision id, proposal, level, approver, expiry);
        5. the authorization has not already been consumed (persistent replay
           protection). The approval is *consumed* only after a successful
           dispatch (see _consume_oob_authority).
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

        # 2. Freshness — the approval must not be expired.
        if datetime.utcnow() > decision.human_approval_valid_until:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval expired at "
                f"{decision.human_approval_valid_until.isoformat()}."
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
        #    (binds decision id, proposal, level, approver, and expiry).
        if not verify_signature(
            public_key_hex,
            self._oob_signed_message(decision),
            decision.human_approval_signature,
        ):
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval signature is invalid."
            )

        # 5. Non-replayability — reject an already-consumed approval. Actual
        #    consumption happens after a successful dispatch (consume-on-success).
        if self._oob_ledger.has_been_used(
            decision.id, decision.human_approval_signature
        ):
            raise OOBVerificationError(
                f"Decision {decision.id} OOB authorization has already been used "
                f"(non-replayable)."
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
                entity.properties["last_contacted"] = datetime.utcnow().isoformat()
                entity.properties["contact_method"] = action.action_type
                entity.last_updated = datetime.utcnow()
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
            entity.last_updated = datetime.utcnow()
            return {"status": "updated", "fields": list(updates.keys())}
        return {"status": "not_found"}
