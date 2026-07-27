# Known Gaps

Defects and unmet requirements known to the maintainers **right now**, kept
current deliberately. An empty critical section is a release gate, not a
marketing claim.

A gap leaves this list one of two ways: it is fixed, or it is reclassified in
the [Threat Model](THREAT_MODEL.md) as out of scope with a **named deployment
control**. It never leaves by being quietly deleted.

For the full earned/not-earned matrix see [CONFORMANCE.md](CONFORMANCE.md).

## Critical

*None currently open.*

The five criticals found in the last review — unauthenticated action-type
registration, unauthenticated world-model writes flipping a hard constraint,
sub-L2 decision replay, the circular trust root, and the agent-supplied
evaluation clock — are closed and regression-tested. The sixth, in-process
signature verification, is not a defect that can be closed in this
architecture; it is [Threat Model §4](THREAT_MODEL.md) and the honest partial
in the isolation row of the conformance matrix.

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
