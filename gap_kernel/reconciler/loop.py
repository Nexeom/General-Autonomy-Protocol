"""
Reconciler Loop — the heartbeat of GAP.

Continuously monitors the World Model for drift from declared intents.
When drift is detected, triggers the CGA loop.

Tiered Observation (Cost Management):
  Tier 0: Rule-based watchers (deterministic, near-zero cost)
  Tier 1: Lightweight classifiers (reserved for production)
  Tier 2: Full cognitive reasoning (reserved for production)
  Tier 3: Adversarial validation (reserved for production)

The prototype implements Tier 0 only.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.learning.engine import LearningEngine
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.reconciler import DampeningState, ReconcilerConfig
from gap_kernel.models.world import EntityState
from gap_kernel.strategy.cga_loop import CGALoop
from gap_kernel.world_model.store import WorldModelStore


class DriftEvent:
    """A detected deviation from declared intent."""

    def __init__(
        self,
        entity_id: str,
        intent_id: str,
        description: str,
        severity: int,
        sla_remaining_minutes: Optional[float] = None,
    ):
        self.entity_id = entity_id
        self.intent_id = intent_id
        self.description = description
        self.severity = severity
        self.sla_remaining_minutes = sla_remaining_minutes
        self.detected_at = datetime.utcnow()

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "intent_id": self.intent_id,
            "description": self.description,
            "severity": self.severity,
            "sla_remaining_minutes": self.sla_remaining_minutes,
            "detected_at": self.detected_at.isoformat(),
        }


class DriftWatcher:
    """
    Tier 0: Rule-based drift watcher.
    Deterministic checks against the world model.
    """

    def __init__(self):
        self._rules: List[Callable] = []
        self._register_default_rules()

    def _register_default_rules(self) -> None:
        """Register default drift detection rules."""
        self._rules.append(self._check_sla_drift)

    def check(
        self,
        entity: EntityState,
        intents: List[IntentVector],
        current_time: Optional[datetime] = None,
    ) -> List[DriftEvent]:
        """Run all drift detection rules against an entity."""
        if current_time is None:
            current_time = datetime.utcnow()

        events = []
        for rule in self._rules:
            event = rule(entity, intents, current_time)
            if event:
                events.append(event)
        return events

    def _check_sla_drift(
        self,
        entity: EntityState,
        intents: List[IntentVector],
        current_time: datetime,
    ) -> Optional[DriftEvent]:
        """
        Check if an entity is drifting from SLA requirements.
        Tier 0 rule: simple time-based check.
        """
        props = entity.properties

        # Check if entity has an SLA-related intent
        for intent_id in entity.obligations:
            intent = next((i for i in intents if i.id == intent_id and i.active), None)
            if not intent:
                continue

            # Parse SLA from intent objective (e.g., "within 10 minutes")
            sla_minutes = self._extract_sla_minutes(intent.objective)
            if sla_minutes is None:
                continue

            # Check if entity has been contacted
            last_contacted = props.get("last_contacted")
            if last_contacted:
                continue  # Already contacted

            # Check how long the entity has been waiting
            created_str = props.get("created_at", props.get("ingested_at"))
            if not created_str:
                continue

            try:
                if isinstance(created_str, str):
                    created = datetime.fromisoformat(created_str)
                else:
                    created = created_str
            except (ValueError, TypeError):
                continue

            minutes_waiting = (current_time - created).total_seconds() / 60.0
            remaining = sla_minutes - minutes_waiting

            if minutes_waiting >= sla_minutes * 0.7:  # 70% of SLA consumed
                severity = min(10, int(8 + (minutes_waiting / sla_minutes) * 2))
                return DriftEvent(
                    entity_id=entity.entity_id,
                    intent_id=intent_id,
                    description=(
                        f"Entity {entity.entity_id} has been waiting "
                        f"{minutes_waiting:.1f} minutes. "
                        f"SLA is {sla_minutes} minutes. "
                        f"Remaining: {max(0, remaining):.1f} minutes."
                    ),
                    severity=severity,
                    sla_remaining_minutes=max(0, remaining),
                )

        return None

    def _extract_sla_minutes(self, objective: str) -> Optional[float]:
        """Extract SLA minutes from an intent objective string."""
        import re
        match = re.search(r'within\s+(\d+)\s+minutes?', objective, re.IGNORECASE)
        if match:
            return float(match.group(1))
        match = re.search(r'within\s+(\d+)\s+hours?', objective, re.IGNORECASE)
        if match:
            return float(match.group(1)) * 60
        return None


class ReconcilerLoop:
    """
    The Reconciler Loop — GAP's heartbeat.

    States:
      MONITORING → DRIFT_DETECTED → CGA_LOOP → (DISPATCH | ESCALATE) → MONITORING
    """

    def __init__(
        self,
        world_store: WorldModelStore,
        governance_kernel: GovernanceKernel,
        execution_fabric: ExecutionFabric,
        lineage_store: LineageStore,
        learning_engine: LearningEngine,
        config: Optional[ReconcilerConfig] = None,
    ):
        self.world_store = world_store
        self.governance = governance_kernel
        self.execution = execution_fabric
        self.lineage_store = lineage_store
        self.learning = learning_engine
        self.config = config or ReconcilerConfig()

        self._intents: Dict[str, IntentVector] = {}
        self._dampening: Dict[str, DampeningState] = {}
        self._drift_watcher = DriftWatcher()
        self._running = False
        self._escalation_queue: List[dict] = []

    @property
    def status(self) -> str:
        """Current reconciler status."""
        return "running" if self._running else "stopped"

    @property
    def pending_escalations(self) -> List[dict]:
        """Get all pending escalations."""
        return [e for e in self._escalation_queue if e.get("status") == "pending"]

    def register_intent(self, intent: IntentVector) -> None:
        """Register an intent for reconciliation."""
        self._intents[intent.id] = intent

    def unregister_intent(self, intent_id: str) -> None:
        """Remove an intent from reconciliation."""
        self._intents.pop(intent_id, None)

    def get_intents(self) -> List[IntentVector]:
        """Get all registered intents."""
        return list(self._intents.values())

    def reconcile_once(self, current_time: Optional[datetime] = None) -> List[dict]:
        """
        Run a single reconciliation cycle.
        Returns a list of results (one per drift event processed).
        """
        if current_time is None:
            current_time = datetime.utcnow()

        results = []
        intents = list(self._intents.values())

        # Scan all entities for drift
        for entity_id, entity in self.world_store.model.entities.items():
            # Check dampening
            if self._is_dampened(entity_id, current_time):
                continue

            # Run drift detection
            drift_events = self._drift_watcher.check(entity, intents, current_time)

            for drift in drift_events:
                result = self._handle_drift(drift, intents, current_time)
                results.append(result)

        self.world_store.mark_reconciled()
        return results

    def _handle_drift(
        self,
        drift: DriftEvent,
        intents: List[IntentVector],
        current_time: datetime,
    ) -> dict:
        """Handle a detected drift event by running the CGA loop."""
        intent = self._intents.get(drift.intent_id)
        if not intent:
            return {"drift": drift.to_dict(), "error": "Intent not found"}

        # Create CGA loop
        cga = CGALoop(
            governance_kernel=self.governance,
            execution_fabric=self.execution,
            max_attempts=self.config.max_retry_budget,
        )

        # Run CGA loop
        world_state = self.world_store.model
        cga_result = cga.run(
            intent=intent,
            drift_event=drift.to_dict(),
            world_state=world_state,
            intents=intents,
        )

        # Build and store lineage record
        cycle_id = f"cycle_{uuid4().hex[:12]}"
        lineage_record = cga_result.build_lineage_record(
            cycle_id=cycle_id,
            world_state_snapshot=self.world_store.get_state_snapshot(),
        )
        self.lineage_store.append(lineage_record)

        # Record drift event in world model
        self.world_store.record_drift_event(drift.to_dict())

        # Update dampening state
        self._update_dampening(drift.entity_id, cga_result.escalated, current_time)

        # Operational learning
        self.learning.learn_from_lineage(lineage_record)

        # Handle escalation
        if cga_result.escalated:
            escalation = {
                "id": f"esc_{uuid4().hex[:12]}",
                "cycle_id": cycle_id,
                "lineage_id": lineage_record.id,
                "intent_id": intent.id,
                "entity_id": drift.entity_id,
                "drift_description": drift.description,
                "proposals_tried": len(cga_result.proposals),
                "rejection_reasons": [
                    d.rejection_reason
                    for d in cga_result.decisions
                    if d.rejection_reason
                ],
                "status": "pending",
                "created_at": current_time.isoformat(),
            }
            self._escalation_queue.append(escalation)

        return {
            "drift": drift.to_dict(),
            "cycle_id": cycle_id,
            "lineage_id": lineage_record.id,
            "verdict": cga_result.final_verdict,
            "attempts": cga_result.total_attempts,
            "escalated": cga_result.escalated,
            "execution_success": (
                cga_result.execution_result.success
                if cga_result.execution_result
                else False
            ),
        }

    def _is_dampened(self, entity_id: str, current_time: datetime) -> bool:
        """Check if an entity is in cooldown or circuit-broken."""
        state = self._dampening.get(entity_id)
        if not state:
            return False

        if state.circuit_broken:
            return True

        if state.cooldown_until and current_time < state.cooldown_until:
            return True

        return False

    def _update_dampening(
        self, entity_id: str, failed: bool, current_time: datetime
    ) -> None:
        """Update dampening state after processing a drift event."""
        state = self._dampening.get(entity_id)
        if not state:
            state = DampeningState(
                entity_id=entity_id,
                last_intervention_at=current_time,
            )
            self._dampening[entity_id] = state

        state.last_intervention_at = current_time
        state.cooldown_until = current_time + timedelta(
            seconds=self.config.cooldown_seconds
        )

        if failed:
            state.consecutive_failures += 1
            if state.consecutive_failures >= self.config.circuit_breaker_threshold:
                state.circuit_broken = True
        else:
            state.consecutive_failures = 0

    def resolve_escalation(
        self, escalation_id: str, resolution: str, resolver: str
    ) -> Optional[dict]:
        """Resolve a pending escalation."""
        for esc in self._escalation_queue:
            if esc["id"] == escalation_id and esc["status"] == "pending":
                esc["status"] = "resolved"
                esc["resolution"] = resolution
                esc["resolved_by"] = resolver
                esc["resolved_at"] = datetime.utcnow().isoformat()
                return esc
        return None

    async def run_async(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Run the reconciler loop asynchronously."""
        self._running = True
        if stop_event is None:
            stop_event = asyncio.Event()

        try:
            while not stop_event.is_set():
                self.reconcile_once()
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.config.heartbeat_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            self._running = False
