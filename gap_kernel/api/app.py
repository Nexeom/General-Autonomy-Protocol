"""
GAP Kernel API — FastAPI endpoints.

Exposes the kernel's functionality via a REST API for:
- Intent management
- World state inspection
- Reconciler control
- Governance evaluation
- Lineage queries
- Learning management
- Escalation handling
"""

from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.learning.engine import LearningEngine
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.governance import GovernanceDecision
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.learning import OperationalHeuristic, PolicyProposal
from gap_kernel.models.lineage import LineageRecord
from gap_kernel.models.reconciler import ReconcilerConfig
from gap_kernel.models.strategy import StrategyProposal
from gap_kernel.models.world import EntityState
from gap_kernel.reconciler.loop import ReconcilerLoop
from gap_kernel.world_model.store import WorldModelStore


# --- Request/Response Models ---

class IntentCreateRequest(BaseModel):
    objective: str
    priority: int = 50
    hard_constraints: list = []
    soft_constraints: list = []
    cost_ceiling: Optional[float] = None
    created_by: str = "api_user"


class EntityIngestRequest(BaseModel):
    entity_type: str
    entity_id: str
    properties: dict
    source: str = "api"
    confidence: float = 1.0
    obligations: list = []


class EvaluateRequest(BaseModel):
    proposal: dict
    intent_ids: Optional[List[str]] = None


class ReconcilerTriggerResponse(BaseModel):
    results: list
    cycle_count: int


class EscalationResolveRequest(BaseModel):
    resolution: str
    resolver: str


class ProposalReviewRequest(BaseModel):
    reviewer: str


# --- Application Factory ---

def create_app(
    world_store: Optional[WorldModelStore] = None,
    governance_kernel: Optional[GovernanceKernel] = None,
    lineage_store: Optional[LineageStore] = None,
    learning_engine: Optional[LearningEngine] = None,
    reconciler_config: Optional[ReconcilerConfig] = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="GAP Kernel API",
        description="General Autonomy Protocol — Kernel Prototype",
        version="0.1.0-alpha",
    )

    # Initialize components
    ws = world_store or WorldModelStore()
    gk = governance_kernel or GovernanceKernel()
    ls = lineage_store or LineageStore()
    le = learning_engine or LearningEngine()
    ef = ExecutionFabric(ws.model)
    config = reconciler_config or ReconcilerConfig()

    reconciler = ReconcilerLoop(
        world_store=ws,
        governance_kernel=gk,
        execution_fabric=ef,
        lineage_store=ls,
        learning_engine=le,
        config=config,
    )

    # Store components on app state for access in endpoints
    app.state.world_store = ws
    app.state.governance_kernel = gk
    app.state.lineage_store = ls
    app.state.learning_engine = le
    app.state.execution_fabric = ef
    app.state.reconciler = reconciler

    # === INTENT MANAGEMENT ===

    @app.post("/intents", response_model=dict)
    def create_intent(req: IntentCreateRequest):
        """Declare a new intent."""
        from gap_kernel.models.intent import Constraint, ConstraintType, PolicyActivation

        intent_id = f"intent_{uuid4().hex[:12]}"

        hard = []
        for c in req.hard_constraints:
            activation = PolicyActivation(**(c.get("activation", {})))
            hard.append(Constraint(
                name=c["name"],
                type=ConstraintType.HARD,
                description=c.get("description", ""),
                activation=activation,
            ))

        soft = []
        for c in req.soft_constraints:
            activation = PolicyActivation(**(c.get("activation", {})))
            soft.append(Constraint(
                name=c["name"],
                type=ConstraintType.SOFT,
                description=c.get("description", ""),
                activation=activation,
            ))

        intent = IntentVector(
            id=intent_id,
            objective=req.objective,
            priority=req.priority,
            hard_constraints=hard,
            soft_constraints=soft,
            cost_ceiling=req.cost_ceiling,
            created_by=req.created_by,
            created_at=datetime.utcnow(),
        )

        reconciler.register_intent(intent)
        return {"id": intent_id, "intent": intent.model_dump(mode="json")}

    @app.get("/intents")
    def list_intents():
        """List all active intents."""
        return [i.model_dump(mode="json") for i in reconciler.get_intents()]

    @app.get("/intents/{intent_id}")
    def get_intent(intent_id: str):
        """Get a specific intent."""
        intents = {i.id: i for i in reconciler.get_intents()}
        if intent_id not in intents:
            raise HTTPException(404, "Intent not found")
        return intents[intent_id].model_dump(mode="json")

    @app.put("/intents/{intent_id}")
    def update_intent(intent_id: str, req: IntentCreateRequest):
        """Update an intent (human only)."""
        from gap_kernel.models.intent import Constraint, ConstraintType, PolicyActivation

        intents = {i.id: i for i in reconciler.get_intents()}
        if intent_id not in intents:
            raise HTTPException(404, "Intent not found")

        old = intents[intent_id]

        hard = []
        for c in req.hard_constraints:
            activation = PolicyActivation(**(c.get("activation", {})))
            hard.append(Constraint(
                name=c["name"],
                type=ConstraintType.HARD,
                description=c.get("description", ""),
                activation=activation,
            ))

        soft = []
        for c in req.soft_constraints:
            activation = PolicyActivation(**(c.get("activation", {})))
            soft.append(Constraint(
                name=c["name"],
                type=ConstraintType.SOFT,
                description=c.get("description", ""),
                activation=activation,
            ))

        updated = IntentVector(
            id=intent_id,
            objective=req.objective,
            priority=req.priority,
            hard_constraints=hard,
            soft_constraints=soft,
            cost_ceiling=req.cost_ceiling,
            created_by=req.created_by,
            created_at=old.created_at,
        )

        reconciler.register_intent(updated)
        return updated.model_dump(mode="json")

    @app.delete("/intents/{intent_id}")
    def delete_intent(intent_id: str):
        """Deactivate an intent."""
        reconciler.unregister_intent(intent_id)
        return {"status": "deactivated", "intent_id": intent_id}

    # === WORLD STATE ===

    @app.get("/world/state")
    def get_world_state():
        """Current world model snapshot."""
        return ws.get_state_snapshot()

    @app.get("/world/entities/{entity_id}")
    def get_entity(entity_id: str):
        """Get a specific entity's state."""
        entity = ws.get_entity(entity_id)
        if not entity:
            raise HTTPException(404, "Entity not found")
        return entity.model_dump(mode="json")

    @app.post("/world/ingest")
    def ingest_entity(req: EntityIngestRequest):
        """Manual state update (for testing)."""
        entity = EntityState(
            entity_type=req.entity_type,
            entity_id=req.entity_id,
            properties=req.properties,
            last_updated=datetime.utcnow(),
            source=req.source,
            confidence=req.confidence,
            obligations=req.obligations,
        )
        ws.upsert_entity(entity)
        return {"status": "ingested", "entity_id": req.entity_id}

    # === RECONCILER ===

    @app.get("/reconciler/status")
    def reconciler_status():
        """Current reconciler loop status."""
        return {
            "status": reconciler.status,
            "config": reconciler.config.model_dump(),
            "registered_intents": len(reconciler.get_intents()),
            "tracked_entities": len(ws.model.entities),
            "pending_escalations": len(reconciler.pending_escalations),
        }

    @app.post("/reconciler/trigger")
    def trigger_reconciliation():
        """Force a reconciliation cycle (for testing)."""
        results = reconciler.reconcile_once()
        return ReconcilerTriggerResponse(
            results=results,
            cycle_count=len(results),
        )

    @app.get("/reconciler/config")
    def get_reconciler_config():
        """Current reconciler configuration."""
        return reconciler.config.model_dump()

    @app.put("/reconciler/config")
    def update_reconciler_config(config: ReconcilerConfig):
        """Update reconciler configuration."""
        reconciler.config = config
        return config.model_dump()

    # === GOVERNANCE ===

    @app.get("/governance/policies")
    def get_policies():
        """All active policies/constraints."""
        policies = []
        for intent in reconciler.get_intents():
            for c in intent.hard_constraints + intent.soft_constraints:
                policies.append({
                    "intent_id": intent.id,
                    "constraint": c.model_dump(mode="json"),
                })
        return policies

    @app.post("/governance/evaluate")
    def evaluate_proposal(req: EvaluateRequest):
        """Manual proposal evaluation (for testing)."""
        proposal = StrategyProposal.model_validate(req.proposal)
        intents = reconciler.get_intents()
        if req.intent_ids:
            intents = [i for i in intents if i.id in req.intent_ids]
        decision = gk.evaluate_proposal(
            proposal=proposal,
            intents=intents,
            world_state=ws.model,
        )
        return decision.model_dump(mode="json")

    @app.get("/governance/decisions")
    def get_recent_decisions():
        """Recent governance decisions (from lineage)."""
        records = ls.query_recent(limit=20)
        decisions = []
        for r in records:
            for d in r.governance_decisions:
                decisions.append(d.model_dump(mode="json"))
        return decisions

    # === LINEAGE ===

    @app.get("/lineage")
    def get_lineage(limit: int = 50):
        """Recent lineage records."""
        records = ls.query_recent(limit=limit)
        return [r.model_dump(mode="json") for r in records]

    @app.get("/lineage/verify")
    def verify_lineage():
        """Verify chain integrity."""
        is_valid = ls.verify_chain_integrity()
        return {
            "integrity_valid": is_valid,
            "total_records": ls.count(),
        }

    @app.get("/lineage/escalations")
    def get_lineage_escalations():
        """All human escalations."""
        records = ls.query_escalations()
        return [r.model_dump(mode="json") for r in records]

    @app.get("/lineage/by-intent/{intent_id}")
    def get_lineage_by_intent(intent_id: str):
        """All lineage for an intent."""
        records = ls.query_by_intent(intent_id)
        return [r.model_dump(mode="json") for r in records]

    @app.get("/lineage/by-entity/{entity_id}")
    def get_lineage_by_entity(entity_id: str):
        """All lineage affecting an entity."""
        records = ls.query_by_entity(entity_id)
        return [r.model_dump(mode="json") for r in records]

    @app.get("/lineage/{cycle_id}")
    def get_lineage_by_cycle(cycle_id: str):
        """Full lineage for a reconciliation cycle."""
        records = ls.get_by_cycle(cycle_id)
        if not records:
            raise HTTPException(404, "Cycle not found")
        return [r.model_dump(mode="json") for r in records]

    # === LEARNING ===

    @app.get("/learning/heuristics")
    def get_heuristics():
        """All operational heuristics."""
        return [h.model_dump(mode="json") for h in le.get_all_heuristics()]

    @app.get("/learning/proposals")
    def get_policy_proposals():
        """Pending policy proposals."""
        return [p.model_dump(mode="json") for p in le.get_all_proposals()]

    @app.post("/learning/proposals/{proposal_id}/approve")
    def approve_policy_proposal(proposal_id: str, req: ProposalReviewRequest):
        """Human approves a policy change."""
        result = le.approve_proposal(proposal_id, req.reviewer)
        if not result:
            raise HTTPException(404, "Proposal not found or not pending")
        return result.model_dump(mode="json")

    @app.post("/learning/proposals/{proposal_id}/reject")
    def reject_policy_proposal(proposal_id: str, req: ProposalReviewRequest):
        """Human rejects a policy change."""
        result = le.reject_proposal(proposal_id, req.reviewer)
        if not result:
            raise HTTPException(404, "Proposal not found or not pending")
        return result.model_dump(mode="json")

    # === ESCALATIONS ===

    @app.get("/escalations/pending")
    def get_pending_escalations():
        """Awaiting human decision."""
        return reconciler.pending_escalations

    @app.post("/escalations/{escalation_id}/resolve")
    def resolve_escalation(escalation_id: str, req: EscalationResolveRequest):
        """Human provides guidance."""
        result = reconciler.resolve_escalation(
            escalation_id, req.resolution, req.resolver
        )
        if not result:
            raise HTTPException(404, "Escalation not found or already resolved")
        return result

    return app


# Default application instance
app = create_app()
