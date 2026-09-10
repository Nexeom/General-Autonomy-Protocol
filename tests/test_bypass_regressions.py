"""
Two bypasses that survived the first pass of their own fixes.

Both were found by writing adversarial tests against code that had just been
hardened, which is the point: the attestation fix covered the store's own write
paths, and the single-use fix covered sequential presentation. Neither covered
the way an agent actually reaches them.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import (
    EVIDENCE_ATTESTATION_PROPERTY,
    EVIDENCE_PROPERTY,
    EntityProperties,
    EntityState,
    WorldModel,
)
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

    def test_execution_cannot_replace_audit_provenance_with_the_old_stamp(self):
        store, kernel, intent = self._setup()
        entity = store.model.entities["lead_eu_1"]
        original_provenance = dict(entity.properties[EVIDENCE_PROPERTY])
        original_attestation = dict(entity.properties[EVIDENCE_ATTESTATION_PROPERTY])
        update = _proposal(
            "forged_stamp", "update_record",
            {"updates": {
                "gdpr_consent": True,
                EVIDENCE_PROPERTY: {
                    "channel": "forged_crm",
                    "attested": True,
                    "governance_properties": ["geo", "gdpr_consent"],
                },
            }},
            1, False,
        )
        decision = self._evaluate(kernel, intent, store.model, update)
        assert decision.verdict.value == "approved"
        result = ExecutionFabric(
            store.model, kernel_public_key_hex=kernel.public_key_hex,
        ).execute(update, decision)

        assert result.success
        assert entity.properties["gdpr_consent"] is True
        assert entity.properties[EVIDENCE_PROPERTY] == original_provenance
        assert entity.properties[EVIDENCE_ATTESTATION_PROPERTY] == original_attestation
        assert not VERIFIER.attests(entity, "gdpr_consent")
        after = self._evaluate(
            kernel, intent, store.model,
            _proposal("out_after_forged_stamp", "send_email", {}, 3, True),
        )
        assert after.verdict.value == "rejected"
        assert "gdpr_consent_required" in after.violated_constraints


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

    def test_an_expired_lease_is_resumable(self, monkeypatch):
        # A process that dies mid-dispatch must not strand the authorization.
        from gap_kernel.verification.execution_ledger import ExecutionLedger

        now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        monkeypatch.setattr(
            "gap_kernel.verification.execution_ledger.utcnow", lambda: now,
        )
        ledger = ExecutionLedger(lease_seconds=30)
        ledger.begin("n2", decision_id="d1", proposal_id="p1")
        now += timedelta(seconds=30)
        from gap_kernel.verification.execution_ledger import ExecutionReplayError
        with pytest.raises(ExecutionReplayError, match="in flight"):
            ledger.begin("n2", decision_id="d1", proposal_id="p1")

        now += timedelta(microseconds=1)
        resumed = ledger.begin("n2", decision_id="d1", proposal_id="p1")
        assert resumed.resumed is True
        assert resumed.attempts == 2
        # The recovered attempt owns a fresh lease, even at the same instant.
        with pytest.raises(ExecutionReplayError, match="in flight"):
            ledger.begin("n2", decision_id="d1", proposal_id="p1")

    def test_failed_retry_is_exclusive_across_durable_ledger_connections(self, tmp_path):
        from gap_kernel.execution.fabric import ReplayExecutionError
        from gap_kernel.verification.execution_ledger import (
            STATUS_FAILED, STATUS_IN_PROGRESS, ExecutionLedger,
        )

        initial_fabric, proposal, decision = self._signed_decision()
        db_path = str(tmp_path / "executions.sqlite")
        first_ledger = ExecutionLedger(db_path)
        first_fabric = ExecutionFabric(
            initial_fabric.world_model,
            kernel_public_key_hex=initial_fabric._kernel_public_key_hex,
            execution_ledger=first_ledger,
        )

        def unavailable(action):
            raise RuntimeError("transient service failure before any side effect")

        first_fabric.register_executor("query_crm", unavailable)
        assert not first_fabric.execute(proposal, decision).success
        assert first_ledger.status(decision.nonce) == STATUS_FAILED
        first_ledger._conn.close()

        # Hold the recovered dispatch open until every competing connection has
        # attempted the same nonce. No timing race or sleep decides the result.
        entered = threading.Event()
        release = threading.Event()
        effects = []
        effects_lock = threading.Lock()

        def dispatch(action):
            with effects_lock:
                effects.append(action.target)
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("test did not release the active dispatch")
            return {"found": True}

        retry_ledger = ExecutionLedger(db_path)
        retry_fabric = ExecutionFabric(
            initial_fabric.world_model,
            kernel_public_key_hex=initial_fabric._kernel_public_key_hex,
            execution_ledger=retry_ledger,
        )
        retry_fabric.register_executor("query_crm", dispatch)

        def competing_claim():
            competing_ledger = ExecutionLedger(db_path)
            competing_fabric = ExecutionFabric(
                initial_fabric.world_model,
                kernel_public_key_hex=initial_fabric._kernel_public_key_hex,
                execution_ledger=competing_ledger,
            )

            def unwanted_dispatch(action):
                with effects_lock:
                    effects.append(action.target)
                return {"found": True}

            competing_fabric.register_executor("query_crm", unwanted_dispatch)
            try:
                return competing_fabric.execute(proposal, decision)
            except Exception as exc:
                return exc
            finally:
                competing_ledger._conn.close()

        with ThreadPoolExecutor(max_workers=5) as workers:
            running = workers.submit(retry_fabric.execute, proposal, decision)
            try:
                assert entered.wait(timeout=5)
                contenders = [workers.submit(competing_claim) for _ in range(4)]
                outcomes = [future.result(timeout=5) for future in contenders]
                assert all(isinstance(outcome, ReplayExecutionError) for outcome in outcomes)
                assert retry_ledger.status(decision.nonce) == STATUS_IN_PROGRESS
            finally:
                release.set()
            assert running.result(timeout=5).success

        assert effects == [proposal.actions[0].target]
        with pytest.raises(ReplayExecutionError, match="already been executed"):
            retry_fabric.execute(proposal, decision)
        retry_ledger._conn.close()

    def test_a_live_lease_refuses_a_second_claim(self):
        from gap_kernel.verification.execution_ledger import (
            ExecutionLedger,
            ExecutionReplayError,
        )

        ledger = ExecutionLedger(lease_seconds=3600)
        ledger.begin("n3", decision_id="d1", proposal_id="p1")
        with pytest.raises(ExecutionReplayError, match="in flight"):
            ledger.begin("n3", decision_id="d1", proposal_id="p1")


@pytest.mark.parametrize("merge_method", ["update", "ior", "setdefault"])
@pytest.mark.parametrize("existing", [False, True])
def test_bulk_merge_cannot_forge_provenance_but_carries_signed_blobs(
    merge_method, existing, evidence_authority,
):
    honest = {"channel": "crm_of_record"}
    props = EntityProperties({"gdpr_consent": False})
    if existing:
        props[EVIDENCE_PROPERTY] = honest
    blob = evidence_authority.attest("lead_eu_1", {"gdpr_consent": False})
    updates = {
        EVIDENCE_PROPERTY: {"channel": "forged", "attested": True},
        EVIDENCE_ATTESTATION_PROPERTY: blob,
        "lead_score": 91,
    }
    if merge_method == "update":
        props.update(updates)
    elif merge_method == "ior":
        props |= updates
    else:
        for key, value in updates.items():
            props.setdefault(key, value)
    assert props.get(EVIDENCE_PROPERTY) == (honest if existing else None)
    assert props[EVIDENCE_ATTESTATION_PROPERTY] == blob
    assert props["lead_score"] == 91

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
