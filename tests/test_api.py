"""Tests for the FastAPI API endpoints."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gap_kernel.api.app import create_app
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.learning.engine import LearningEngine
from gap_kernel.lineage.store import LineageStore
from gap_kernel.models.reconciler import ReconcilerConfig
from gap_kernel.world_model.store import WorldModelStore


@pytest.fixture
def client():
    """Create a test client with fresh components."""
    world_store = WorldModelStore()
    governance = GovernanceKernel()
    lineage_store = LineageStore(db_path=":memory:")
    learning = LearningEngine()
    config = ReconcilerConfig(cooldown_seconds=0)

    app = create_app(
        world_store=world_store,
        governance_kernel=governance,
        lineage_store=lineage_store,
        learning_engine=learning,
        reconciler_config=config,
    )

    return TestClient(app)


class TestIntentEndpoints:
    def test_create_intent(self, client):
        response = client.post("/intents", json={
            "objective": "Respond to leads within 10 minutes",
            "priority": 80,
            "hard_constraints": [
                {
                    "name": "gdpr_consent_required",
                    "description": "Must verify GDPR consent",
                }
            ],
            "soft_constraints": [],
            "created_by": "test_user",
        })
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["intent"]["priority"] == 80

    def test_list_intents(self, client):
        # Create two intents
        client.post("/intents", json={
            "objective": "Intent 1",
            "priority": 50,
            "created_by": "test",
        })
        client.post("/intents", json={
            "objective": "Intent 2",
            "priority": 60,
            "created_by": "test",
        })

        response = client.get("/intents")
        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_delete_intent(self, client):
        # Create then delete
        create = client.post("/intents", json={
            "objective": "To delete",
            "priority": 50,
            "created_by": "test",
        })
        intent_id = create.json()["id"]

        response = client.delete(f"/intents/{intent_id}")
        assert response.status_code == 200

        # Should be gone
        list_response = client.get("/intents")
        assert len(list_response.json()) == 0


class TestWorldStateEndpoints:
    def test_ingest_entity(self, client):
        response = client.post("/world/ingest", json={
            "entity_type": "lead",
            "entity_id": "lead_123",
            "properties": {"name": "Test", "value": 10000},
            "source": "test",
        })
        assert response.status_code == 200
        assert response.json()["status"] == "ingested"

    def test_get_entity(self, client):
        # Ingest first
        client.post("/world/ingest", json={
            "entity_type": "lead",
            "entity_id": "lead_456",
            "properties": {"name": "Test Lead"},
            "source": "test",
        })

        response = client.get("/world/entities/lead_456")
        assert response.status_code == 200
        assert response.json()["entity_id"] == "lead_456"

    def test_get_world_state(self, client):
        client.post("/world/ingest", json={
            "entity_type": "lead",
            "entity_id": "lead_789",
            "properties": {},
            "source": "test",
        })

        response = client.get("/world/state")
        assert response.status_code == 200
        assert "entities" in response.json()


class TestReconcilerEndpoints:
    def test_reconciler_status(self, client):
        response = client.get("/reconciler/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "config" in data

    def test_trigger_reconciliation(self, client):
        response = client.post("/reconciler/trigger")
        assert response.status_code == 200
        assert "cycle_count" in response.json()

    def test_get_config(self, client):
        response = client.get("/reconciler/config")
        assert response.status_code == 200
        assert "max_retry_budget" in response.json()


class TestGovernanceEndpoints:
    def test_get_policies(self, client):
        # Create an intent with constraints
        client.post("/intents", json={
            "objective": "Test",
            "priority": 50,
            "hard_constraints": [
                {"name": "test_constraint", "description": "Test"},
            ],
            "created_by": "test",
        })

        response = client.get("/governance/policies")
        assert response.status_code == 200
        assert len(response.json()) >= 1


class TestLineageEndpoints:
    def test_verify_integrity_empty(self, client):
        response = client.get("/lineage/verify")
        assert response.status_code == 200
        data = response.json()
        assert data["integrity_valid"] is True
        assert data["total_records"] == 0

    def test_get_lineage_empty(self, client):
        response = client.get("/lineage")
        assert response.status_code == 200
        assert response.json() == []


class TestLearningEndpoints:
    def test_get_heuristics_empty(self, client):
        response = client.get("/learning/heuristics")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_proposals_empty(self, client):
        response = client.get("/learning/proposals")
        assert response.status_code == 200
        assert response.json() == []


class TestFullAPIScenario:
    """End-to-end test via API calls matching Section 8.2."""

    def test_full_scenario_via_api(self, client):
        """
        Run the full Section 8.2 scenario from API calls alone.
        Validation Criterion from Phase 5.
        """
        # 1. Declare intents
        sla_response = client.post("/intents", json={
            "objective": "Respond to high-value leads within 10 minutes",
            "priority": 80,
            "hard_constraints": [
                {
                    "name": "gdpr_consent_required",
                    "description": "Must verify GDPR consent before any direct outreach to EU leads",
                },
            ],
            "soft_constraints": [
                {
                    "name": "prefer_automation",
                    "description": "Prefer automated responses over human routing",
                },
            ],
            "created_by": "jeremy",
        })
        assert sla_response.status_code == 200
        sla_intent_id = sla_response.json()["id"]

        cost_response = client.post("/intents", json={
            "objective": "Keep per-action cost below $2",
            "priority": 40,
            "soft_constraints": [
                {
                    "name": "use_lightweight_models",
                    "description": "Prefer lightweight models for routine decisions",
                },
            ],
            "created_by": "jeremy",
        })
        assert cost_response.status_code == 200

        # 2. Simulate lead state — ingest EU lead that's been waiting
        created_at = (datetime.utcnow() - timedelta(minutes=8)).isoformat()
        ingest_response = client.post("/world/ingest", json={
            "entity_type": "lead",
            "entity_id": "lead_4821",
            "properties": {
                "name": "EU High-Value Lead",
                "value": 50000,
                "geo": "EU",
                "gdpr_consent": False,
                "local_hour": 14,
                "created_at": created_at,
            },
            "source": "crm_webhook",
            "obligations": [sla_intent_id],
        })
        assert ingest_response.status_code == 200

        # 3. Trigger reconciliation
        reconcile_response = client.post("/reconciler/trigger")
        assert reconcile_response.status_code == 200
        reconcile_data = reconcile_response.json()

        # 4. Inspect lineage
        lineage_response = client.get("/lineage")
        assert lineage_response.status_code == 200

        # 5. Verify chain integrity
        integrity_response = client.get("/lineage/verify")
        assert integrity_response.status_code == 200
        assert integrity_response.json()["integrity_valid"] is True

        # 6. Check reconciler status
        status_response = client.get("/reconciler/status")
        assert status_response.status_code == 200
