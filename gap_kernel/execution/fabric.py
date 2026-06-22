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

from datetime import datetime
from typing import Callable, Dict, Optional
import time

from gap_kernel.crypto.signing import PublicKeyRegistry, verify as verify_signature
from gap_kernel.models.execution import ExecutionResult
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
    canonical_decision_payload,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.verification.oob_ledger import OOBLedger, ReplayError

# Authorization levels that require OOB verification
_OOB_REQUIRED_LEVELS = {
    AuthorizationLevel.L2,
    AuthorizationLevel.L3,
    AuthorizationLevel.L4,
}


class ExecutionError(Exception):
    """Raised when an action fails to execute."""
    pass


class OOBVerificationError(ExecutionError):
    """Raised when Out-of-Band Authority Verification fails for L2+ actions."""
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
    ):
        self.world_model = world_model
        self._executors: Dict[str, Callable] = {}
        # Persistent replay protection + approver-key trust boundary for OOB.
        # Defaults are process-local; production injects shared, durable stores.
        self._oob_ledger = oob_ledger if oob_ledger is not None else OOBLedger()
        self._public_key_registry = (
            public_key_registry if public_key_registry is not None else PublicKeyRegistry()
        )
        # Kernel public key (Fix 2). When set, the fabric verifies that every
        # decision was signed by the trusted Governance Kernel before executing,
        # so a forged or altered decision cannot drive execution. When unset
        # (open prototype default), signature verification is disabled.
        self._kernel_public_key_hex = kernel_public_key_hex
        self._register_default_executors()

    def execute(
        self,
        proposal: StrategyProposal,
        governance_decision: GovernanceDecision,
    ) -> ExecutionResult:
        """
        Execute an approved strategy proposal.

        GUARD: The decision must be signed by the trusted Governance Kernel.
        GUARD: Never execute without governance approval.
        GUARD: L2+ requires Out-of-Band Authority Verification.
        """
        # Structural boundary (Fix 2): trust only decisions the kernel signed.
        self._verify_decision_signature(governance_decision)

        if governance_decision.verdict != GovernanceVerdict.APPROVED:
            raise ExecutionError(
                f"Cannot execute proposal {proposal.id}: "
                f"governance verdict is {governance_decision.verdict.value}, "
                f"not approved."
            )

        # OOB Authority Verification for L2+ authorization gates
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

        return ExecutionResult(
            proposal_id=proposal.id,
            actions_completed=completed,
            actions_failed=failed,
            success=len(failed) == 0,
            world_state_changes=state_changes,
            executed_at=datetime.utcnow(),
            execution_duration_seconds=round(elapsed, 3),
        )

    def _verify_decision_signature(self, decision: GovernanceDecision) -> None:
        """Verify the decision was signed by the trusted Governance Kernel (Fix 2).

        No-op when no kernel public key is configured (open prototype). When one
        is configured, an unsigned or invalidly-signed decision is refused — an
        in-process agent cannot forge an approval without the kernel's key.
        """
        if self._kernel_public_key_hex is None:
            return
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

        Binds the approval to this specific Decision Record ID *and* its expiry,
        so neither the decision nor the validity window can be swapped under a
        captured signature.
        """
        valid_until = (
            decision.human_approval_valid_until.isoformat()
            if decision.human_approval_valid_until
            else ""
        )
        return f"{decision.id}:{valid_until}"

    def _verify_oob_authority(self, decision: GovernanceDecision) -> None:
        """
        Out-of-Band Authority Verification for L2+ authorization gates (Fix 4).

        For L2 and above, the human approver must have signed this specific
        Decision Record ID over an agent-independent channel. This verifies, in
        order (fail closed at every step):
        1. a signature and approver key id are present;
        2. the approval has not expired (freshness);
        3. the approver's public key is registered (known authority);
        4. the signature cryptographically verifies over the decision id+expiry;
        5. the authorization has not already been consumed (persistent replay
           protection, enforced even across a restarted Execution Fabric).
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

        # 4. Cryptographically verify the signature over THIS decision's id+expiry.
        if not verify_signature(
            public_key_hex,
            self._oob_signed_message(decision),
            decision.human_approval_signature,
        ):
            raise OOBVerificationError(
                f"Decision {decision.id} OOB approval signature is invalid."
            )

        # 5. Non-replayability — consume the authorization in the persistent
        #    ledger. A reused (decision_id, signature) pair is rejected.
        try:
            self._oob_ledger.record_use(
                decision.id,
                decision.human_approval_signature,
                decision.human_approver_public_key_id,
            )
        except ReplayError as exc:
            raise OOBVerificationError(
                f"Decision {decision.id} OOB authorization has already been used "
                f"(non-replayable)."
            ) from exc

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
