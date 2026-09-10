# Known Gaps

Defects and unmet requirements known to the maintainers **right now**, kept
current deliberately. An empty critical section is a release gate, not a
marketing claim.

A gap leaves this list one of two ways: it is fixed, or it is reclassified in
the [Threat Model](THREAT_MODEL.md) as out of scope with a **named deployment
control**. It never leaves by being quietly deleted.

For the full earned/not-earned matrix see [CONFORMANCE.md](CONFORMANCE.md).

## Critical

No newly reproduced critical remains unaddressed in the bounded regression
scenarios below. This is not a claim that no unknown critical exists. Read the
deployment-specific architectural limits before enabling real tools.

**September remediation:** failed retries now atomically persist `in_progress`
before dispatch; the durable failure-then-concurrency regression proves a second
claim is refused. Bulk property merges cannot restore the audit-only provenance
stamp, and signed evidence from the existing evidence PR binds the actual values.
Unsupported runtime conditions conservatively keep their policies active.
Evidence: `tests/test_bypass_regressions.py`, `tests/test_fail_closed.py`.

**C1 is closed. The evidence attestation stamp is now an Ed25519 signature.**
Governance-relevant world-model properties used to carry a provenance stamp
under `_evidence_provenance` that was a plain dict, so any party that could
supply a `WorldModel` could write it. In the isolated posture that party is the
agent, which legitimately authors the entire `world_state` field of the
`evaluate` request. Reproduced against a governed kernel: the same proposal was
`rejected ['gdpr_consent_required']` unattested and `approved []` with a
hand-written `{"attested": true, "governance_properties": ["gdpr_consent",
"geo"]}` — **no code execution required.**

Closed by Signed Evidence Attestation (`gap_kernel/world_model/attestation.py`):
an Ed25519 signature by a registered issuer binds the entity id, the exact
governance values, the window and the issuer key id; the kernel verifies it
itself against issuers resolved from the trust root, and compares values as
canonical JSON so a signature over `1` cannot certify `True`. The same forged
dict now yields `rejected ['gdpr_consent_required']` under both reserved keys.
Pinned in `tests/test_evidence_attestation.py`.

This was previously recorded as closed once before, on the strength of an HTTP
reproduction that exercised the ingest path the store guards rather than the RPC
path it does not. So, precisely what "closed" means this time, and what it does
not:

* **It is a deployment property.** If an issuer private key lives on the agent's
  host under the agent's OS user, the agent reads it and mints any consent it
  likes. `InProcessEvidenceSigner` exists for prototypes and says so in its own
  docstring. If the agent process can sign, the mechanism is theatre.
* **A signature proves an issuer asserted a fact, not that the fact is true.** A
  compromised source of record signs falsehoods and GAP certifies them
  faithfully — the same shape as "GAP verifies a signature, it cannot verify a
  person".
* **Replay and staleness are bounded, not closed.** There is no revocation list
  and no CRL. A captured attestation keeps working until the kernel's max-age
  ceiling passes, so **GAP is not compliant on immediate consent withdrawal**.
* **It hardens 2 of the 9 registered evaluators** — see F8 below, which is the
  larger hole.
* **Signed evidence alone does not supply an execution boundary.** The separate
  reference gateway and container topology described under A1 now address a
  bounded two-tool integration; embedded callers retain the original limit.

The five criticals found in the last review — unauthenticated action-type
registration, unauthenticated world-model writes flipping a hard constraint,
sub-L2 decision replay, the circular trust root, and the agent-supplied
evaluation clock — are closed and regression-tested. The sixth, in-process
signature verification, is not a defect that can be closed in this
architecture; it is [Threat Model §4](THREAT_MODEL.md) and the honest partial
in the isolation row of the conformance matrix.

Two of those fixes did not hold on the first attempt, which is worth recording
rather than smoothing over. Attestation covered the store's own write paths but
neither the Execution Fabric's result write-back nor the RPC path, so an agent
could launder its own consent in one approved step and, more cheaply, simply
hand-write the stamp. The signature now binds the values, so a write that
changes one leaves the attestation failing its value check — with one
exception that had to be fixed separately: `update_from_execution` re-minted
the attestation when the channel carried a signer, which let an executor
certify its own output. An execution result is never a source of record, so
that path no longer mints.
Single-use covered sequential presentation but not concurrent: an in-progress
row was resumable, so eight threads presenting one authorization all dispatched.
A settled-failure state plus an in-flight lease separates "an attempt failed"
from "an attempt is running". Both are pinned in
`tests/test_bypass_regressions.py`. Both were found by writing adversarial tests
against code that had just been hardened — the fix and the test for the fix are
not the same work.

Three further bypasses were found by attacking the signed-evidence work itself,
after it was written and before it shipped, and all three are closed:

- **The entity binding did not bind.** An attestation names an `entity_id`, but
  the kernel resolves a proposal's target by the world model's **map key** — two
  independent strings, both authored by the agent on the RPC path. A genuine
  attestation filed under any key authorized outreach to arbitrarily many
  invented targets without the blob being touched. `WorldModel` now requires an
  entity's key to equal its own `entity_id`.
- **A signing channel re-minted on executor write-back.**
  `update_from_execution` re-signed whatever an executor had just written when
  the channel carried a signer, so a deployment that wired one got the laundering
  back in a single call. An execution result is never a source of record; that
  path no longer mints.
- **Renaming an action walked around the gate.** Both world-model-backed gates
  fired on an allowlist of four exact literals, so an EU lead with no consent and
  no attestation was approved under `action_type` of `email`, `outreach`,
  `SEND_EMAIL`, or `send_email ` with a trailing space. The classification is an
  input to a HARD constraint, so an unknown type now counts as contact. This one
  was pre-existing and independent of signed evidence, and it mattered more than
  it looks: it meant the attestation was protecting a door with no walls.

## Architectural

**A1. Embedded execution still runs in the agent's process.**
`ExecutionFabric` holds the pinned kernel key and the unsigned-decisions escape
hatch as ordinary mutable attributes, beside the executor registry, kill
switch, world model and lineage store. Code execution in the agent process
defeats governance without forging anything. Closing this needs an execution
boundary with keys and tool credentials inaccessible to the agent.
`gap_kernel/gateway/` now owns authorization and dispatch for the reference
`lookup` and `notify` tools. `deploy/compose.yaml` separates agent, gateway and
credential-protected sink by mounts and networks. This control depends on the
deployed container/OS boundary; merely starting same-user processes does not
close A1. Container runtime, host administrators, gateway code and tool owners
are trusted. See [GATEWAY.md](GATEWAY.md) and the separate container CI results.

**A2. The lineage chain has no independent witness.**
The chain anchor is signed, but the signing key is held by the process that
owns the database and the anchor lives in the same SQLite file. An attacker
with both the file and the key can delete records and re-sign a consistent
shorter chain. The real answer is external/WORM anchoring.

**A3. The kill switch has no operator identity.**
Engage and disengage take a free-text actor label and log to memory. Any
in-process holder can flip it.
The reference gateway instead recognizes an operator-owned `halted` file in
its state directory and checks it before planning and each dispatch. There is
no agent HTTP toggle. Its identity assurance is the service host's administrative
access control; it cannot undo a tool call already in progress.

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

**F7. Runtime condition expressions are not evaluated; fail-open behavior fixed.**
Any supplied `PolicyActivation.condition` conservatively activates the policy,
including beside a nonmatching schedule. This may over-restrict actions, but
does not silently drop a hard rule. A safe expression evaluator remains unbuilt.

**F8. Proposal-borne regulatory metadata is unattested — the named follow-on to
C1, and by count of affected categories a LARGER hole than the one C1 closed.**
Signed Evidence Attestation covers the two evaluators that read the world model:
`gdpr_consent_required` and `no_contact_outside_hours`. The other **seven** rule
entirely on `action.parameters` and `estimated_cost`, which the agent authors,
and five of the seven are gated by a flag the agent also sets (the other two,
`ai_interaction_disclosure` and `cost_ceiling`, fail closed on omission but
still rule on a number the agent wrote):

| Evaluator | The flag that switches it on |
| --- | --- |
| `safety_boundary` (Cat 7) | omit `safety_critical` and the physical-safety check never runs |
| `aml_screening_required` (Cat 5) | omit `transaction_amount` and AML screening never runs |
| `minimum_necessary_phi` (Cat 6) | omit `accesses_phi` and the PHI gate never runs |
| `fairness_evaluation_required` (Cat 4) | omit `consequential_decision` and the disparate-impact gate never runs |
| `ip_content_risk` (Cat 8) | omit `generates_content` and the IP gate never runs |
| `ai_interaction_disclosure` (Cat 3) | reads `ai_disclosed` off the same agent-authored dict |
| `cost_ceiling` | reads `estimated_cost`, declared by the proposal |

The fix has the same shape as C1: extend attestation to proposal-borne
regulatory metadata, so a claim like `transaction_amount` or
`within_safety_boundary` must be signed by a party that is not the proposer. It
is harder than C1 because there is no equivalent of a consent-of-record system
for "this action is safety-critical" — the classification is often the agent's
own, which is the actual problem. Not built, not scheduled.

The reference gateway narrows this surface for its two tools: it rejects
caller-supplied risk, cost, evidence and policy fields, and constructs proposals
from an operator-controlled catalog. This is not a general classifier or a fix
for arbitrary embedded callers of the other regulatory evaluators.

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

**P1. No independent external security review.** The implementation has automated
review and regression evidence, not a third-party audit. The owner is preparing
the repository before requesting outside review; [REVIEW_GUIDE.md](REVIEW_GUIDE.md)
is the handoff package, not evidence that review occurred.

**P2. No independent implementation/adoption evidence.** The prior
`v0.2.0-alpha` tag exists; older documentation saying there were no tags was
stale. The new alpha adds build provenance, not external implementation evidence.
Independent deployment feedback remains needed.

**P3. Published specifications with no implementation.**
`GAP-AT-FIN-001 / SpendGate` is published under `action-types/` and has zero
corresponding code.

**P4. Advisory type and resource-lifecycle debt.** Type checking is not yet a
release gate; existing embedded-runtime annotations still fail mypy. Python 3.13
also reports unclosed SQLite connections in parts of the legacy test/runtime
setup, alongside dependency deprecations. The reference gateway explicitly
closes its stores, but repository-wide cleanup and a warning-free test baseline
remain work. Passing CI does not imply these advisory checks are clean.
