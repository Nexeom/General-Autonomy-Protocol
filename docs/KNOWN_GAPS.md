# Known Gaps

Defects and unmet requirements known to the maintainers **right now**, kept
current deliberately. An empty critical section is a release gate, not a
marketing claim.

A gap leaves this list one of two ways: it is fixed, or it is reclassified in
the [Threat Model](THREAT_MODEL.md) as out of scope with a **named deployment
control**. It never leaves by being quietly deleted.

For the full earned/not-earned matrix see [CONFORMANCE.md](CONFORMANCE.md).

## Critical

**C1. The evidence attestation stamp is unsigned and therefore forgeable.**
Governance-relevant world-model properties carry a provenance stamp under
`_evidence_provenance`, and a governed kernel treats an unattested value as
unevaluable — a violation. But the stamp is a plain dict. A caller that
assembles the `WorldModel` itself never passes through `WorldModelStore`, and in
the isolated posture the agent legitimately authors the entire `world_state`
field of the `evaluate` request. A hand-written
`{"attested": true, "governance_properties": ["gdpr_consent", "geo"]}` therefore
reads as attested to the kernel.

Reproduced against a governed kernel: the same proposal is
`rejected ['gdpr_consent_required']` with an unattested world model and
`approved []` with the forged stamp. **This requires no code execution** — it is
reachable through the published RPC interface by any party that supplies data,
which puts it in a different and larger exposure class than A1 below.

This was previously recorded as closed on the strength of an HTTP reproduction.
That verification exercised the ingest path, which the store does guard, and not
the RPC path, which it does not. The fix is an Ed25519 attestation binding
entity, property values, expiry and issuer, verified kernel-side against an
issuer registry resolved from the trust root — the only control in GAP whose
anchor can live off the agent host entirely. In progress on
`claude/execution-boundary`.

The five criticals found in the last review — unauthenticated action-type
registration, unauthenticated world-model writes flipping a hard constraint,
sub-L2 decision replay, the circular trust root, and the agent-supplied
evaluation clock — are closed and regression-tested. The sixth, in-process
signature verification, is not a defect that can be closed in this
architecture; it is [Threat Model §4](THREAT_MODEL.md) and the honest partial
in the isolation row of the conformance matrix.

Two of those fixes did not hold on the first attempt, which is worth recording
rather than smoothing over. Attestation covered the store's own write paths but
not the Execution Fabric's result write-back, so an agent could still launder
its own consent in one approved step; a direct write to a governance-relevant
property now revokes its attested standing wherever the write comes from.
Single-use covered sequential presentation but not concurrent: an in-progress
row was resumable, so eight threads presenting one authorization all dispatched.
A settled-failure state plus an in-flight lease separates "an attempt failed"
from "an attempt is running". Both are pinned in
`tests/test_bypass_regressions.py`. Both were found by writing adversarial tests
against code that had just been hardened — the fix and the test for the fix are
not the same work.

## Architectural

**A1. Signature verification runs in the agent's process.**
`ExecutionFabric` holds the pinned kernel key and the unsigned-decisions escape
hatch as ordinary mutable attributes, beside the executor registry, kill
switch, world model and lineage store. Code execution in the agent process
defeats governance without forging anything. Closing this needs an execution
boundary — a socket or attach mode where dispatch is authorized by a process
the agent cannot reach. Not built, not scheduled.

**A2. The lineage chain has no independent witness.**
The chain anchor is signed, but the signing key is held by the process that
owns the database and the anchor lives in the same SQLite file. An attacker
with both the file and the key can delete records and re-sign a consistent
shorter chain. The real answer is external/WORM anchoring.

**A3. The kill switch has no operator identity.**
Engage and disengage take a free-text actor label and log to memory. Any
in-process holder can flip it.

## Functional

**F1. Three of five GIM detectors never fire.**
GIM-2, GIM-4 and GIM-5 are implemented and unit-tested but consume telemetry no
shipped path supplies. Either wire the feeds or move them to Planned — leaving
them looking live is the failure mode this project exists to avoid.

**F2. `DynamicRiskEngine` is unreachable.**
All three detectors gate on baseline state populated only by feed methods that
nothing calls. Feeding it must not reintroduce a governance-config write on the
RPC boundary, which is what the registry fix removed.

**F3. SIR captures boundaries it never enforces.**
`declared_boundaries` — "what the system will NOT do" — is stored and never
checked against a proposal. `verify_seal` has zero callers. The shipped
reconciler and REST API run CGA without the SIR gate at all.

**F4. The independent classifier is paraphrase-evadable.**
`independent_review.py` and SIR's meta-intent inference rate risk from keyword
substring matches. Renaming an action defeats them, and the independent
classifier is load-bearing for authorization ratcheting.

**F5. `resolve_intent_conflict` asserts an invariant it does not verify.**
It returns `hard_constraints_preserved: True` unconditionally, and that
unverified assertion is written into the lineage record.

**F6. The learning engine's operational learning is write-only.**
Heuristics are recorded and never consumed on any decision path.

**F7. `PolicyActivation.condition` is a dead field that fails open.**
A hard constraint declared by runtime condition is silently never enforced,
because nothing reads the field.

## Migration

**M1. v1-signed decisions do not verify.**
Moving `kernel_public_key_id` into the signed payload and bumping the domain
tag to `gap.governance.decision.v2` was deliberate and one-way. Nothing
migrates decisions already persisted in a lineage record.

**M2. The mutating HTTP routes moved behind a flag.**
`create_app(enable_mutating_routes=True)` is now required for the intent,
world, reconciler, learning and escalation writers.
`POST /governance/action-types` is gone permanently.

## Process

**P1. No external security review.** Every finding fixed to date was found by
the maintainers and their own tooling. A second pair of eyes has never audited
this code.

**P2. No tagged release, no adopters, one author.** Nothing here has been
exercised by an independent implementer, which is the strongest evidence a
protocol can have and the evidence this project most lacks.

**P3. Published specifications with no implementation.**
`GAP-AT-FIN-001 / SpendGate` is published under `action-types/` and has zero
corresponding code.
