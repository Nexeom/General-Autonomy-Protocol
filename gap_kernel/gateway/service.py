"""Trusted service owns policy, keys, evidence, ledgers AND tool dispatch.

This is a deliberately small two-tool reference gateway. The HTTP API does not
accept an executor, arbitrary URL, world state, risk score, policy, decision, or
credential. Isolation requires the documented container/OS deployment.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import ValidationError

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry
from gap_kernel.execution.fabric import ExecutionError, ExecutionFabric, OOBVerificationError
from gap_kernel.gateway.models import (
    ExecuteRequest, GatewayConfig, LookupArguments, NotifyArguments, ProposalRequest,
)
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.governance import AuthorizationLevel, GovernanceDecision
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.lineage import LineageRecord
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import WorldModel
from gap_kernel.service.kernel_server import load_trust_root
from gap_kernel.verification.execution_ledger import (
    ExecutionLedger, STATUS_COMPLETE, STATUS_IN_PROGRESS,
)
from gap_kernel.verification.oob_ledger import OOBLedger


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class GatewayError(Exception):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class GatewayService:
    def __init__(self, config_file: str | Path, state_dir: str | Path):
        config_file = Path(config_file).resolve()
        self.config = GatewayConfig.model_validate_json(config_file.read_text(encoding="utf-8"))
        base = config_file.parent

        def resolve(value):
            return base / value

        root = load_trust_root(str(resolve(self.config.trust_root)))
        private, public = root.load_kernel_identity()
        self.agent_token = resolve(self.config.agent_token_file).read_text().strip()
        self._tool_token = resolve(self.config.tool_token_file).read_text().strip()
        if min(len(self.agent_token), len(self._tool_token)) < 32:
            raise ValueError("gateway tokens must have at least 32 characters")
        profile = ApplicabilityProfile.model_validate_json(resolve(self.config.profile).read_text())
        self.world = WorldModel.model_validate_json(resolve(self.config.world).read_text())
        self.intent = IntentVector.model_validate_json(resolve(self.config.intent).read_text())
        self.kernel = GovernanceKernel(
            governed=True, applicability_profile=profile,
            profile_key_registry=root.profile_key_registry(),
            evidence_issuers=root.evidence_issuer_registry(),
            signing_key_hex=private, public_key_hex=public,
            kernel_key_id="gateway", decision_ttl_seconds=240,
        )
        if self.kernel.public_key_hex != public:
            raise ValueError("kernel identity does not match trust root")
        self._approvers = PublicKeyRegistry(self.config.approvers)
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self.execution_ledger = ExecutionLedger(str(self._state_dir / "executions.sqlite"))
        self.oob_ledger = OOBLedger(str(self._state_dir / "approvals.sqlite"))
        self.lineage = LineageStore(str(self._state_dir / "lineage.sqlite"), private, public)
        # Bind persisted requests to their original deployment policy and facts.
        # On a configuration/evidence change a fresh request and approval are
        # required; old authority cannot silently transfer to a new deployment.
        self.config_digest = hashlib.sha256(canonical({
            "config": self.config.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "world": self.world.model_dump(mode="json"),
            "intent": self.intent.model_dump(mode="json"),
            "kernel_key": public,
        }).encode()).hexdigest()
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self._state_dir / "requests.sqlite"),
                                   check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, request_json TEXT NOT NULL,
            config_digest TEXT NOT NULL, response_json TEXT NOT NULL)""")
        self._closed = False
        self._http = httpx.Client(base_url=self.config.tool_base_url,
                                  timeout=self.config.tool_timeout_seconds,
                                  follow_redirects=False, trust_env=False,
                                  headers={"Authorization": "Bearer " + self._tool_token})

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._http.close()
        self._db.close()
        self.execution_ledger.close()
        self.oob_ledger.close()
        self.lineage.close()

    def _audit(self, request_id, proposal, decision, result=None):
        self.lineage.append(LineageRecord(
            id=str(uuid4()), cycle_id=request_id, intent=self.intent,
            drift_detected="Gateway tool request", drift_severity=0,
            world_state_snapshot=self.world.model_dump(mode="json"),
            proposals=[proposal], governance_decisions=[decision],
            final_approved_proposal=proposal.id if decision.verdict.value == "approved" else None,
            execution_result=result, execution_success=bool(result and result.get("success")),
            total_attempts=1, escalated_to_human=decision.authorization_level in (
                AuthorizationLevel.L2, AuthorizationLevel.L3, AuthorizationLevel.L4),
            resolved_at=utcnow(),
        ))

    @contextmanager
    def _exclusive(self):
        """Serialize every gateway dispatch/renewal across service processes.

        SQLite releases this transaction on process death. Only after acquiring
        it may this gateway reclaim an in-progress execution without its lease.
        Tool and ledger commits intentionally survive a requests DB rollback.
        """
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise GatewayError("gateway_busy", 503) from exc
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def _stored(self, request_id):
        row = self._db.execute("SELECT response_json FROM requests WHERE id=?",
                               (request_id,)).fetchone()
        if row is None:
            raise GatewayError("unknown_request", 404)
        return json.loads(row[0])

    def _save(self, response):
        self._db.execute("UPDATE requests SET response_json=? WHERE id=?",
                         (canonical(response), response["request_id"]))

    def _context(self, response):
        return {"request_id": response["request_id"], "proposal": response["proposal"],
                "intent": self.intent.model_dump(mode="json"),
                "world_state_snapshot": self.world.model_dump(mode="json")}

    def _deliver_outcome(self, outcome):
        """Idempotently deliver an immutable journal event to signed lineage."""
        decision = GovernanceDecision.model_validate(outcome["decision"])
        event_id = f"gateway-{decision.nonce}-{outcome['attempt']}"
        context = outcome["context"]
        if context is None:
            raise ValueError("execution audit context is unavailable")
        result = outcome["result"]
        record = LineageRecord(
            id=event_id, cycle_id=context["request_id"], intent=context["intent"],
            drift_detected="Gateway tool request", drift_severity=0,
            world_state_snapshot=context["world_state_snapshot"],
            proposals=[context["proposal"]], governance_decisions=[decision],
            final_approved_proposal=decision.proposal_id,
            execution_result=result, execution_success=result["success"],
            total_attempts=outcome["attempt"],
            escalated_to_human=decision.authorization_level in (
                AuthorizationLevel.L2, AuthorizationLevel.L3, AuthorizationLevel.L4),
            resolved_at=result["executed_at"],
        )
        existing = self.lineage.get_by_id(event_id)
        if existing is not None:
            excluded = {"signature", "prior_record_hash"}
            if existing.model_dump(mode="json", exclude=excluded) != record.model_dump(
                    mode="json", exclude=excluded):
                raise ValueError("audit event identity conflicts with its immutable outcome")
            return
        self.lineage.append(record)

    def _observe_interruption(self, nonce):
        """Record uncertainty before releasing an orphaned claim; never invent effects."""
        if self.execution_ledger.status(nonce) != STATUS_IN_PROGRESS:
            return
        decision = self.execution_ledger.current_attempt(nonce)
        if decision is None:
            # Old deployments did not journal attempt authority. Do not attach
            # a new caller's approval to a historical attempt.
            return
        receipts = [value for value in self.execution_ledger.action_results(nonce).values()
                    if value is not None]
        self.execution_ledger.finish(nonce, success=False, decision=decision, result={
            "proposal_id": decision["proposal_id"], "actions_completed": receipts,
            "actions_failed": [{"success": False, "outcome_unknown": True,
                                "error": "process_interrupted_before_outcome_recorded"}],
            "success": False, "world_state_changes": [], "executed_at": utcnow().isoformat(),
            "execution_duration_seconds": 0.0, "timestamp_kind": "recovery_observation",
            "outcome_unknown": True,
        })

    def _reconcile(self, response):
        """Repair response/audit state from the ledger, without dispatching tools."""
        decision = response.get("decision")
        if not decision or not decision.get("nonce"):
            return response
        nonce = decision["nonce"]
        pending = 0
        unknown = False
        current_unknown = False
        current = []
        for recorded_nonce in [*response.get("prior_nonces", []), nonce]:
            self._observe_interruption(recorded_nonce)
            outcomes = self.execution_ledger.outcomes(recorded_nonce)
            unknown |= self.execution_ledger.status(recorded_nonce) == STATUS_IN_PROGRESS
            if recorded_nonce == nonce:
                current = outcomes
                current_unknown = self.execution_ledger.status(nonce) == STATUS_IN_PROGRESS
            for outcome in outcomes:
                unknown |= outcome["result"].get("outcome_unknown", False)
                if recorded_nonce == nonce:
                    current_unknown |= outcome["result"].get("outcome_unknown", False)
                try:
                    self._deliver_outcome(outcome)
                except (sqlite3.Error, OSError, ValueError):
                    pending += 1
        status = self.execution_ledger.status(nonce)
        if current:
            latest = current[-1]["result"]
            response.update(status="completed" if latest["success"] else "failed", result=latest)
        if status == STATUS_IN_PROGRESS:
            # No live gateway owns it while this transaction is held.
            response["status"] = "interrupted"
        if status == STATUS_COMPLETE and not current:
            # Upgrade of an old ledger: never invent missing tool receipts.
            response["status"] = "completed"
            if "result" not in response:
                response["audit_status"] = "recovery_required"
        if current or pending or response.get("prior_nonces"):
            response["audit_status"] = "pending" if pending else "recorded"
            response["pending_audit_events"] = pending
        if unknown and response["status"] != "completed":
            response["audit_status"] = "recovery_required"
            if current_unknown:
                response["status"] = "interrupted"
        self._save(response)
        return response

    def get(self, request_id):
        with self._exclusive():
            return self._reconcile(self._stored(request_id))

    def audit_status(self):
        with self._exclusive():
            pending = 0
            recovery_required = 0
            for row in self._db.execute("SELECT response_json FROM requests").fetchall():
                response = self._reconcile(json.loads(row[0]))
                pending += response.get("pending_audit_events", 0)
                recovery_required += response.get("audit_status") == "recovery_required"
            return {"records": self.lineage.count(),
                    "valid": self.lineage.verify_chain_integrity(),
                    "complete": pending == 0 and recovery_required == 0,
                    "pending_outcomes": pending, "recovery_required": recovery_required,
                    "public_key": self.lineage.public_key_hex}

    def propose(self, request: ProposalRequest):
        encoded = canonical(request.model_dump(mode="json"))
        with self._exclusive():
            row = self._db.execute("SELECT request_json, response_json FROM requests WHERE id=?",
                                   (request.request_id,)).fetchone()
            if row:
                if encoded != row[0]:
                    raise GatewayError("request_id_content_mismatch")
                return self._reconcile(json.loads(row[1]))
            response = self._evaluate_request(request)
            self._db.execute("INSERT INTO requests VALUES (?, ?, ?, ?)", (
                request.request_id, encoded, self.config_digest, canonical(response)))
            return response

    def _evaluate_request(self, request):
        if (self._state_dir / "halted").exists():
            return self._reject_request(request, "gateway_halted")
        actions = []
        cost = 0.0
        for supplied in request.actions:
            definition = self.config.tools.get(supplied.tool)
            if definition is None or supplied.target not in self.config.targets:
                return self._reject_request(request, "tool_or_target_not_allowed")
            schema = LookupArguments if definition.action_type == "query_crm" else NotifyArguments
            try:
                arguments = schema.model_validate(supplied.arguments).model_dump()
            except ValidationError:
                return self._reject_request(request, "invalid_tool_arguments")
            actions.append(PlannedAction(
                action_type=definition.action_type, target=supplied.target,
                parameters=arguments, risk_score=definition.risk_score,
                requires_consent=definition.action_type == "send_email",
                reversible=definition.action_type == "query_crm",
            ))
            cost += definition.cost
        proposal = StrategyProposal(
            id=request.request_id, intent_id=self.intent.id, attempt_number=1,
            plan_description="Allowlisted gateway tools", actions=actions,
            estimated_cost=cost, rationale="Explicit agent tool request", generated_at=utcnow(),
        )
        decision = self.kernel.evaluate_proposal(proposal=proposal, intents=[self.intent],
                                                world_state=self.world, action_type_id="task_execution")
        self._audit(request.request_id, proposal, decision)
        if decision.verdict.value != "approved":
            status = "rejected"
        elif decision.authorization_level in (AuthorizationLevel.L2, AuthorizationLevel.L3,
                                             AuthorizationLevel.L4):
            status = "awaiting_approval"
        else:
            status = "ready"
        return {"request_id": request.request_id, "status": status,
                "reason": decision.rejection_reason,
                "decision": decision.model_dump(mode="json"),
                "proposal": proposal.model_dump(mode="json")}

    def _reject_request(self, request, reason):
        self.lineage.append(LineageRecord(
            id=str(uuid4()), cycle_id=request.request_id, intent=self.intent,
            drift_detected=reason, drift_severity=0,
            world_state_snapshot={"gateway_request": request.model_dump(mode="json")},
            proposals=[], governance_decisions=[], execution_success=False,
            total_attempts=1, resolved_at=utcnow(),
        ))
        return {"request_id": request.request_id, "status": "rejected",
                "reason": reason, "decision": None}

    def _check_deployment(self, request_id):
        row = self._db.execute("SELECT config_digest FROM requests WHERE id=?",
                               (request_id,)).fetchone()
        if row[0] != self.config_digest:
            raise GatewayError("deployment_changed_repropose")

    def _check_policy(self, proposal, decision):
        fresh = self.kernel.evaluate_proposal(proposal=proposal, intents=[self.intent],
                                              world_state=self.world,
                                              action_type_id="task_execution")
        if fresh.verdict.value != "approved" or fresh.authorization_level != decision.authorization_level:
            raise GatewayError("policy_or_evidence_changed_repropose")

    def reauthorize(self, request_id: str):
        """Issue fresh authority for the SAME immutable operation and receipts.

        Only the decision stored by this service can execute. Replacing it under
        the shared writer lock supersedes old approvals; the new L2 decision
        needs a fresh human signature. The original proposal digest, hence tool
        idempotency keys, never changes. No tools run in this method.
        """
        with self._exclusive():
            response = self._reconcile(self._stored(request_id))
            self._check_deployment(request_id)
            if (self._state_dir / "halted").exists():
                raise GatewayError("gateway_halted", 403)
            if response["status"] in ("completed", "rejected"):
                raise GatewayError("request_not_renewable")
            proposal = StrategyProposal.model_validate(response["proposal"])
            fresh = self.kernel.evaluate_proposal(proposal=proposal, intents=[self.intent],
                                                  world_state=self.world,
                                                  action_type_id="task_execution")
            if fresh.verdict.value != "approved":
                raise GatewayError("policy_or_evidence_changed_repropose")
            old_nonce = response["decision"]["nonce"]
            self.execution_ledger.seed_successor(
                fresh.nonce, decision_id=fresh.id, proposal_id=proposal.id,
                completed=self.execution_ledger.action_results(old_nonce))
            response["prior_nonces"] = [*response.get("prior_nonces", []), old_nonce]
            response["decision"] = fresh.model_dump(mode="json")
            response["status"] = "awaiting_approval" if fresh.authorization_level in (
                AuthorizationLevel.L2, AuthorizationLevel.L3, AuthorizationLevel.L4) else "ready"
            response.pop("result", None)
            self.execution_ledger.set_context(fresh.nonce, self._context(response))
            self._audit(request_id, proposal, fresh)
            self._save(response)
            return response

    def execute(self, request_id: str, request: ExecuteRequest):
        with self._exclusive():
            # Recover settled results before freshness checks: expired authority
            # must not hide an effect that already completed.
            response = self._reconcile(self._stored(request_id))
            if (self._state_dir / "halted").exists():
                raise GatewayError("gateway_halted", 403)
            self._check_deployment(request_id)
            if response["status"] == "completed":
                raise GatewayError("authorization_spent")
            if response["status"] == "rejected":
                raise GatewayError("request_rejected")
            proposal = StrategyProposal.model_validate(response["proposal"])
            decision = GovernanceDecision.model_validate(response["decision"])
            self._check_policy(proposal, decision)
            if request.approval:
                decision = decision.model_copy(update=request.approval.model_dump())
            # The shared writer lock proves that no other gateway still owns an
            # old claim. Generic embedded callers retain lease-based recovery.
            self.execution_ledger.set_context(decision.nonce, self._context(response))

            def before_dispatch(action):
                if (self._state_dir / "halted").exists():
                    raise GatewayError("gateway_halted", 403)
                self._check_policy(proposal, decision)

            fabric = ExecutionFabric(
                self.world.model_copy(deep=True), kernel_public_key_hex=self.kernel.public_key_hex,
                public_key_registry=self._approvers,
                approver_max_levels={key: AuthorizationLevel.L2 for key in self.config.approvers},
                execution_ledger=self.execution_ledger, oob_ledger=self.oob_ledger,
                before_dispatch=before_dispatch,
            )
            # Replace both supported executors with service-owned capabilities.
            # All proposal actions were rebuilt from the trusted catalog above.
            indexes = {id(action): index for index, action in enumerate(proposal.actions)}

            def dispatch(action):
                index = indexes[id(action)]
                token = hashlib.sha256(f"{decision.proposal_digest}:{index}".encode()).hexdigest()
                try:
                    if action.action_type == "query_crm":
                        reply = self._http.get("/records/" + action.target)
                    else:
                        reply = self._http.post("/notify", json={
                            "idempotency_key": token, "target": action.target,
                            "message": action.parameters["message"],
                        })
                    reply.raise_for_status()
                    return reply.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise RuntimeError("tool_request_failed") from exc

            fabric.register_executor("query_crm", dispatch)
            fabric.register_executor("send_email", dispatch)
            prior_outcomes = len(self.execution_ledger.outcomes(decision.nonce))
            try:
                if self.execution_ledger.status(decision.nonce) == STATUS_IN_PROGRESS:
                    # Legacy claims lack original attempt authority. A successor
                    # keeps their uncertainty and receipts without an unsafe
                    # settle-then-reclaim window or invented approval history.
                    raise GatewayError("reauthorization_required")
                fabric.execute(proposal, decision)
            except (OOBVerificationError, ExecutionError, GatewayError) as exc:
                if len(self.execution_ledger.outcomes(decision.nonce)) > prior_outcomes:
                    # An attempt began and then stopped at a per-action guard.
                    # Return its recorded partial outcome, just like tool failure.
                    return self._reconcile(response)
                if isinstance(exc, OOBVerificationError):
                    raise GatewayError("valid_human_approval_required", 403) from exc
                if isinstance(exc, ExecutionError):
                    raise GatewayError("authorization_invalid_or_spent") from exc
                raise
            return self._reconcile(response)
