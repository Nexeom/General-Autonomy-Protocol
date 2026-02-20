"""
Tests for GAP spec additions dated 2026-02-20:

- Action Type Registry
- Multi-Phase Authorization
- Output Artifact Provenance
- Structured Uncertainty
- Separation of Creation and Validation
- L0-L4 Authorization Levels
"""

import hashlib
from datetime import datetime

import pytest

from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import (
    GovernanceKernel,
    _build_uncertainty_declaration,
    _determine_auth_level,
    _determine_auth_tier,
)
from gap_kernel.models.governance import (
    ActionTypeSpec,
    AuthorizationLevel,
    GovernancePhaseResult,
    GovernanceVerdict,
    PhaseConfig,
    RiskProfile,
    UncertaintyDeclaration,
)
from gap_kernel.models.intent import (
    Constraint,
    ConstraintType,
    IntentVector,
    PolicyActivation,
)
from gap_kernel.models.lineage import ArtifactProvenance, LineageRecord
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.strategy.cga_loop import CGALoop, RuleBasedStrategyGenerator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_intent(intent_id="intent_test", with_gdpr=True):
    hard = []
    if with_gdpr:
        hard.append(Constraint(
            name="gdpr_consent_required",
            type=ConstraintType.HARD,
            description="GDPR consent must be verified before contacting EU leads",
        ))
    return IntentVector(
        id=intent_id,
        objective="Respond to inbound leads within 10 minutes",
        priority=80,
        hard_constraints=hard,
        soft_constraints=[],
        created_by="test",
        created_at=datetime.utcnow(),
    )


def _make_proposal(intent_id="intent_test", action_type="send_email", risk=3, target="lead_001"):
    return StrategyProposal(
        id="prop_test",
        intent_id=intent_id,
        attempt_number=1,
        plan_description="Test proposal",
        actions=[PlannedAction(
            action_type=action_type,
            target=target,
            parameters={},
            risk_score=risk,
        )],
        estimated_cost=0.10,
        rationale="Test",
        generated_at=datetime.utcnow(),
    )


def _make_world_state(entity_id="lead_001", geo="EU", consent=False, confidence=1.0):
    return WorldModel(
        entities={
            entity_id: EntityState(
                entity_type="lead",
                entity_id=entity_id,
                properties={
                    "geo": geo,
                    "gdpr_consent": consent,
                    "created_at": datetime.utcnow().isoformat(),
                },
                last_updated=datetime.utcnow(),
                source="test",
                confidence=confidence,
            ),
        },
        last_reconciled=datetime.utcnow(),
    )


# ===========================================================================
# TEST: Action Type Registry
# ===========================================================================

class TestActionTypeRegistry:
    """The kernel maintains a registry of action types with governance config."""

    def test_baseline_action_types_registered(self):
        """Five baseline action types from the spec are pre-registered."""
        kernel = GovernanceKernel()
        types = kernel.get_registered_action_types()
        assert "task_execution" in types
        assert "skill_modification" in types
        assert "drift_reconciliation" in types
        assert "escalation" in types
        assert "policy_proposal" in types
        assert len(types) == 5

    def test_action_type_has_governance_config(self):
        """Each action type carries risk profile, auth level, etc."""
        kernel = GovernanceKernel()
        spec = kernel.get_action_type("skill_modification")
        assert spec is not None
        assert spec.default_authorization_level == AuthorizationLevel.L2
        assert spec.risk_profile.reversibility == "partially_reversible"
        assert spec.risk_profile.impact_scope == "team"

    def test_unregistered_action_type_rejected(self):
        """Actions with unregistered types are rejected by the kernel."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(action_type="query_crm", risk=1)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
            action_type_id="nonexistent_type",
        )
        assert decision.verdict == GovernanceVerdict.REJECTED
        assert decision.rejection_reason == "unregistered_action_type"
        assert "nonexistent_type" in decision.rejection_detail

    def test_register_custom_action_type(self):
        """Registering a new action type (human-only) succeeds."""
        kernel = GovernanceKernel()
        spec = ActionTypeSpec(
            type_id="custom_report_generation",
            description="Generate a financial report",
            risk_profile=RiskProfile(
                impact_scope="org",
                reversibility="irreversible",
                blast_radius="wide",
            ),
            default_authorization_level=AuthorizationLevel.L3,
        )
        registered = kernel.register_action_type(spec, registered_by="admin_user")
        assert registered.registered_by == "admin_user"
        assert registered.registered_at is not None
        assert kernel.validate_action_type("custom_report_generation")

    def test_registered_type_accepted_in_evaluation(self):
        """A proposal with a registered action type is evaluated normally."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(action_type="route_to_human", risk=2)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
            action_type_id="task_execution",
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        assert decision.action_type_id == "task_execution"

    def test_action_type_default_level_overrides_risk(self):
        """Action type's default auth level overrides risk-based if higher."""
        kernel = GovernanceKernel()
        # skill_modification has default L2, but risk=1 would give L0
        proposal = _make_proposal(action_type="query_crm", risk=1)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
            action_type_id="skill_modification",
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        assert decision.authorization_level == AuthorizationLevel.L2


# ===========================================================================
# TEST: Multi-Phase Authorization
# ===========================================================================

class TestMultiPhaseAuthorization:
    """Multiple governance gates per action lifecycle."""

    def test_single_gate_default(self):
        """Most actions use single-gate (no phases configured)."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(action_type="route_to_human", risk=2)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
            action_type_id="task_execution",
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        assert decision.phase_results == []

    def test_multi_phase_all_pass(self):
        """Action type with phases — all pass."""
        kernel = GovernanceKernel()
        # Register a type with multi-phase
        spec = ActionTypeSpec(
            type_id="report_generation",
            description="Generate and validate a report",
            default_authorization_level=AuthorizationLevel.L1,
            phase_config=[
                PhaseConfig(phase_name="intent", required=True,
                            default_authorization_level=AuthorizationLevel.L1),
                PhaseConfig(phase_name="outcome", required=True,
                            default_authorization_level=AuthorizationLevel.L1,
                            escalation_on_deviation=True),
            ],
        )
        kernel.register_action_type(spec, "admin")

        proposal = _make_proposal(action_type="query_crm", risk=2)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
            action_type_id="report_generation",
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        assert len(decision.phase_results) == 2
        assert decision.phase_results[0].phase_name == "intent"
        assert decision.phase_results[1].phase_name == "outcome"

    def test_multi_phase_with_constraint_violation(self):
        """If a phase fails due to constraint violation, action is rejected."""
        kernel = GovernanceKernel()
        spec = ActionTypeSpec(
            type_id="outreach_with_validation",
            description="Outreach requiring multi-phase",
            default_authorization_level=AuthorizationLevel.L1,
            phase_config=[
                PhaseConfig(phase_name="intent", required=True,
                            default_authorization_level=AuthorizationLevel.L1),
                PhaseConfig(phase_name="outcome", required=True,
                            default_authorization_level=AuthorizationLevel.L2),
            ],
        )
        kernel.register_action_type(spec, "admin")

        # Proposal that violates GDPR
        proposal = _make_proposal(action_type="send_email", risk=2)
        intent = _make_intent(with_gdpr=True)
        world = _make_world_state(consent=False)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
            action_type_id="outreach_with_validation",
        )
        # Hard constraint catches it before phases even run
        assert decision.verdict == GovernanceVerdict.REJECTED

    def test_phase_conditional_escalation(self):
        """Outcome phase escalates auth level when prior phase approved."""
        kernel = GovernanceKernel()
        spec = ActionTypeSpec(
            type_id="escalating_report",
            description="Report that escalates on outcome",
            default_authorization_level=AuthorizationLevel.L0,
            phase_config=[
                PhaseConfig(phase_name="intent", required=True,
                            default_authorization_level=AuthorizationLevel.L1),
                PhaseConfig(phase_name="outcome", required=True,
                            default_authorization_level=AuthorizationLevel.L1,
                            escalation_on_deviation=True),
            ],
        )
        kernel.register_action_type(spec, "admin")

        proposal = _make_proposal(action_type="query_crm", risk=1)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
            action_type_id="escalating_report",
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        # Outcome phase should escalate to at least L2
        outcome_phase = decision.phase_results[1]
        assert outcome_phase.authorization_level == AuthorizationLevel.L2


# ===========================================================================
# TEST: Structured Uncertainty
# ===========================================================================

class TestStructuredUncertainty:
    """Every Decision Record carries an Uncertainty Declaration."""

    def test_uncertainty_on_approved_decision(self):
        """Approved decisions include structured uncertainty."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(action_type="route_to_human", risk=2)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert decision.verdict == GovernanceVerdict.APPROVED
        assert decision.uncertainty is not None
        assert isinstance(decision.uncertainty.confidence_level, float)
        assert 0.0 <= decision.uncertainty.confidence_level <= 1.0

    def test_uncertainty_on_rejected_decision(self):
        """Rejected decisions also include structured uncertainty."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(action_type="send_email", risk=3)
        intent = _make_intent(with_gdpr=True)
        world = _make_world_state(consent=False)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert decision.verdict == GovernanceVerdict.REJECTED
        assert decision.uncertainty is not None

    def test_uncertainty_captures_evidence_basis(self):
        """Evidence basis includes entity data sources."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(action_type="route_to_human", risk=2)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert len(decision.uncertainty.evidence_basis) > 0
        assert "lead_001" in decision.uncertainty.evidence_basis[0]

    def test_low_confidence_entity_creates_assumptions(self):
        """Entities with confidence < 1.0 generate assumptions."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(action_type="route_to_human", risk=2)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True, confidence=0.6)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert len(decision.uncertainty.assumptions) > 0
        assert "60%" in decision.uncertainty.assumptions[0]
        assert decision.uncertainty.confidence_level < 1.0

    def test_unknown_entity_creates_known_unknowns(self):
        """Missing entity data creates known unknowns."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(target="nonexistent_entity", action_type="route_to_human", risk=2)
        intent = _make_intent(with_gdpr=False)
        world = WorldModel(entities={}, last_reconciled=datetime.utcnow())

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert len(decision.uncertainty.known_unknowns) > 0
        assert "nonexistent_entity" in decision.uncertainty.known_unknowns[0]

    def test_uncertainty_propagates_to_lineage(self):
        """Uncertainty flows from governance decision into lineage record."""
        kernel = GovernanceKernel()
        fabric = ExecutionFabric(WorldModel(entities={}, last_reconciled=datetime.utcnow()))
        cga = CGALoop(
            governance_kernel=kernel,
            execution_fabric=fabric,
            max_attempts=3,
        )

        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)
        drift_event = {
            "entity_id": "lead_001",
            "description": "SLA drift",
            "severity": 7,
        }

        result = cga.run(
            intent=intent,
            drift_event=drift_event,
            world_state=world,
        )
        lineage = result.build_lineage_record(
            cycle_id="test_cycle",
            world_state_snapshot=world.model_dump(mode="json"),
        )
        assert lineage.uncertainty is not None


# ===========================================================================
# TEST: Output Artifact Provenance
# ===========================================================================

class TestOutputArtifactProvenance:
    """Decision Records support artifact provenance for durable outputs."""

    def test_artifact_provenance_model(self):
        """ArtifactProvenance model validates correctly."""
        provenance = ArtifactProvenance(
            artifact_id="report_q1_2026",
            artifact_type="financial_report",
            integrity_hash=hashlib.sha256(b"report content").hexdigest(),
            validation_evidence={
                "validator": "audit_agent_v2",
                "checks_passed": ["format", "accuracy", "completeness"],
                "confidence": 0.95,
            },
            validation_independent=True,
            validating_entity="audit_agent_v2",
            quality_uncertainty=UncertaintyDeclaration(
                assumptions=["Source data is from Q1 2026 financial close"],
                known_unknowns=["Late-arriving journal entries not included"],
                confidence_level=0.92,
            ),
        )
        assert provenance.artifact_id == "report_q1_2026"
        assert provenance.validation_independent is True
        assert provenance.quality_uncertainty.confidence_level == 0.92

    def test_lineage_record_with_provenance(self):
        """LineageRecord can carry artifact provenance."""
        intent = _make_intent(with_gdpr=False)
        provenance = ArtifactProvenance(
            artifact_id="artifact_001",
            artifact_type="report",
            integrity_hash="abc123",
            validation_independent=True,
        )
        record = LineageRecord(
            id="lin_test",
            cycle_id="cycle_test",
            intent=intent,
            drift_detected="test drift",
            drift_severity=5,
            world_state_snapshot={},
            proposals=[],
            governance_decisions=[],
            total_attempts=1,
            artifact_provenance=provenance,
        )
        assert record.artifact_provenance is not None
        assert record.artifact_provenance.artifact_id == "artifact_001"

    def test_lineage_record_without_provenance(self):
        """Non-artifact actions have no provenance (standard behavior)."""
        intent = _make_intent(with_gdpr=False)
        record = LineageRecord(
            id="lin_test",
            cycle_id="cycle_test",
            intent=intent,
            drift_detected="test drift",
            drift_severity=5,
            world_state_snapshot={},
            proposals=[],
            governance_decisions=[],
            total_attempts=1,
        )
        assert record.artifact_provenance is None

    def test_provenance_requires_integrity_hash(self):
        """Provenance must include an integrity hash."""
        provenance = ArtifactProvenance(
            artifact_id="art_001",
            artifact_type="report",
            integrity_hash=hashlib.sha256(b"data").hexdigest(),
        )
        assert len(provenance.integrity_hash) == 64  # SHA256 hex digest


# ===========================================================================
# TEST: L0-L4 Authorization Levels
# ===========================================================================

class TestAuthorizationLevels:
    """Graduated L0-L4 authorization alongside legacy string tiers."""

    def test_l0_for_low_risk(self):
        assert _determine_auth_level(1) == AuthorizationLevel.L0
        assert _determine_auth_level(3) == AuthorizationLevel.L0

    def test_l1_for_medium_risk(self):
        assert _determine_auth_level(4) == AuthorizationLevel.L1
        assert _determine_auth_level(5) == AuthorizationLevel.L1

    def test_l2_for_high_risk(self):
        assert _determine_auth_level(6) == AuthorizationLevel.L2
        assert _determine_auth_level(7) == AuthorizationLevel.L2

    def test_l3_for_collaborative(self):
        assert _determine_auth_level(8) == AuthorizationLevel.L3

    def test_l4_for_human_only(self):
        assert _determine_auth_level(9) == AuthorizationLevel.L4
        assert _determine_auth_level(10) == AuthorizationLevel.L4

    def test_legacy_tier_preserved(self):
        """Legacy string-based tiers still work for backward compatibility."""
        assert _determine_auth_tier(2) == "auto_execute"
        assert _determine_auth_tier(5) == "notify_proceed"
        assert _determine_auth_tier(7) == "require_approval"
        assert _determine_auth_tier(9) == "escalate"

    def test_both_levels_on_decision(self):
        """Decisions carry both L0-L4 and legacy tier."""
        kernel = GovernanceKernel()
        proposal = _make_proposal(action_type="route_to_human", risk=2)
        intent = _make_intent(with_gdpr=False)
        world = _make_world_state(consent=True)

        decision = kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
        )
        assert decision.authorization_level == AuthorizationLevel.L0
        assert decision.authorization_tier == "auto_execute"


# ===========================================================================
# TEST: Separation of Creation and Validation
# ===========================================================================

class TestSeparationOfCreationAndValidation:
    """The entity that produces an output must not be the sole validator."""

    def test_provenance_tracks_validation_independence(self):
        """ArtifactProvenance records whether validation was independent."""
        independent = ArtifactProvenance(
            artifact_id="art_001",
            artifact_type="report",
            integrity_hash="hash",
            validation_independent=True,
            validating_entity="external_auditor",
        )
        assert independent.validation_independent is True
        assert independent.validating_entity == "external_auditor"

    def test_provenance_flags_non_independent_validation(self):
        """Non-independent validation is explicitly tracked."""
        self_validated = ArtifactProvenance(
            artifact_id="art_002",
            artifact_type="report",
            integrity_hash="hash",
            validation_independent=False,
            validating_entity="producing_agent",
        )
        assert self_validated.validation_independent is False

    def test_quality_uncertainty_on_validated_artifact(self):
        """Validated artifacts carry quality uncertainty scoped to the output."""
        provenance = ArtifactProvenance(
            artifact_id="art_003",
            artifact_type="clinical_recommendation",
            integrity_hash="hash",
            validation_independent=True,
            validating_entity="review_board",
            quality_uncertainty=UncertaintyDeclaration(
                assumptions=["Patient data is current within 24 hours"],
                watch_conditions=["New lab results may change recommendation"],
                known_unknowns=["Patient medication interactions not fully mapped"],
                confidence_level=0.85,
            ),
        )
        assert provenance.quality_uncertainty is not None
        assert provenance.quality_uncertainty.confidence_level == 0.85
        assert len(provenance.quality_uncertainty.known_unknowns) == 1


# ===========================================================================
# TEST: API Endpoints for New Features
# ===========================================================================

class TestAPINewFeatures:
    """API endpoints for action type registry."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from gap_kernel.api.app import create_app
        app = create_app()
        return TestClient(app)

    def test_get_action_types(self, client):
        """GET /governance/action-types returns all registered types."""
        resp = client.get("/governance/action-types")
        assert resp.status_code == 200
        data = resp.json()
        assert "task_execution" in data
        assert "policy_proposal" in data
        assert len(data) == 5

    def test_get_single_action_type(self, client):
        """GET /governance/action-types/{id} returns a specific type."""
        resp = client.get("/governance/action-types/drift_reconciliation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type_id"] == "drift_reconciliation"
        assert data["default_authorization_level"] == "L1"

    def test_get_nonexistent_action_type(self, client):
        """GET /governance/action-types/{id} returns 404 for unknown type."""
        resp = client.get("/governance/action-types/nonexistent")
        assert resp.status_code == 404

    def test_register_custom_action_type_via_api(self, client):
        """POST /governance/action-types registers a new type."""
        resp = client.post("/governance/action-types", json={
            "type_id": "custom_analysis",
            "description": "Run a custom analysis",
            "default_authorization_level": "L2",
            "registered_by": "admin",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["type_id"] == "custom_analysis"

        # Verify it's now in the registry
        resp2 = client.get("/governance/action-types/custom_analysis")
        assert resp2.status_code == 200
