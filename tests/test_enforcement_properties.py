"""Enforcement-semantic properties (SA-5).

The sweeps in :mod:`tests.test_property_based` assert the governance core never
FAILS CRASHED. These assert the other half — that it also ENFORCES. Every
property here is a statement about the *content* of a verdict rather than its
shape: a violated HARD constraint is never approved, an authorization is never
granted below its own floor, a governed decision always carries the binding that
makes it single-use, an authorization is spendable exactly once, and a governed
kernel times its own decisions.

Each property is checked against an oracle restated in this module — the
authorization ladder, the registration floor, the conditions that constitute a
violation — never against the kernel's own evaluators. An oracle derived from
the implementation agrees with the implementation whatever it does; one stated
independently turns an enforcement bypass into a falsifying example.
"""

from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import (
    PublicKeyRegistry,
    generate_keypair,
    sign,
    verify,
)
from gap_kernel.errors import GovernanceConfigError
from gap_kernel.execution.fabric import ExecutionError, ExecutionFabric
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.models.governance import (
    ActionTypeSpec,
    AuthorizationLevel,
    GovernanceDecision,
    GovernanceVerdict,
    RiskProfile,
    canonical_decision_payload,
)
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.strategy import (
    PlannedAction,
    StrategyProposal,
    compute_proposal_digest,
)
from gap_kernel.models.world import EntityState, WorldModel
from gap_kernel.verification.execution_ledger import ExecutionLedger

# --- Oracles -----------------------------------------------------------------
# The spec's ladders, restated here so a property compares the kernel against the
# rule rather than against itself.

_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}

# Graduated authorization by risk score (L0 autonomous ... L4 human only).
_RISK_FLOOR = {
    1: "L0", 2: "L0", 3: "L0", 4: "L1", 5: "L1",
    6: "L2", 7: "L2", 8: "L3", 9: "L4", 10: "L4",
}

# The authorization gate each baseline action type carries.
_BASELINE_TYPE_FLOOR = {
    "task_execution": "L0",
    "skill_modification": "L2",
    "drift_reconciliation": "L1",
    "escalation": "L0",
    "policy_proposal": "L4",
}

_IMPACT = {"local": 0, "team": 1, "org": 2, "external": 3}
_REVERSIBILITY = {"reversible": 0, "partially_reversible": 1, "irreversible": 2}
_BLAST = {"narrow": 0, "moderate": 1, "wide": 2}


def _registration_floor(impact: str, reversibility: str, blast: str) -> int:
    """The lowest authorization rank a risk profile may be registered at."""
    score = _IMPACT[impact] + _REVERSIBILITY[reversibility] + _BLAST[blast]
    return min(max(_RANK.values()), (score + 1) // 2)


# --- Fixtures in miniature ---------------------------------------------------

PROFILE_KEY_ID = "regulatory_authority_key"
KERNEL_KEY_ID = "enforcement_kernel"
APPROVER = "human_approver_property"

_INTERACTIVE = ["send_email", "send_sms", "direct_call", "automated_outreach"]
_EU_JURISDICTIONS = ["EU", "DE", "FR", "IT", "ES", "NL", "IE", "PL", "SE"]
_TRUTHY = st.sampled_from([True, 1, "yes"])
# Values that leave an affirmative flag unsatisfied, alongside omitting it.
_FALSEY = st.sampled_from([False, None, 0, "", []])


def _intent(hard=(), soft=()) -> IntentVector:
    return IntentVector(
        id="intent_p", objective="o", priority=50,
        hard_constraints=list(hard), soft_constraints=list(soft),
        created_by="human", created_at=utcnow(),
    )


def _proposal(actions, *, pid="prop_p", cost=0.01) -> StrategyProposal:
    return StrategyProposal(
        id=pid, intent_id="intent_p", attempt_number=1, plan_description="p",
        actions=list(actions), estimated_cost=cost, rationale="r",
        generated_at=utcnow(),
    )


def _empty_world() -> WorldModel:
    return WorldModel(entities={}, last_reconciled=utcnow())


def _world_with(properties: dict, entity_id: str = "lead_p") -> WorldModel:
    return WorldModel(
        entities={
            entity_id: EntityState(
                entity_type="lead", entity_id=entity_id, properties=dict(properties),
                last_updated=utcnow(), source="crm",
            )
        },
        last_reconciled=utcnow(),
    )


def _hard(name: str, description: str = "d", threshold=None) -> Constraint:
    return Constraint(
        name=name, type=ConstraintType.HARD, description=description,
        threshold=threshold,
    )


def _governed_kernel(**kwargs) -> GovernanceKernel:
    private_hex, public_hex = generate_keypair()
    profile = sign_profile(
        ApplicabilityProfile(profile_id="prof_enforcement"),
        private_hex,
        PROFILE_KEY_ID,
    )
    return GovernanceKernel(
        governed=True,
        applicability_profile=profile,
        profile_key_registry=PublicKeyRegistry({PROFILE_KEY_ID: public_hex}),
        kernel_key_id=KERNEL_KEY_ID,
        **kwargs,
    )


# --- P1: a violated HARD constraint is never approved ------------------------

@st.composite
def _hard_violation(draw):
    """A HARD constraint, a proposal, and a world state whose violation holds by
    construction. The scenario is the oracle: each branch withholds exactly the
    evidence the rule demands, so the correct verdict is knowable without asking
    the kernel."""
    kind = draw(st.sampled_from([
        "no_evaluator", "ai_disclosure", "fairness", "safety", "aml_amount",
        "phi_bulk", "phi_count", "ip_assessment", "ip_provenance", "cost",
        "gdpr", "hours",
    ]))
    risk = draw(st.integers(min_value=1, max_value=3))
    omit = draw(st.booleans())
    action_type = "query_crm"
    params: dict = {}
    world = _empty_world()
    cost = 0.01
    target = "lead_p"

    if kind == "no_evaluator":
        # A rule the kernel has no concrete check for cannot be certified
        # satisfied, so it stands as a violation.
        constraint = _hard(draw(st.sampled_from([
            "bespoke_client_rule", "unmapped_policy", "future_regulation",
            "no_such_check",
        ])))
        action_type = draw(st.sampled_from(_INTERACTIVE + ["query_crm"]))
    elif kind == "ai_disclosure":
        constraint = _hard("ai_interaction_disclosure")
        action_type = draw(st.sampled_from(_INTERACTIVE))
        if not omit:
            params["ai_disclosed"] = draw(_FALSEY)
    elif kind == "fairness":
        constraint = _hard("fairness_evaluation_required")
        params["consequential_decision"] = draw(_TRUTHY)
        if not omit:
            params["fairness_evaluation"] = draw(_FALSEY)
    elif kind == "safety":
        constraint = _hard("safety_boundary")
        params["safety_critical"] = draw(_TRUTHY)
        if not omit:
            # Anything short of an affirmative True leaves the bound uncertified.
            params["within_safety_boundary"] = draw(
                st.sampled_from([False, None, 0, 1, "true", "within"])
            )
    elif kind == "aml_amount":
        threshold = draw(st.integers(min_value=0, max_value=10_000))
        constraint = _hard("aml_screening_required", threshold=float(threshold))
        params["transaction_amount"] = threshold + draw(
            st.integers(min_value=1, max_value=5_000)
        )
        screen = draw(st.sampled_from(["neither", "aml_only", "sanctions_only"]))
        if screen == "aml_only":
            params["aml_screened"] = True
        elif screen == "sanctions_only":
            params["sanctions_checked"] = True
    elif kind == "phi_bulk":
        constraint = _hard("minimum_necessary_phi")
        params["accesses_phi"] = draw(_TRUTHY)
        params["scope"] = "bulk"
        if not omit:
            params["phi_access_justification"] = draw(_FALSEY)
    elif kind == "phi_count":
        threshold = draw(st.integers(min_value=1, max_value=500))
        constraint = _hard("minimum_necessary_phi", threshold=float(threshold))
        params["accesses_phi"] = draw(_TRUTHY)
        params["record_count"] = threshold + draw(
            st.integers(min_value=1, max_value=500)
        )
    elif kind == "ip_assessment":
        constraint = _hard("ip_content_risk")
        params["generates_content"] = draw(_TRUTHY)
        if not omit:
            params["ip_risk_assessment"] = draw(_FALSEY)
    elif kind == "ip_provenance":
        constraint = _hard("ip_content_risk")
        params["generates_content"] = draw(_TRUTHY)
        params["ip_risk_assessment"] = "reviewed"
        params[draw(st.sampled_from([
            "copyright_similarity", "trademark_usage", "public_distribution",
        ]))] = draw(_TRUTHY)
        if not omit:
            params["provenance"] = draw(_FALSEY)
    elif kind == "cost":
        ceiling = draw(st.integers(min_value=1, max_value=5_000))
        constraint = _hard(
            "cost_ceiling", description=f"Spend must stay under ${ceiling} per cycle"
        )
        cost = ceiling + draw(
            st.floats(min_value=0.5, max_value=500.0, allow_nan=False,
                      allow_infinity=False, width=32)
        )
    elif kind == "gdpr":
        constraint = _hard("gdpr_consent_required")
        action_type = draw(st.sampled_from(_INTERACTIVE))
        entity_props = {"geo": draw(st.sampled_from(_EU_JURISDICTIONS))}
        if not omit:
            entity_props["gdpr_consent"] = draw(_FALSEY)
        world = _world_with(entity_props, target)
    else:  # hours
        constraint = _hard("no_contact_outside_hours")
        action_type = draw(st.sampled_from(_INTERACTIVE))
        world = _world_with(
            {"local_hour": draw(st.sampled_from([22, 23, 0, 1, 2, 3, 4, 5, 6]))},
            target,
        )

    action = PlannedAction(
        action_type=action_type, target=target, parameters=params, risk_score=risk
    )
    return constraint, _proposal([action], cost=cost), world


@given(case=_hard_violation())
@settings(max_examples=300, deadline=None)
def test_a_violated_hard_constraint_is_never_approved(case):
    """P1. No generated proposal that violates a HARD constraint is APPROVED, and
    the decision names the constraint it broke. Risk stays in the L0 band, so the
    ONLY thing standing between this proposal and an autonomous approval is
    constraint enforcement."""
    constraint, proposal, world = case
    decision = GovernanceKernel().evaluate_proposal(
        proposal=proposal, intents=[_intent([constraint])], world_state=world
    )
    assert decision.verdict != GovernanceVerdict.APPROVED
    assert constraint.name in decision.violated_constraints
    assert decision.rejection_detail


@st.composite
def _hard_compliance(draw):
    """The mirror of :func:`_hard_violation`: the same rules, each satisfied. A
    kernel that rejects indiscriminately is not enforcing either."""
    kind = draw(st.sampled_from([
        "ai_disclosure", "fairness", "safety", "aml_under", "aml_screened",
        "phi_bulk", "phi_count", "ip", "cost", "gdpr_consented",
        "gdpr_non_eu", "hours",
    ]))
    risk = draw(st.integers(min_value=1, max_value=3))
    action_type = "query_crm"
    params: dict = {}
    world = _empty_world()
    cost = 0.01
    target = "lead_p"

    if kind == "ai_disclosure":
        constraint = _hard("ai_interaction_disclosure")
        action_type = draw(st.sampled_from(_INTERACTIVE))
        params["ai_disclosed"] = True
    elif kind == "fairness":
        constraint = _hard("fairness_evaluation_required")
        params["consequential_decision"] = True
        params["fairness_evaluation"] = "no disparate impact"
    elif kind == "safety":
        constraint = _hard("safety_boundary")
        params["safety_critical"] = True
        params["within_safety_boundary"] = True
    elif kind == "aml_under":
        threshold = draw(st.integers(min_value=100, max_value=10_000))
        constraint = _hard("aml_screening_required", threshold=float(threshold))
        params["transaction_amount"] = draw(
            st.integers(min_value=0, max_value=threshold)
        )
    elif kind == "aml_screened":
        threshold = draw(st.integers(min_value=0, max_value=10_000))
        constraint = _hard("aml_screening_required", threshold=float(threshold))
        params["transaction_amount"] = threshold + 1
        params["aml_screened"] = True
        params["sanctions_checked"] = True
    elif kind == "phi_bulk":
        constraint = _hard("minimum_necessary_phi")
        params["accesses_phi"] = True
        params["scope"] = "bulk"
        params["phi_access_justification"] = "care coordination"
    elif kind == "phi_count":
        threshold = draw(st.integers(min_value=5, max_value=500))
        constraint = _hard("minimum_necessary_phi", threshold=float(threshold))
        params["accesses_phi"] = True
        params["record_count"] = draw(st.integers(min_value=1, max_value=threshold))
    elif kind == "ip":
        constraint = _hard("ip_content_risk")
        params["generates_content"] = True
        params["ip_risk_assessment"] = "cleared"
    elif kind == "cost":
        ceiling = draw(st.integers(min_value=10, max_value=5_000))
        constraint = _hard(
            "cost_ceiling", description=f"Spend must stay under ${ceiling} per cycle"
        )
        cost = float(draw(st.integers(min_value=0, max_value=ceiling)))
    elif kind == "gdpr_consented":
        constraint = _hard("gdpr_consent_required")
        action_type = draw(st.sampled_from(_INTERACTIVE))
        world = _world_with(
            {"geo": draw(st.sampled_from(_EU_JURISDICTIONS)), "gdpr_consent": True},
            target,
        )
    elif kind == "gdpr_non_eu":
        constraint = _hard("gdpr_consent_required")
        action_type = draw(st.sampled_from(_INTERACTIVE))
        world = _world_with(
            {"geo": draw(st.sampled_from(["US", "CA", "AU", "JP", "BR"]))}, target
        )
    else:  # hours
        constraint = _hard("no_contact_outside_hours")
        action_type = draw(st.sampled_from(_INTERACTIVE))
        world = _world_with(
            {"local_hour": draw(st.integers(min_value=7, max_value=21))}, target
        )

    action = PlannedAction(
        action_type=action_type, target=target, parameters=params, risk_score=risk
    )
    return constraint, _proposal([action], cost=cost), world


@given(case=_hard_compliance())
@settings(max_examples=200, deadline=None)
def test_a_satisfied_hard_constraint_does_not_block(case):
    """P1's mirror. Enforcement means ruling both ways: a proposal that carries
    the evidence a HARD constraint demands is approved on it."""
    constraint, proposal, world = case
    decision = GovernanceKernel().evaluate_proposal(
        proposal=proposal, intents=[_intent([constraint])], world_state=world
    )
    assert decision.verdict == GovernanceVerdict.APPROVED
    assert decision.violated_constraints == []


# --- P2: no authorization is granted below its own floor ---------------------

@given(
    risks=st.lists(st.integers(min_value=1, max_value=10), min_size=1, max_size=4),
    action_type_id=st.one_of(
        st.none(), st.sampled_from(sorted(_BASELINE_TYPE_FLOOR))
    ),
)
@settings(max_examples=300, deadline=None)
def test_no_proposal_is_authorized_below_its_own_floor(risks, action_type_id):
    """P2. The level a decision grants is never weaker than the greater of the
    floor its risk score implies and the gate its action type carries — and a
    proposal whose floor is L4 (human only) is never approved at all."""
    proposal = _proposal([
        PlannedAction(
            action_type="query_crm", target=f"t{i}", parameters={}, risk_score=risk
        )
        for i, risk in enumerate(risks)
    ])
    decision = GovernanceKernel().evaluate_proposal(
        proposal=proposal, intents=[_intent()], world_state=_empty_world(),
        action_type_id=action_type_id,
    )

    floor = _RANK[_RISK_FLOOR[max(risks)]]
    if action_type_id is not None:
        floor = max(floor, _RANK[_BASELINE_TYPE_FLOOR[action_type_id]])

    assert decision.authorization_level is not None
    assert _RANK[decision.authorization_level.value] >= floor
    if floor == _RANK["L4"]:
        assert decision.verdict == GovernanceVerdict.ESCALATE


@given(
    impact=st.sampled_from(sorted(_IMPACT)),
    reversibility=st.sampled_from(sorted(_REVERSIBILITY)),
    blast=st.sampled_from(sorted(_BLAST)),
    declared=st.sampled_from(sorted(_RANK)),
)
@settings(max_examples=200, deadline=None)
def test_an_action_type_cannot_be_registered_below_its_risk_floor(
    impact, reversibility, blast, declared
):
    """P2 at the registry. The comparator that decides whether a granted level
    meets a required one also decides what gate a new action type may be filed
    under; a type that clears its floor registers, one that does not is refused
    and leaves no entry behind."""
    kernel = GovernanceKernel()
    spec = ActionTypeSpec(
        type_id="generated_type",
        description="d",
        risk_profile=RiskProfile(
            impact_scope=impact, reversibility=reversibility, blast_radius=blast
        ),
        default_authorization_level=AuthorizationLevel(declared),
    )

    if _RANK[declared] < _registration_floor(impact, reversibility, blast):
        with pytest.raises(GovernanceConfigError, match="requires at least"):
            kernel.register_action_type(spec, "admin_user")
        assert kernel.get_action_type("generated_type") is None
    else:
        kernel.register_action_type(spec, "admin_user")
        registered = kernel.get_action_type("generated_type")
        assert registered.default_authorization_level.value == declared


# --- P3: a governed decision always carries its single-use binding -----------

_ANY_ACTION_TYPE = st.sampled_from(
    _INTERACTIVE + ["query_crm", "update_record", "wire_transfer"]
)
_KNOWN_CONSTRAINTS = st.sampled_from([
    "gdpr_consent_required", "no_contact_outside_hours", "cost_ceiling",
    "ai_interaction_disclosure", "safety_boundary", "unregistered_rule",
])


@given(
    action_type=_ANY_ACTION_TYPE,
    action_type_id=st.one_of(
        st.none(),
        st.sampled_from(sorted(_BASELINE_TYPE_FLOOR)),
        st.text(max_size=8),
    ),
    risk=st.integers(min_value=1, max_value=10),
    constraint_names=st.lists(_KNOWN_CONSTRAINTS, max_size=2),
    target=st.text(max_size=8),
)
@settings(max_examples=200, deadline=None)
def test_a_governed_decision_always_carries_its_single_use_binding(
    action_type, action_type_id, risk, constraint_names, target
):
    """P3. Whatever it rules and however it got there, a governed kernel never
    hands back a decision without a verifiable signature, a nonce, an expiry, and
    the digest of the proposal it authorizes. Anything missing here is a decision
    the Execution Fabric cannot bind to one proposal, one use, or one window."""
    kernel = _governed_kernel()
    proposal = _proposal([
        PlannedAction(
            action_type=action_type, target=target, parameters={}, risk_score=risk
        )
    ])
    decision = kernel.evaluate_proposal(
        proposal=proposal,
        intents=[_intent([_hard(name) for name in constraint_names])],
        world_state=_empty_world(),
        action_type_id=action_type_id,
    )

    assert decision.decision_signature
    assert decision.nonce
    assert decision.expires_at is not None
    assert decision.expires_at > decision.evaluated_at
    assert decision.kernel_public_key_id == KERNEL_KEY_ID
    assert decision.proposal_digest == compute_proposal_digest(proposal)
    assert verify(
        kernel.public_key_hex,
        canonical_decision_payload(decision),
        decision.decision_signature,
    )


# --- P4: an authorization is spendable exactly once --------------------------

class _CallCounter:
    """An executor that records every dispatch it receives."""

    def __init__(self):
        self.calls = 0

    def __call__(self, action):
        self.calls += 1
        return {"status": "ok"}


_EXECUTABLE_ACTIONS = st.sampled_from([
    "query_crm", "send_email", "send_sms", "update_record", "route_to_human",
])


@given(
    level=st.sampled_from(list(AuthorizationLevel)),
    action_types=st.lists(_EXECUTABLE_ACTIONS, min_size=1, max_size=3),
    nonce=st.text(alphabet="0123456789abcdef", min_size=8, max_size=8),
)
@settings(max_examples=120, deadline=None)
def test_an_authorization_is_spendable_exactly_once(level, action_types, nonce):
    """P4. At EVERY authorization level, a decision that executed successfully is
    refused on re-presentation and dispatches nothing a second time. The ledger is
    the only guard that can tell a first execution from a fourth; every other one
    is stateless and passes identically on both."""
    kernel_priv, kernel_pub = generate_keypair()
    approver_priv, approver_pub = generate_keypair()
    proposal = _proposal(
        [
            PlannedAction(
                action_type=action_type, target=f"lead_{i}", parameters={},
                risk_score=1,
            )
            for i, action_type in enumerate(action_types)
        ],
        pid="prop_single_use",
    )
    decision = GovernanceDecision(
        id="gov_single_use", proposal_id=proposal.id,
        verdict=GovernanceVerdict.APPROVED, authorization_level=level,
        temporal_context={}, policy_snapshot={}, evaluated_at=utcnow(),
        nonce=nonce, expires_at=utcnow() + timedelta(minutes=5),
        proposal_digest=compute_proposal_digest(proposal),
    )
    if level in (AuthorizationLevel.L2, AuthorizationLevel.L3, AuthorizationLevel.L4):
        decision.human_approver_public_key_id = APPROVER
        decision.human_approval_timestamp = utcnow()
        decision.human_approval_valid_until = decision.expires_at
        decision.human_approval_signature = sign(
            approver_priv, ExecutionFabric._oob_signed_message(decision)
        )
    decision.decision_signature = sign(
        kernel_priv, canonical_decision_payload(decision)
    )

    fabric = ExecutionFabric(
        _empty_world(),
        kernel_public_key_hex=kernel_pub,
        public_key_registry=PublicKeyRegistry({APPROVER: approver_pub}),
        execution_ledger=ExecutionLedger(),
    )
    counter = _CallCounter()
    for action_type in set(action_types):
        fabric.register_executor(action_type, counter)

    assert fabric.execute(proposal, decision).success is True
    assert counter.calls == len(action_types)

    for _ in range(3):
        with pytest.raises(ExecutionError):
            fabric.execute(proposal, decision)
    assert counter.calls == len(action_types)


# --- P5: a governed kernel times its own decisions ---------------------------

_TTL_SECONDS = 120


@given(
    offset_seconds=st.integers(min_value=3_600, max_value=10_000_000),
    direction=st.sampled_from([-1, 1]),
    risk=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=150, deadline=None)
def test_a_governed_kernel_times_its_own_decisions(offset_seconds, direction, risk):
    """P5. A caller-named evaluation time moves nothing a governed kernel signs.
    The evaluation clock decides which scheduled constraints are active and how
    long the authorization lives, so a caller who could name it would be choosing
    its own policy set and its own expiry."""
    kernel = _governed_kernel(decision_ttl_seconds=_TTL_SECONDS)
    claimed = utcnow() + timedelta(seconds=direction * offset_seconds)
    proposal = _proposal([
        PlannedAction(
            action_type="query_crm", target="t1", parameters={}, risk_score=risk
        )
    ])

    before = utcnow()
    decision = kernel.evaluate_proposal(
        proposal=proposal, intents=[_intent()], world_state=_empty_world(),
        current_time=claimed, action_type_id="task_execution",
    )
    after = utcnow()

    assert before <= decision.evaluated_at <= after
    assert decision.temporal_context["evaluated_at"] == decision.evaluated_at.isoformat()
    assert decision.expires_at <= after + timedelta(seconds=_TTL_SECONDS)
