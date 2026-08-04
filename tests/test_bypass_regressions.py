"""
Two bypasses that survived the first pass of their own fixes.

Both were found by writing adversarial tests against code that had just been
hardened, which is the point: the attestation fix covered the store's own write
paths, and the single-use fix covered sequential presentation. Neither covered
the way an agent actually reaches them.
"""

import threading
from datetime import datetime

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.world_model.store import WorldModelStore
from tests.conftest import EVIDENCE_AUTHORITY

# The shared issuer (tests/conftest.py): an attested channel now signs what it
# writes, and the kernel verifies that signature against the issuer's public key.
ATTESTED_CRM = EVIDENCE_AUTHORITY.channel(
    channel_id="crm", description="system of record"
)

# The kernel builds one of these itself; a test needs one to assert on standing
# directly, since there is no longer a pure function that reads it off an entity.
VERIFIER = EVIDENCE_AUTHORITY.verifier()


def _governed_kernel() -> GovernanceKernel:
    priv, pub = generate_keypair()
    profile = sign_profile(
        ApplicabilityProfile(
            profile_id="p", tier1_constraints=[], issued_at=datetime(2026, 1, 1)
        ),
        priv,
        "regulatory_authority",
    )
    return GovernanceKernel(
        applicability_profile=profile,
        profile_key_registry=PublicKeyRegistry({"regulatory_authority": pub}),
        evidence_issuers=EVIDENCE_AUTHORITY.registry(),
    )


def _gdpr_intent() -> IntentVector:
    return IntentVector(
        id="i1",
        objective="Respond to high-value leads",
        priority=80,
        hard_constraints=[
            Constraint(
                name="gdpr_consent_required",
                type=ConstraintType.HARD,
                description="Verify GDPR consent before any direct outreach to EU leads",
            )
        ],
        soft_constraints=[],
        created_by="human",
        created_at=utcnow(),
    )


def _proposal(pid, action_type, parameters, risk, requires_consent):
    return StrategyProposal(
        id=pid,
        intent_id="i1",
        attempt_number=1,
        plan_description="d",
        actions=[
            PlannedAction(
                action_type=action_type,
                target="lead_eu_1",
                parameters=parameters,
                requires_consent=requires_consent,
                reversible=True,
                risk_score=risk,
            )
        ],
        estimated_cost=1.0,
        rationale="r",
        generated_at=utcnow(),
    )


class TestEvidenceLaunderingThroughExecution:
    """An agent must not be able to attest its own facts by acting on them.

    The Execution Fabric's `update_record` executor writes results straight onto
    `entity.properties` without going through the store, which left the old
    provenance stamp in place still vouching for a value it never saw — so an
    agent could launder its own consent in one governed, fully-signed step:
    ingest an attested `gdpr_consent=False`, run one approved `update_record`
    setting it True, and the previously-rejected outreach is approved.

    Signed Evidence Attestation closes it by construction rather than by
    bookkeeping: the signature binds the VALUE, so a write that changes the value
    leaves the attestation vouching for the old one and the kernel's value check
    fails. Restoring standing takes a new signature, which takes the issuer key.
    """

    def _setup(self):
        store = WorldModelStore()
        store.upsert_entity(
            EntityState(
                entity_type="lead",
                entity_id="lead_eu_1",
                properties={"geo": "DE", "gdpr_consent": False},
                last_updated=utcnow(),
                source="crm",
                obligations=[],
            ),
            channel=ATTESTED_CRM,
        )
        kernel = _governed_kernel()
        return store, kernel, _gdpr_intent()

    def _evaluate(self, kernel, intent, world, proposal):
        return kernel.evaluate_proposal(
            proposal=proposal,
            intents=[intent],
            world_state=world,
            action_type_id="task_execution",
        )

    def test_execution_writeback_cannot_launder_consent(self):
        store, kernel, intent = self._setup()
        world = store.model

        outreach = _proposal("out_1", "send_email", {}, 3, True)
        first = self._evaluate(kernel, intent, world, outreach)
        assert first.verdict.value == "rejected"
        assert "gdpr_consent_required" in first.violated_constraints

        # The laundering step: the agent updates the very fact that blocked it.
        update = _proposal(
            "upd_1", "update_record", {"updates": {"gdpr_consent": True}}, 1, False
        )
        decision = self._evaluate(kernel, intent, world, update)
        if decision.verdict.value == "approved":
            ExecutionFabric(
                world, kernel_public_key_hex=kernel.public_key_hex
            ).execute(proposal=update, governance_decision=decision)

        entity = world.entities["lead_eu_1"]
        assert entity.properties["gdpr_consent"] is True, (
            "the write itself is allowed; what it must lose is attested standing"
        )
        assert not VERIFIER.attests(entity, "gdpr_consent")

        after = self._evaluate(
            kernel, intent, world, _proposal("out_2", "send_email", {}, 3, True)
        )
        assert after.verdict.value == "rejected"
        assert "gdpr_consent_required" in after.violated_constraints

    def test_an_attested_channel_can_restore_standing(self):
        # De-attestation must be recoverable, or the world model would decay
        # into permanently unusable evidence after any executor write.
        store, kernel, intent = self._setup()
        world = store.model
        world.entities["lead_eu_1"].properties["gdpr_consent"] = True
        assert not VERIFIER.attests(world.entities["lead_eu_1"], "gdpr_consent")

        store.upsert_entity(
            EntityState(
                entity_type="lead",
                entity_id="lead_eu_1",
                properties={"geo": "DE", "gdpr_consent": True},
                last_updated=utcnow(),
                source="crm",
                obligations=[],
            ),
            channel=ATTESTED_CRM,
        )
        assert VERIFIER.attests(store.model.entities["lead_eu_1"], "gdpr_consent")
        approved = self._evaluate(
            kernel, intent, store.model, _proposal("out_3", "send_email", {}, 3, True)
        )
        assert approved.verdict.value == "approved"

    def test_an_ordinary_property_write_does_not_de_attest(self):
        store, _, _ = self._setup()
        entity = store.model.entities["lead_eu_1"]
        entity.properties["lead_score"] = 91
        assert VERIFIER.attests(entity, "gdpr_consent")
        assert VERIFIER.attests(entity, "geo")


class TestConcurrentReplay:
    """A single-use authorization must be single-use under concurrency.

    `begin()` was atomic, but an IN_PROGRESS row was resumable, so N threads
    presenting the same signed decision simultaneously all resumed the same row
    and all dispatched. Measured before the fix: 8 threads, one L0
    authorization, 5-6 successful executions.
    """

    def _signed_decision(self):
        world = WorldModel(
            entities={
                "e1": EntityState(
                    entity_type="lead",
                    entity_id="e1",
                    properties={"geo": "US"},
                    last_updated=utcnow(),
                    source="t",
                    obligations=[],
                )
            },
            last_reconciled=utcnow(),
        )
        kernel = GovernanceKernel()
        proposal = _proposal("p_conc", "query_crm", {}, 3, False)
        proposal.actions[0].target = "e1"
        decision = kernel.evaluate_proposal(
            proposal=proposal, intents=[], world_state=world
        )
        assert decision.verdict.value == "approved"
        fabric = ExecutionFabric(world, kernel_public_key_hex=kernel.public_key_hex)
        return fabric, proposal, decision

    def test_concurrent_presentations_execute_exactly_once(self):
        fabric, proposal, decision = self._signed_decision()
        threads_n = 8
        barrier = threading.Barrier(threads_n)
        successes = []
        lock = threading.Lock()

        def present():
            barrier.wait()
            try:
                result = fabric.execute(
                    proposal=proposal, governance_decision=decision
                )
                if result.success:
                    with lock:
                        successes.append(1)
            except Exception:
                pass

        threads = [threading.Thread(target=present) for _ in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1

    def test_a_settled_failure_is_still_resumable(self):
        # The lease must not turn a transient dispatch failure into a burned
        # authorization — that would make the fix worse than the bug.
        from gap_kernel.verification.execution_ledger import ExecutionLedger

        ledger = ExecutionLedger()
        row = ledger.begin("n1", decision_id="d1", proposal_id="p1")
        assert row.resumed is False
        ledger.finish("n1", success=False)

        resumed = ledger.begin("n1", decision_id="d1", proposal_id="p1")
        assert resumed.resumed is True
        assert resumed.attempts == 2

    def test_an_expired_lease_is_resumable(self):
        # A process that dies mid-dispatch must not strand the authorization.
        from gap_kernel.verification.execution_ledger import ExecutionLedger

        ledger = ExecutionLedger(lease_seconds=0)
        ledger.begin("n2", decision_id="d1", proposal_id="p1")
        resumed = ledger.begin("n2", decision_id="d1", proposal_id="p1")
        assert resumed.resumed is True

    def test_a_live_lease_refuses_a_second_claim(self):
        from gap_kernel.verification.execution_ledger import (
            ExecutionLedger,
            ExecutionReplayError,
        )
        import pytest

        ledger = ExecutionLedger(lease_seconds=3600)
        ledger.begin("n3", decision_id="d1", proposal_id="p1")
        with pytest.raises(ExecutionReplayError, match="in flight"):
            ledger.begin("n3", decision_id="d1", proposal_id="p1")


class TestExecutorCannotReSignItsOwnOutput:
    """A signing channel must not turn an executor's write into evidence.

    `update_from_execution` is the path an executor's output takes into the
    world model. It used to re-mint the entity's attestation whenever the
    channel carried a signer, so a deployment that wired a signing channel got
    the laundering back in one call: the executor wrote `gdpr_consent=True` and
    the same call re-signed it, certifying the agent's own output back to the
    kernel. An execution result is never a source of record.
    """

    def _store_with_attested_lead(self):
        store = WorldModelStore()
        store.upsert_entity(
            EntityState(
                entity_type="lead",
                entity_id="lead_eu_1",
                properties={"geo": "DE", "gdpr_consent": False},
                last_updated=utcnow(),
                source="crm",
                obligations=[],
            ),
            channel=ATTESTED_CRM,
        )
        return store

    def test_a_signing_channel_does_not_re_mint_on_execution_writeback(self):
        store = self._store_with_attested_lead()
        entity = store.model.entities["lead_eu_1"]
        # Positive control: the honest ingest really did produce attested evidence.
        assert VERIFIER.attests(entity, "gdpr_consent")

        store.update_from_execution(
            "lead_eu_1", {"gdpr_consent": True}, channel=ATTESTED_CRM
        )

        entity = store.model.entities["lead_eu_1"]
        assert entity.properties["gdpr_consent"] is True, "the write is still allowed"
        assert not VERIFIER.attests(entity, "gdpr_consent"), (
            "an executor re-signing its own output is the laundering path"
        )

    def test_the_source_of_record_can_still_attest_the_new_value(self):
        # De-attestation must be recoverable through the ingest path, or a
        # legitimate consent update would be permanently unusable.
        store = self._store_with_attested_lead()
        store.update_from_execution(
            "lead_eu_1", {"gdpr_consent": True}, channel=ATTESTED_CRM
        )
        store.upsert_entity(
            EntityState(
                entity_type="lead",
                entity_id="lead_eu_1",
                properties={"geo": "DE", "gdpr_consent": True},
                last_updated=utcnow(),
                source="crm",
                obligations=[],
            ),
            channel=ATTESTED_CRM,
        )
        assert VERIFIER.attests(store.model.entities["lead_eu_1"], "gdpr_consent")
