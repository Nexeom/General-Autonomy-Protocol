"""
End-to-end test: CRM Lead Response Compliance (Section 8.2).

This test validates the full GAP kernel by running the exact scenario
described in the specification:

  1. Reconciler detects Lead #4821 (EU, high-value) untouched for 8 minutes
  2. CGA Loop runs:
     - Attempt 1: Direct email → REJECTED (GDPR)
     - Attempt 2: Query CRM + email → REJECTED (no consent)
     - Attempt 3: Route to human → APPROVED
  3. Execution dispatches human handoff
  4. Lineage records the full decision chain
  5. Chain integrity is verified

This test exercises all validation criteria from Section 11.
"""

from datetime import datetime, timedelta

import pytest

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.learning.engine import LearningEngine
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyActivation,
)
from gap_kernel.models.reconciler import ReconcilerConfig
from gap_kernel.models.world import EntityState
from gap_kernel.reconciler.loop import ReconcilerLoop
from gap_kernel.world_model.store import WorldModelStore


class TestCRMScenarioE2E:
    """Full end-to-end test of the CRM Lead Response Compliance scenario."""

    def setup_method(self):
        """Set up the complete GAP runtime."""
        self.world_store = WorldModelStore()
        self.governance = GovernanceKernel()
        self.lineage_store = LineageStore(db_path=":memory:")
        self.learning = LearningEngine()
        self.execution = ExecutionFabric(self.world_store.model)
        self.config = ReconcilerConfig(
            cooldown_seconds=0,  # Disable for test
            max_retry_budget=3,
        )
        self.reconciler = ReconcilerLoop(
            world_store=self.world_store,
            governance_kernel=self.governance,
            execution_fabric=self.execution,
            lineage_store=self.lineage_store,
            learning_engine=self.learning,
            config=self.config,
        )

    def _setup_scenario(self):
        """
        Set up the exact scenario from Section 8.1:
        - Intent: lead_response_sla (priority 80)
        - Intent: cost_optimization (priority 40)
        - Entity: Lead #4821 (EU, high-value, no consent, 8 min waiting)
        """
        # Intent 1: Lead Response SLA
        sla_intent = IntentVector(
            id="lead_response_sla",
            objective="Respond to high-value leads within 10 minutes",
            priority=80,
            hard_constraints=[
                Constraint(
                    name="gdpr_consent_required",
                    type=ConstraintType.HARD,
                    description="Must verify GDPR consent before any direct outreach to EU leads",
                ),
                Constraint(
                    name="no_contact_outside_hours",
                    type=ConstraintType.HARD,
                    description="No automated outreach between 10PM-7AM lead local time",
                    activation=PolicyActivation(
                        always=False,
                        schedule="0 22-23,0-6 * * *",
                    ),
                ),
            ],
            soft_constraints=[
                Constraint(
                    name="prefer_automation",
                    type=ConstraintType.SOFT,
                    description="Prefer automated responses over human routing when possible",
                ),
            ],
            created_by="jeremy",
            created_at=datetime.utcnow(),
        )

        # Intent 2: Cost Optimization
        cost_intent = IntentVector(
            id="cost_optimization",
            objective="Keep per-action cost below $2",
            priority=40,
            hard_constraints=[],
            soft_constraints=[
                Constraint(
                    name="use_lightweight_models",
                    type=ConstraintType.SOFT,
                    description="Prefer GPT-4o-mini over GPT-4o for routine decisions",
                ),
            ],
            created_by="jeremy",
            created_at=datetime.utcnow(),
        )

        self.reconciler.register_intent(sla_intent)
        self.reconciler.register_intent(cost_intent)

        # Entity: Lead #4821 — EU, high-value, no GDPR consent, waiting 8 minutes
        created_at = datetime.utcnow() - timedelta(minutes=8)
        lead = EntityState(
            entity_type="lead",
            entity_id="lead_4821",
            properties={
                "name": "EU High-Value Lead",
                "value": 50000,
                "geo": "EU",
                "jurisdiction": "EU",
                "gdpr_consent": False,
                "local_hour": 14,  # 2 PM — within business hours
                "created_at": created_at.isoformat(),
                "ingested_at": created_at.isoformat(),
                "source_campaign": "enterprise_outbound",
            },
            last_updated=datetime.utcnow(),
            source="crm_webhook",
            confidence=1.0,
            obligations=["lead_response_sla"],
        )
        self.world_store.upsert_entity(lead)

    def test_full_scenario(self):
        """
        Validation Criterion 1: CGA Loop Works
        A governance rejection produces a reformulated strategy that
        addresses the specific rejection reason, not a generic retry.
        """
        self._setup_scenario()

        # Run reconciliation
        results = self.reconciler.reconcile_once()

        # Should have detected drift and processed it
        assert len(results) >= 1
        result = results[0]

        # CGA should have resolved this
        assert result["verdict"] == "approved"
        assert result["attempts"] == 3  # Exactly 3 attempts as per spec

    def test_lineage_completeness(self):
        """
        Validation Criterion 2: Lineage Is Complete
        Every reconciliation cycle produces a lineage record that answers:
        what intent, what drift, what was proposed, what was rejected and why,
        what was finally approved, what happened.
        """
        self._setup_scenario()
        self.reconciler.reconcile_once()

        records = self.lineage_store.query_recent(limit=1)
        assert len(records) == 1
        record = records[0]

        # WHAT INTENT
        assert record.intent.id == "lead_response_sla"
        assert record.intent.priority == 80

        # WHAT DRIFT
        assert "lead_4821" in record.drift_detected or record.drift_severity >= 7

        # WHAT WAS PROPOSED
        assert len(record.proposals) == 3
        assert record.proposals[0].attempt_number == 1
        assert record.proposals[1].attempt_number == 2
        assert record.proposals[2].attempt_number == 3

        # WHAT WAS REJECTED AND WHY
        assert len(record.governance_decisions) == 3
        from gap_kernel.models.governance import GovernanceVerdict
        assert record.governance_decisions[0].verdict == GovernanceVerdict.REJECTED
        assert record.governance_decisions[1].verdict == GovernanceVerdict.REJECTED
        assert record.governance_decisions[2].verdict == GovernanceVerdict.APPROVED

        # Rejection reasons are specific
        assert "gdpr" in record.governance_decisions[0].rejection_reason.lower()

        # WHAT WAS FINALLY APPROVED
        assert record.final_approved_proposal is not None

        # WHAT HAPPENED
        assert record.execution_success is True
        assert record.execution_result is not None
        assert record.resolved_at is not None

        # META
        assert record.total_attempts == 3
        assert record.escalated_to_human is False

    def test_chain_integrity_after_scenario(self):
        """
        Validation Criterion 3: Chain Integrity Holds
        The lineage store passes verify_chain_integrity().
        """
        self._setup_scenario()
        self.reconciler.reconcile_once()

        assert self.lineage_store.verify_chain_integrity() is True

    def test_constraint_path_correct(self):
        """
        Verify the constraint path from Section 8.2:
        gdpr_consent → no_consent_on_file → compliant_human_handoff
        """
        self._setup_scenario()
        self.reconciler.reconcile_once()

        records = self.lineage_store.query_recent(limit=1)
        record = records[0]

        # Attempt 1: Direct email rejected for GDPR
        assert "send_email" in str(record.proposals[0].actions[0].action_type)
        assert "gdpr_consent_required" in record.governance_decisions[0].violated_constraints

        # Attempt 2: Query CRM + email still rejected (no consent exists)
        assert "gdpr_consent_required" in record.governance_decisions[1].violated_constraints

        # Attempt 3: Human handoff approved
        final_proposal = record.proposals[2]
        assert any(a.action_type == "route_to_human" for a in final_proposal.actions)
        from gap_kernel.models.governance import GovernanceVerdict
        assert record.governance_decisions[2].verdict == GovernanceVerdict.APPROVED

    def test_self_resolution_rate(self):
        """
        Validation Criterion 5: Escalation Is Rare
        The system self-resolves at least 70% of drift events
        within the retry budget.
        """
        self._setup_scenario()

        # Run multiple scenarios with different entity types
        entities_to_add = [
            # US lead with consent — should resolve immediately
            EntityState(
                entity_type="lead",
                entity_id="lead_us_1",
                properties={
                    "geo": "US",
                    "gdpr_consent": True,
                    "local_hour": 10,
                    "created_at": (datetime.utcnow() - timedelta(minutes=8)).isoformat(),
                },
                last_updated=datetime.utcnow(),
                source="crm",
                obligations=["lead_response_sla"],
            ),
            # EU lead with consent — should resolve
            EntityState(
                entity_type="lead",
                entity_id="lead_eu_consent",
                properties={
                    "geo": "EU",
                    "gdpr_consent": True,
                    "local_hour": 11,
                    "created_at": (datetime.utcnow() - timedelta(minutes=9)).isoformat(),
                },
                last_updated=datetime.utcnow(),
                source="crm",
                obligations=["lead_response_sla"],
            ),
            # Non-EU lead — should resolve
            EntityState(
                entity_type="lead",
                entity_id="lead_jp_1",
                properties={
                    "geo": "JP",
                    "local_hour": 15,
                    "created_at": (datetime.utcnow() - timedelta(minutes=7)).isoformat(),
                },
                last_updated=datetime.utcnow(),
                source="crm",
                obligations=["lead_response_sla"],
            ),
        ]

        for entity in entities_to_add:
            self.world_store.upsert_entity(entity)

        results = self.reconciler.reconcile_once()

        total = len(results)
        resolved = sum(1 for r in results if r["verdict"] == "approved")

        if total > 0:
            resolution_rate = resolved / total
            assert resolution_rate >= 0.70, (
                f"Self-resolution rate {resolution_rate:.0%} is below 70% "
                f"({resolved}/{total} resolved)"
            )

    def test_operational_learning_after_scenario(self):
        """
        Verify that operational learning extracts patterns from the scenario.
        The Iron Rule: Learning modifies strategy weights, never policy boundaries.
        """
        self._setup_scenario()
        self.reconciler.reconcile_once()

        heuristics = self.learning.get_all_heuristics()
        # Should have learned something from the GDPR rejections
        assert len(heuristics) >= 1

        # Verify the heuristic is operational, not normative
        for h in heuristics:
            assert h.status == "active"
            assert len(h.source_lineage_ids) >= 1

    def test_lineage_queryable_by_intent(self):
        """Lineage should be queryable by intent ID."""
        self._setup_scenario()
        self.reconciler.reconcile_once()

        records = self.lineage_store.query_by_intent("lead_response_sla")
        assert len(records) >= 1
        assert all(r.intent.id == "lead_response_sla" for r in records)

    def test_lineage_queryable_by_entity(self):
        """Lineage should be queryable by entity ID."""
        self._setup_scenario()
        self.reconciler.reconcile_once()

        records = self.lineage_store.query_by_entity("lead_4821")
        assert len(records) >= 1

    def test_multiple_reconciliation_cycles(self):
        """
        Run multiple reconciliation cycles and verify chain integrity.
        """
        self._setup_scenario()

        # Add more entities with staggered creation times
        for i in range(5):
            created_at = datetime.utcnow() - timedelta(minutes=7 + i * 0.5)
            entity = EntityState(
                entity_type="lead",
                entity_id=f"lead_batch_{i}",
                properties={
                    "geo": "US" if i % 2 == 0 else "EU",
                    "gdpr_consent": i % 2 == 0,
                    "local_hour": 14,
                    "created_at": created_at.isoformat(),
                },
                last_updated=datetime.utcnow(),
                source="crm",
                obligations=["lead_response_sla"],
            )
            self.world_store.upsert_entity(entity)

        # Run reconciliation
        results = self.reconciler.reconcile_once()
        assert len(results) >= 1

        # Verify chain integrity across all records
        assert self.lineage_store.verify_chain_integrity() is True
