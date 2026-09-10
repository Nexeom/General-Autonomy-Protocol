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
from gap_kernel.verification.execution_ledger import ExecutionLedger
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
        self.oob_ledger._conn.close()
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

    def get(self, request_id):
        with self._lock:
            row = self._db.execute("SELECT response_json FROM requests WHERE id=?",
                                   (request_id,)).fetchone()
            if row is None:
                raise GatewayError("unknown_request", 404)
            return json.loads(row[0])

    def propose(self, request: ProposalRequest):
        encoded = canonical(request.model_dump(mode="json"))
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT request_json, response_json FROM requests WHERE id=?",
                                       (request.request_id,)).fetchone()
                if row:
                    if encoded != row[0]:
                        raise GatewayError("request_id_content_mismatch")
                    self._db.execute("COMMIT")
                    return json.loads(row[1])
                response = self._evaluate_request(request)
                self._db.execute("INSERT INTO requests VALUES (?, ?, ?, ?)", (
                    request.request_id, encoded, self.config_digest, canonical(response)))
                self._db.execute("COMMIT")
                return response
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

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

    def execute(self, request_id: str, request: ExecuteRequest):
        # A single instance serializes dispatch; SQLite nonce claims additionally
        # protect independent service instances using the same durable state.
        with self._lock:
            if (self._state_dir / "halted").exists():
                raise GatewayError("gateway_halted", 403)
            row = self._db.execute("SELECT config_digest FROM requests WHERE id=?",
                                   (request_id,)).fetchone()
            response = self.get(request_id)
            if row[0] != self.config_digest:
                raise GatewayError("deployment_changed_repropose")
            if response["status"] == "completed":
                raise GatewayError("authorization_spent")
            if response["status"] == "rejected":
                raise GatewayError("request_rejected")
            proposal = StrategyProposal.model_validate(response["proposal"])
            decision = GovernanceDecision.model_validate(response["decision"])
            # Recheck current time/evidence immediately before dispatch. The
            # approval remains bound to the original, stored signed decision.
            fresh = self.kernel.evaluate_proposal(proposal=proposal, intents=[self.intent],
                                                  world_state=self.world,
                                                  action_type_id="task_execution")
            if fresh.verdict.value != "approved" or fresh.authorization_level != decision.authorization_level:
                raise GatewayError("policy_or_evidence_changed_repropose")
            if request.approval:
                decision = decision.model_copy(update=request.approval.model_dump())
            fabric = ExecutionFabric(
                self.world.model_copy(deep=True), kernel_public_key_hex=self.kernel.public_key_hex,
                public_key_registry=self._approvers,
                approver_max_levels={key: AuthorizationLevel.L2 for key in self.config.approvers},
                execution_ledger=self.execution_ledger, oob_ledger=self.oob_ledger,
            )
            # Replace both supported executors with service-owned capabilities.
            # All proposal actions were rebuilt from the trusted catalog above.
            indexes = {id(action): index for index, action in enumerate(proposal.actions)}

            def dispatch(action):
                if (self._state_dir / "halted").exists():
                    raise RuntimeError("gateway_halted")
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
            try:
                result = fabric.execute(proposal, decision)
            except OOBVerificationError as exc:
                raise GatewayError("valid_human_approval_required", 403) from exc
            except ExecutionError as exc:
                raise GatewayError("authorization_invalid_or_spent") from exc
            result_json = result.model_dump(mode="json")
            self._audit(request_id, proposal, decision, result_json)
            response.update(status="completed" if result.success else "failed", result=result_json)
            self._db.execute("UPDATE requests SET response_json=? WHERE id=?",
                             (canonical(response), request_id))
            return response
