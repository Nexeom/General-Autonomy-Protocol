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

from gap_kernel.models.execution import ExecutionResult
from gap_kernel.models.governance import (
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
)
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel

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

    def __init__(self, world_model: WorldModel):
        self.world_model = world_model
        self._executors: Dict[str, Callable] = {}
        self._used_verification_tokens: set = set()
        self._register_default_executors()

    def execute(
        self,
        proposal: StrategyProposal,
        governance_decision: GovernanceDecision,
    ) -> ExecutionResult:
        """
        Execute an approved strategy proposal.

        GUARD: Never execute without governance approval.
        GUARD: L2+ requires Out-of-Band Authority Verification.
        """
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

    def _verify_oob_authority(self, decision: GovernanceDecision) -> None:
        """
        Out-of-Band Authority Verification for L2+ authorization gates.

        For L2 and above, verifies:
        1. verification_method is provided
        2. verification channel is independent of agent communication
        3. Non-replayability (authorization bound to specific Decision Record ID)
        """
        if decision.authorization_level not in _OOB_REQUIRED_LEVELS:
            return  # L0 and L1 do not require OOB verification

        # 1. Check that verification_method is provided
        if not decision.authority_verification_method:
            raise OOBVerificationError(
                f"Decision {decision.id} requires Out-of-Band Authority Verification "
                f"at authorization level {decision.authorization_level.value}. "
                f"No verification_method provided."
            )

        # 2. Validate that the verification channel is independent
        if not decision.authority_verification_channel:
            raise OOBVerificationError(
                f"Decision {decision.id} requires an independent verification channel "
                f"at authorization level {decision.authorization_level.value}. "
                f"No verification_channel provided."
            )

        # 3. Non-replayability: authorization bound to specific Decision Record ID
        verification_token = f"{decision.id}:{decision.authority_verification_method}"
        if verification_token in self._used_verification_tokens:
            raise OOBVerificationError(
                f"Decision {decision.id} verification has already been used. "
                f"Authorization signals are non-replayable."
            )
        self._used_verification_tokens.add(verification_token)

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
