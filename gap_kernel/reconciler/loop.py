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
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from gap_kernel._time import ensure_utc, utcnow
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.corrigibility import KillSwitch
from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.self_evolution import SelfEvolutionMonitor
from gap_kernel.learning.engine import LearningEngine
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.reconciler import DampeningState, ReconcilerConfig
from gap_kernel.models.world import EntityState
from gap_kernel.governance.action_classifier import ActionTypeClassifier
from gap_kernel.strategy.cga_loop import CGALoop
from gap_kernel.world_model.store import WorldModelStore

logger = logging.getLogger("gap_kernel.reconciler")


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
        self.detected_at = utcnow()

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
            current_time = utcnow()

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
            if self._contacted_at(props) is not None:
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
                # Entity timestamps arrive from outside GAP and may be naive or
                # aware; subtracting a naive from an aware datetime raises, so
                # normalize before any arithmetic.
                created = ensure_utc(created)
            except (ValueError, TypeError, AttributeError):
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

    def _contacted_at(self, props: dict) -> Optional[datetime]:
        """When this entity was contacted, or None if it was not.

        Contact suppresses drift detection for the entity, so the evidence has to
        be evaluable: a value that cannot be read as a timestamp is not proof the
        obligation was served, and fails closed to "not contacted".
        """
        value = props.get("last_contacted")
        if not value:
            return None
        try:
            if isinstance(value, str):
                return ensure_utc(datetime.fromisoformat(value))
            if isinstance(value, datetime):
                return ensure_utc(value)
        except (ValueError, TypeError, AttributeError):
            return None
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


# Escalation statuses that still need a human: a pending rejection escalation, an
# L2+ action awaiting Out-of-Band approval, or an action held by GIM. All three
# must be listable and resolvable through the human-facing surface.
_OPEN_ESCALATION_STATUSES = {"pending", "awaiting_approval", "integrity_hold"}

# How many contained failures the loop keeps for inspection. Bounded because the
# incident log is in-process memory on a loop that runs forever.
_MAX_INCIDENTS = 200


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
        default_action_type_id: Optional[str] = None,
        action_type_classifier: Optional[ActionTypeClassifier] = None,
        kill_switch: Optional[KillSwitch] = None,
        integrity_monitor: Optional[GovernanceIntegrityMonitor] = None,
        block_on_integrity: bool = False,
        self_evolution_monitor: Optional[SelfEvolutionMonitor] = None,
    ):
        self.world_store = world_store
        self.governance = governance_kernel
        self.execution = execution_fabric
        self.lineage_store = lineage_store
        self.learning = learning_engine
        self.config = config or ReconcilerConfig()
        # Governance Integrity Monitoring on the autonomous heartbeat. ONE monitor
        # is shared across every CGA loop this reconciler spawns, so it watches the
        # whole decision stream (authorization drift, threshold-avoidance
        # decomposition spanning drift events) rather than a single cycle in
        # isolation. When block_on_integrity is on (governed deployments), an
        # action GIM flags is HELD and escalated to a human rather than executed.
        self._integrity_monitor = integrity_monitor
        self._block_on_integrity = block_on_integrity
        # Self-evolution capability-gain monitor (SA-4), shared across cycles like
        # the integrity monitor so it watches the whole self-modification stream.
        self._self_evolution_monitor = self_evolution_monitor
        # Corrigibility: GAP's autonomous heartbeat must be haltable. The same
        # human-controlled kill-switch is shared with every CGA loop this
        # reconciler spawns AND with the Execution Fabric; engaging it halts
        # autonomous drift-correction (planning and dispatch). Defaults to a
        # fresh switch so the heartbeat is never without one; engage/disengage
        # it out of band via ``reconciler.kill_switch``.
        self.kill_switch = kill_switch if kill_switch is not None else KillSwitch()
        # How the reconciler classifies its own autonomous drift-correction
        # actions into a governance action type (needed under strict typing).
        # Defaults to a drift_reconciliation classifier (escalating to a
        # more-restrictive category for sensitive ops); a flat
        # default_action_type_id sets the base. None => no classification (open).
        # (SIR governs the human intent-transfer at intent *registration*, not per
        # autonomous drift, so the reconciler's loop is not SIR-gated.)
        self._classifier = action_type_classifier
        if self._classifier is None and default_action_type_id is not None:
            self._classifier = ActionTypeClassifier(base_action_type=default_action_type_id)

        self._intents: Dict[str, IntentVector] = {}
        self._dampening: Dict[str, DampeningState] = {}
        self._drift_watcher = DriftWatcher()
        self._running = False
        self._escalation_queue: List[dict] = []
        self._incidents: List[dict] = []

    @property
    def status(self) -> str:
        """Current reconciler status."""
        return "running" if self._running else "stopped"

    @property
    def incidents(self) -> List[dict]:
        """Failures the loop contained rather than died of, plus the circuit
        breakers it tripped. A contained failure is still a failure of
        governance: it must be visible to a human, not silently absorbed."""
        return list(self._incidents)

    @property
    def circuit_broken_entities(self) -> List[str]:
        """Entities the circuit breaker has stopped reconciling. Nothing about
        them is being governed until a human calls ``reset_circuit_breaker``."""
        return sorted(
            entity_id
            for entity_id, state in self._dampening.items()
            if state.circuit_broken
        )

    @property
    def pending_escalations(self) -> List[dict]:
        """Get all pending (rejection) escalations."""
        return [e for e in self._escalation_queue if e.get("status") == "pending"]

    @property
    def open_escalations(self) -> List[dict]:
        """Every escalation still awaiting a human — a pending rejection, an L2+
        action awaiting Out-of-Band approval, or a GIM integrity_hold. These are
        what a human must see and resolve; ``pending_escalations`` alone would
        hide held / awaiting-approval items (a dead letter)."""
        return [
            e for e in self._escalation_queue
            if e.get("status") in _OPEN_ESCALATION_STATUSES
        ]

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
            current_time = utcnow()

        results = []
        intents = list(self._intents.values())

        # Scan all entities for drift. Iterate over a snapshot: an executor can
        # add or remove entities while the cycle runs, and a world model that
        # changes size mid-scan must not end the cycle.
        for entity_id, entity in list(self.world_store.model.entities.items()):
            # Check dampening
            if self._is_dampened(entity_id, current_time):
                continue

            try:
                # Run drift detection
                drift_events = self._drift_watcher.check(entity, intents, current_time)

                for drift in drift_events:
                    result = self._handle_drift(drift, intents, current_time)
                    results.append(result)
            except Exception as exc:
                # Contain the failure at the entity: one malformed entity must
                # not stop the rest of the world being governed this cycle.
                results.append(self._degrade_entity(entity_id, exc, current_time))

        self.world_store.mark_reconciled()
        return results

    def _degrade_entity(
        self, entity_id: str, exc: Exception, current_time: datetime
    ) -> dict:
        """Record a contained per-entity failure and count it against the entity.

        Counting matters: an entity that fails every cycle would otherwise be
        retried forever. Counting it as a failure lets the circuit breaker stop
        re-queueing it, and the incident is what tells a human it is unreconciled.
        """
        logger.exception("Reconciliation failed for entity %s", entity_id)
        incident = self._record_incident(
            "entity_failure", entity_id, repr(exc), current_time
        )
        self._update_dampening(entity_id, True, current_time)
        return {
            "entity_id": entity_id,
            "verdict": "degraded",
            "incident_id": incident["id"],
            "error": incident["detail"],
            "execution_success": False,
        }

    def _record_incident(
        self,
        kind: str,
        entity_id: Optional[str],
        detail: str,
        current_time: datetime,
    ) -> dict:
        """Append a contained-failure record to the bounded incident log."""
        incident = {
            "id": f"inc_{uuid4().hex[:12]}",
            "kind": kind,
            "entity_id": entity_id,
            "detail": detail,
            "at": current_time.isoformat(),
        }
        self._incidents.append(incident)
        del self._incidents[:-_MAX_INCIDENTS]
        return incident

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

        # Create CGA loop. The shared kill-switch flows into both the loop (it
        # refuses to plan when halted) and — via self.execution — the fabric
        # (it refuses to dispatch), so a halt stops the autonomous path cleanly.
        cga = CGALoop(
            governance_kernel=self.governance,
            execution_fabric=self.execution,
            max_attempts=self.config.max_retry_budget,
            action_type_classifier=self._classifier,
            kill_switch=self.kill_switch,
            integrity_monitor=self._integrity_monitor,
            block_on_integrity=self._block_on_integrity,
            self_evolution_monitor=self._self_evolution_monitor,
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

        # Update dampening state. Only a cycle that actually executed something
        # moved the entity forward; escalated, HELD (integrity_hold) and
        # awaiting_approval outcomes all leave it exactly as blocked as before,
        # so each must count toward the circuit breaker. Otherwise a blocked
        # target is re-planned every cooldown forever — appending a lineage
        # record each time — without the breaker ever tripping. A kill-switch
        # halt is a deliberate human stop rather than a failure of this entity,
        # so it is cooled down but not counted (None).
        resolved = bool(
            cga_result.execution_result is not None
            and cga_result.execution_result.success
        )
        self._update_dampening(
            drift.entity_id,
            None if cga_result.halted else not resolved,
            current_time,
        )

        # Operational learning
        self.learning.learn_from_lineage(lineage_record)

        # Route to a human: either an escalation, OR an L2+ action that governance
        # approved but that is held pending Out-of-Band approval. Without this, an
        # awaiting_approval outcome would be silently dropped (the high-stakes
        # action neither executes nor reaches a human). Dedupe: don't pile on a
        # duplicate every cycle — one open item per entity AND KIND. The kind is
        # part of the key because the kinds are different problems needing
        # different human responses: an entity already awaiting approval that GIM
        # then flags for integrity would otherwise have the hold silently dropped,
        # which is exactly the dead letter this queue exists to prevent.
        needs_human = (
            cga_result.escalated or cga_result.awaiting_approval or cga_result.integrity_hold
        )
        kind = (
            "awaiting_approval" if cga_result.awaiting_approval
            else "integrity_hold" if cga_result.integrity_hold
            else "pending"
        )
        already_open = any(
            e["entity_id"] == drift.entity_id
            and e["status"] in _OPEN_ESCALATION_STATUSES
            and e.get("kind", e["status"]) == kind
            for e in self._escalation_queue
        )
        if needs_human and not already_open:
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
                "kind": kind,
                "status": kind,
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
        self, entity_id: str, failed: Optional[bool], current_time: datetime
    ) -> None:
        """Update dampening state after processing a drift event.

        ``failed`` may be None for an outcome that says nothing about the entity
        (a human halt): the cooldown still applies, but the consecutive-failure
        count that drives the circuit breaker is left untouched, so a halt cannot
        leave an entity circuit-broken once the halt is lifted.
        """
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

        if failed is None:
            return

        if failed:
            state.consecutive_failures += 1
            if (
                state.consecutive_failures >= self.config.circuit_breaker_threshold
                and not state.circuit_broken
            ):
                state.circuit_broken = True
                state.circuit_broken_at = current_time
                logger.error(
                    "Circuit breaker tripped for entity %s after %d consecutive "
                    "failures; it is no longer being reconciled",
                    entity_id,
                    state.consecutive_failures,
                )
                self._record_incident(
                    "circuit_breaker_tripped",
                    entity_id,
                    (
                        f"{state.consecutive_failures} consecutive failures reached "
                        f"the threshold of {self.config.circuit_breaker_threshold}; "
                        f"reconciliation stopped until a human resets it"
                    ),
                    current_time,
                )
        else:
            state.consecutive_failures = 0

    def reset_circuit_breaker(self, entity_id: str, reset_by: str = "operator") -> bool:
        """Resume reconciling an entity the circuit breaker stopped.

        The breaker is otherwise a terminal state: nothing clears it, so the
        entity is never governed again for the life of the process. Returns False
        if that entity has no tripped breaker.
        """
        state = self._dampening.get(entity_id)
        if state is None or not state.circuit_broken:
            return False

        state.circuit_broken = False
        state.circuit_broken_at = None
        state.consecutive_failures = 0
        state.cooldown_until = None
        self._record_incident(
            "circuit_breaker_reset", entity_id, f"reset by {reset_by}", utcnow()
        )
        return True

    def reset_all_circuit_breakers(self, reset_by: str = "operator") -> List[str]:
        """Resume reconciling every circuit-broken entity. Returns their ids."""
        reset = [
            entity_id for entity_id in self.circuit_broken_entities
            if self.reset_circuit_breaker(entity_id, reset_by)
        ]
        return reset

    def attach_escalation_framing(
        self,
        escalation_id: str,
        *,
        preferred_option_id: str,
        option_order: List[str],
        favorably_framed: List[str],
    ) -> Optional[dict]:
        """Record how an escalation's options were PRESENTED to the human (GIM-4):
        the system's preferred option, the order the options were shown in, and
        which options were framed favorably. A deployment that renders multiple
        options for a human decision declares the framing it used here; resolving
        with the human's choice then feeds the GIM-4 escalation-framing-bias
        detector so presentation bias becomes measurable on the shipped path."""
        for esc in self._escalation_queue:
            if esc["id"] == escalation_id:
                esc["framing"] = {
                    "preferred_option_id": preferred_option_id,
                    "option_order": list(option_order),
                    "favorably_framed": list(favorably_framed),
                }
                return esc
        return None

    def resolve_escalation(
        self,
        escalation_id: str,
        resolution: str,
        resolver: str,
        chosen_option_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Resolve any OPEN escalation — a pending rejection, an awaiting-approval
        item, or a GIM integrity_hold — so a held action is not a dead letter.

        If the escalation carries framing (see ``attach_escalation_framing``) and a
        ``chosen_option_id`` is supplied, feed the GIM-4 detector: whether the human
        chose the system's preferred option, the first-listed option, and whether
        the preferred option was framed favorably — so escalation-framing bias is
        measured from real human decisions."""
        for esc in self._escalation_queue:
            if esc["id"] == escalation_id and esc["status"] in _OPEN_ESCALATION_STATUSES:
                esc["prior_status"] = esc["status"]
                esc["status"] = "resolved"
                esc["resolution"] = resolution
                esc["resolved_by"] = resolver
                esc["resolved_at"] = utcnow().isoformat()
                esc["chosen_option_id"] = chosen_option_id
                self._feed_framing_telemetry(esc, chosen_option_id)
                return esc
        return None

    def _feed_framing_telemetry(self, esc: dict, chosen_option_id: Optional[str]) -> None:
        """GIM-4: record this resolution's framing/choice on the integrity monitor.

        Skips a monitor/framing/choice that is absent, a framing dict missing any
        required key (a malformed sample must not be counted as an all-False
        observation), and a second feed for an already-fed escalation (so a
        re-opened-and-re-resolved escalation cannot double-count one decision)."""
        framing = esc.get("framing")
        if (
            self._integrity_monitor is None
            or framing is None
            or chosen_option_id is None
            or esc.get("_framing_fed")
            or not all(k in framing for k in
                       ("preferred_option_id", "option_order", "favorably_framed"))
        ):
            return
        order = framing.get("option_order") or []
        self._integrity_monitor.observe_escalation(
            preferred_chosen=(chosen_option_id == framing["preferred_option_id"]),
            first_option_chosen=(bool(order) and chosen_option_id == order[0]),
            preferred_favorably_framed=(
                framing["preferred_option_id"] in framing["favorably_framed"]
            ),
        )
        esc["_framing_fed"] = True

    def escalation_framing_bias(self):
        """The current GIM-4 escalation-framing-bias signal (or None) — surfaced
        once enough framed resolutions have been observed on the shipped path."""
        if self._integrity_monitor is None:
            return None
        return self._integrity_monitor.check_escalation_framing_bias()

    async def run_async(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Run the reconciler loop asynchronously.

        The loop stops for one reason only: the stop event. A cycle that raises
        is contained and recorded — governance that stops running is governance
        that stops governing, and it would stop silently.
        """
        self._running = True
        if stop_event is None:
            stop_event = asyncio.Event()

        try:
            while not stop_event.is_set():
                try:
                    self.reconcile_once()
                except Exception as exc:
                    logger.exception("Reconciliation cycle failed")
                    self._record_incident("cycle_failure", None, repr(exc), utcnow())
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.config.heartbeat_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            self._running = False
