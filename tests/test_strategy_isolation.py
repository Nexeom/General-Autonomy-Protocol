"""Strategy-layer isolation — the generator is untrusted, and monitors see the
WHOLE proposal.

Two leaks the CGA loop has to close:

  * The generator is deployment-supplied strategy code, yet it was handed the
    *live* governance inputs. That is one-step consent laundering with no action
    executed at all: flip ``gdpr_consent`` on the world model (or drop a hard
    constraint off the intent) inside ``generate``, and the very next kernel
    evaluation reads the mutation and approves.
  * The integrity and self-evolution monitors were told only about
    ``proposal.actions[0]``, so every action after the first was invisible to
    per-target detection — an agent packs the sensitive action second and it is
    never observed.
"""

from datetime import timedelta

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import PublicKeyRegistry, generate_keypair, sign
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.governance.integrity_monitor import GovernanceIntegrityMonitor
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.self_evolution import SelfEvolutionMonitor
from gap_kernel.models.governance import GovernanceVerdict
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import PlannedAction, StrategyProposal
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.strategy.cga_loop import CGALoop

DRIFT = {"entity_id": "eu_lead", "description": "untouched", "severity": 8}


def _eu_world(consent: bool) -> WorldModel:
    return WorldModel(
        entities={
            "eu_lead": EntityState(
                entity_type="lead",
                entity_id="eu_lead",
                properties={"geo": "EU", "gdpr_consent": consent, "local_hour": 14},
                last_updated=utcnow(),
                source="crm",
            )
        },
        last_reconciled=utcnow(),
    )


def _empty_world() -> WorldModel:
    return WorldModel(entities={}, last_reconciled=utcnow())


def _gdpr_intent() -> IntentVector:
    return IntentVector(
        id="i1",
        objective="contact high-value EU leads",
        priority=80,
        hard_constraints=[
            Constraint(
                name="gdpr_consent_required",
                type=ConstraintType.HARD,
                description="Must verify GDPR consent before contacting EU leads",
            )
        ],
        soft_constraints=[],
        created_by="t",
        created_at=utcnow(),
    )


def _plain_intent() -> IntentVector:
    return IntentVector(
        id="i1", objective="o", priority=50, hard_constraints=[], soft_constraints=[],
        created_by="t", created_at=utcnow(),
    )


class _Generator:
    """Baseline generator: reports what it was shown, mutates nothing."""

    def __init__(self, actions, risk: int = 1, parameters=None):
        self.actions = list(actions)
        self.risk = risk
        self.parameters = parameters or {}
        self.seen_consent = None
        self.seen_hard_constraints = None
        self._n = 0

    def generate(self, intent, world_state, drift_event, accumulated_constraints,
                 prior_proposals, attempt_number):
        self._n += 1
        entity = world_state.entities.get("eu_lead")
        if entity is not None:
            self.seen_consent = entity.properties["gdpr_consent"]
        self.seen_hard_constraints = [c.name for c in intent.hard_constraints]
        self.mutate(intent, world_state)
        return StrategyProposal(
            id=f"prop_{self._n}",
            intent_id=intent.id,
            attempt_number=attempt_number,
            plan_description="plan",
            actions=[
                PlannedAction(
                    action_type=at, target=tg, parameters=dict(self.parameters),
                    risk_score=self.risk,
                )
                for at, tg in self.actions
            ],
            estimated_cost=0.01,
            rationale="r",
            generated_at=utcnow(),
        )

    def mutate(self, intent, world_state) -> None:
        pass


class _ConsentLaunderer(_Generator):
    """Grants itself the consent the hard constraint requires."""

    def mutate(self, intent, world_state) -> None:
        world_state.entities["eu_lead"].properties["gdpr_consent"] = True


class _ConstraintStripper(_Generator):
    """Deletes the hard constraint standing between it and approval."""

    def mutate(self, intent, world_state) -> None:
        intent.hard_constraints.clear()


def _loop(generator, world, **kw):
    kernel = GovernanceKernel()
    fabric = ExecutionFabric(world, kernel_public_key_hex=kernel.public_key_hex)
    kw.setdefault("max_attempts", 1)
    return CGALoop(kernel, fabric, strategy_generator=generator, **kw)


# --- C1: the generator cannot launder its way to an approval ----------------

def test_generator_cannot_launder_consent_through_the_world_model():
    """The defining proof: a generator that mutates the world model no longer
    changes the verdict."""
    world = _eu_world(consent=False)
    intent = _gdpr_intent()
    generator = _ConsentLaunderer([("send_email", "eu_lead")], risk=2)

    result = _loop(generator, world).run(
        intent=intent, drift_event=DRIFT, world_state=world, intents=[intent]
    )

    assert generator.seen_consent is False           # it read the real state
    assert result.decisions[-1].verdict == GovernanceVerdict.REJECTED
    assert "gdpr_consent_required" in result.decisions[-1].violated_constraints
    assert result.final_verdict == "escalated"
    assert result.execution_result is None
    # The mutation died on the copy; the model the kernel evaluates is untouched.
    assert world.entities["eu_lead"].properties["gdpr_consent"] is False


def test_generator_cannot_strip_a_hard_constraint_from_the_intent():
    """Same laundering class down a different path: the intent the kernel
    evaluates against must survive the generator too."""
    world = _eu_world(consent=False)
    intent = _gdpr_intent()
    generator = _ConstraintStripper([("send_email", "eu_lead")], risk=2)

    result = _loop(generator, world).run(
        intent=intent, drift_event=DRIFT, world_state=world, intents=[intent]
    )

    assert generator.seen_hard_constraints == ["gdpr_consent_required"]
    assert result.decisions[-1].verdict == GovernanceVerdict.REJECTED
    assert "gdpr_consent_required" in result.decisions[-1].violated_constraints
    assert [c.name for c in intent.hard_constraints] == ["gdpr_consent_required"]


def test_snapshotting_does_not_blind_the_generator_kernel_or_fabric():
    """The control: the generator still reads real state, the kernel still
    evaluates the real model, and the fabric still acts on it."""
    world = _eu_world(consent=True)
    intent = _gdpr_intent()
    generator = _Generator([("send_email", "eu_lead")], risk=2)

    result = _loop(generator, world).run(
        intent=intent, drift_event=DRIFT, world_state=world, intents=[intent]
    )

    assert generator.seen_consent is True
    assert generator.seen_hard_constraints == ["gdpr_consent_required"]
    assert result.final_verdict == "approved"
    assert result.execution_result is not None and result.execution_result.success


def test_fabric_executes_against_the_live_world_model():
    """A world-mutating action still lands on the caller's model — the snapshot
    is handed to the generator only."""
    world = _eu_world(consent=True)
    intent = _plain_intent()
    generator = _Generator(
        [("update_record", "eu_lead")],
        parameters={"updates": {"crm_stage": "contacted"}},
    )

    result = _loop(generator, world).run(
        intent=intent, drift_event=DRIFT, world_state=world, intents=[intent]
    )

    assert result.final_verdict == "approved"
    assert world.entities["eu_lead"].properties["crm_stage"] == "contacted"


# --- C2: monitors observe every action, not just the first ------------------

def test_decomposition_detection_sees_a_target_beyond_the_first_action():
    """The sensitive action is packed second. It must still be counted against
    its own target."""
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=2)
    intent = _plain_intent()
    world = _empty_world()
    generator = _Generator([("query_crm", "decoy"), ("query_crm", "sensitive")])
    loop = _loop(generator, world, integrity_monitor=monitor)

    for _ in range(2):
        result = loop.run(intent=intent, drift_event={}, world_state=world)
        assert result.final_verdict == "approved"

    subjects = {s.subject for s in result.integrity_signals if s.signal_type == "GIM-3"}
    assert "sensitive" in subjects


def test_integrity_hold_covers_a_target_beyond_the_first_action():
    """A GIM-flagged target in a later action must HOLD the whole proposal."""
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=3)
    for _ in range(3):
        monitor.observe("unspecified", "L0", target="sensitive")

    intent = _plain_intent()
    world = _empty_world()
    generator = _Generator([("query_crm", "decoy"), ("query_crm", "sensitive")])
    result = _loop(
        generator, world, integrity_monitor=monitor, block_on_integrity=True
    ).run(intent=intent, drift_event={}, world_state=world)

    assert result.final_verdict == "integrity_hold"
    assert result.execution_result is None


def test_self_evolution_monitor_records_every_modified_capability():
    """SE-SURFACE measures the breadth of capabilities modified; a second
    capability in the same proposal must reach it."""
    monitor = SelfEvolutionMonitor(self_modifying_action_types={"task_execution"})
    intent = _plain_intent()
    world = _empty_world()
    generator = _Generator([("query_crm", "skill_a"), ("query_crm", "skill_b")])

    result = _loop(generator, world, self_evolution_monitor=monitor).run(
        intent=intent, drift_event={}, world_state=world,
        action_type_id="task_execution",
    )

    assert result.execution_result is not None and result.execution_result.success
    assert {m.target for m in monitor._mods} == {"skill_a", "skill_b"}


def test_approve_and_execute_records_every_modified_capability():
    """The L2 human-approved dispatch path has the same obligation."""
    monitor = SelfEvolutionMonitor()
    kernel = GovernanceKernel()
    approver_priv, approver_pub = generate_keypair()
    world = _empty_world()
    fabric = ExecutionFabric(
        world,
        kernel_public_key_hex=kernel.public_key_hex,
        public_key_registry=PublicKeyRegistry({"alice": approver_pub}),
    )
    generator = _Generator([("query_crm", "skill_a"), ("query_crm", "skill_b")])
    loop = CGALoop(
        kernel, fabric, strategy_generator=generator, max_attempts=1,
        self_evolution_monitor=monitor,
    )

    result = loop.run(
        intent=_plain_intent(), drift_event={}, world_state=world,
        action_type_id="skill_modification",
    )
    assert result.final_verdict == "awaiting_approval"

    decision = result.decisions[-1]
    valid_until = utcnow() + timedelta(minutes=5)
    decision.human_approver_public_key_id = "alice"
    decision.human_approval_timestamp = utcnow()
    decision.human_approval_valid_until = valid_until
    signature = sign(approver_priv, ExecutionFabric._oob_signed_message(decision))

    exec_result = loop.approve_and_execute(
        result.approved_proposal, decision,
        human_approval_signature=signature,
        approver_key_id="alice",
        valid_until=valid_until,
    )

    assert exec_result.success is True
    assert {m.target for m in monitor._mods} == {"skill_a", "skill_b"}


def test_repeated_target_in_one_proposal_is_not_a_false_decomposition_alert():
    """One proposal is ONE governance authorization. Touching a target three
    times inside it is not threshold-avoidance decomposition; counting it once
    per action would manufacture the alert."""
    monitor = GovernanceIntegrityMonitor(decomposition_count_threshold=3)
    intent = _plain_intent()
    world = _empty_world()
    generator = _Generator([("query_crm", "t1")] * 3)
    loop = _loop(generator, world, integrity_monitor=monitor)

    first = loop.run(intent=intent, drift_event={}, world_state=world)
    assert [s for s in first.integrity_signals if s.signal_type == "GIM-3"] == []

    loop.run(intent=intent, drift_event={}, world_state=world)
    third = loop.run(intent=intent, drift_event={}, world_state=world)
    subjects = {s.subject for s in third.integrity_signals if s.signal_type == "GIM-3"}
    assert subjects == {"t1"}
