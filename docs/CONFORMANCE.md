# GAP Conformance & Maturity Statement

This document is the single source of truth for **what the GAP reference
implementation actually enforces today**, versus what the
[specification](PROTOCOL_SPECIFICATION.md) states as a normative requirement.

It exists because the project's own top audit finding was a *calibration gap*:
the specification's certainty ("cannot", "structurally incapable", "immutable")
exceeded what the artifact delivered. A deployer must be able to tell a
built-and-tested guarantee from an aspiration.

**Read the specification as the normative standard. Read this matrix for the
implementation's earned status.** Where the README and this document disagree,
this document wins.

For the adversary this implementation does and does not defend against, read
the [Threat Model](THREAT_MODEL.md). For what is known-broken right now, read
[KNOWN_GAPS.md](KNOWN_GAPS.md).

## Status legend

| Status | Meaning |
|---|---|
| ✅ **Enforced** | Enforced in code on the shipped governed path, covered by passing tests including adversarial ones where the claim is a safety property. |
| 🟡 **Partial** | A real, working mechanism exists but does not meet the full normative requirement. The gap is stated in the row. |
| ⚪ **Built, unfed** | The mechanism is implemented and unit-tested, but nothing on the shipped path supplies its input, so it does not fire in a default deployment. |
| 🔧 **Deployment-configured** | Delivered by deployment topology, not by this codebase. The code provides the seam; the deployer provides the guarantee. |
| 📋 **Normative / Planned** | Specified but not implemented. |

Baseline: **550 tests**, 93% line coverage, CI-enforced 90% floor, on Python
3.11–3.13 (Linux) and 3.13 (Windows).

---

## Kernel evaluation

| Capability / Claim | Status | Evidence |
|---|---|---|
| **Fail-closed evaluation** — an unrecognized constraint or unhandled category is a violation, never a silent pass | ✅ Enforced | `governance/kernel.py` (`_check_constraint_violation`, `_CONSTRAINT_EVALUATORS`) → `tests/test_fail_closed.py` |
| **Strict action typing** — a missing or unregistered `action_type_id` is rejected | ✅ Enforced | `governance/kernel.py` (`strict_action_typing`, auto-on under a profile) → `tests/test_fail_closed.py` |
| **The kernel owns its clock** — a caller-supplied `current_time` cannot deactivate a schedule-gated constraint | ✅ Enforced | A governed kernel ignores the parameter and reads `utcnow()` (`_resolve_time`); the RPC no longer carries it at all. Previously an agent could name a time outside a cron window, drop the constraint from `_get_active_constraints`, and receive a genuinely kernel-signed approval backdated a year. → `tests/test_registry_integrity.py`, `tests/test_boundary_hardening.py` |
| **Temporal authority fails closed** — a malformed schedule does not silently disable a constraint | ✅ Enforced | `governance/kernel.py` (`_is_constraint_active`) → `tests/test_fail_closed.py` |
| **World-model-backed constraints fail closed on an untracked target** | ✅ Enforced | The GDPR and contact-hours gates branched on `if entity:` with no else, and the agent authors `action.target` — so it chose whether the gate had anything to evaluate. Untracked targets are now a violation in governed mode (`allow_untracked_targets`, default False when governed). → `tests/test_registry_integrity.py` |
| **Governance-relevant evidence must be attested** | 🟡 Partial — **and the previous edition of this row overstated it** | The HTTP path is closed: `POST /world/ingest` accepted arbitrary properties unauthenticated, and re-ingesting an entity with `gdpr_consent: true` turned a rejected proposal into a kernel-signed approval. `gdpr_consent`, `geo`, `jurisdiction` and `local_hour` now carry store-stamped provenance, unattested values are unevaluable and therefore a violation, and stamping is downgrade-only. → `tests/test_world_model_trust.py`. **What that does not cover:** the stamp is an unsigned dict, and a caller that assembles the `WorldModel` itself never passes through the store. In the isolated posture the agent authors the `world_state` field of the `evaluate` request, so a hand-written stamp reads as attested — reproduced, `rejected ['gdpr_consent_required']` becomes `approved []`. This row was previously marked Enforced on the strength of the HTTP reproduction alone; that verification tested the wrong path. Closing it requires the evidence to be signed by a key the agent side does not hold. See [KNOWN_GAPS](KNOWN_GAPS.md). |
| **Authorization comparator** — rank-based `granted ≥ required` | ✅ Enforced | `governance/kernel.py` (`_satisfies_auth`/`_max_auth`) → `tests/test_tier_enforcement.py` |
| **Policy Tier 1 regulatory floor** — loaded from a signed, runtime-immutable Applicability Profile, always active, not weakenable by lower tiers | ✅ Enforced | `governance/profile.py`, `governance/kernel.py` (`_tier1_floor`) → `tests/test_tier_enforcement.py`. The floor's *content* is deployment-specific; *requiring* one is enforced. |
| **Regulatory Constraint Category evaluators** — all 8 spec categories | 🟡 Partial | `governance/kernel.py` (`_CONSTRAINT_EVALUATORS`, 9 entries covering 8 categories plus a cost ceiling) → `tests/test_regulatory_categories.py`. **Honestly scoped — these are STRUCTURAL gates, not legal adjudication.** Each evaluator checks that a required element is present and declared (a fairness evaluation was performed, AML/sanctions screening ran, AI disclosure occurred, PHI access is justified). It does not adjudicate whether the fairness result *passed*, whether a disclosure was *adequate*, or whether content infringes. Numeric thresholds come from the structured `Constraint.threshold` field, never parsed from free text; a present-but-malformed value fails closed. |
| **Structured Uncertainty / Decision Records** | ✅ Enforced | `models/governance.py`, `governance/kernel.py` → `tests/test_spec_20260220.py` |
| **Multi-Phase Authorization** — authorizing intent does not pre-authorize outcome | ✅ Enforced | `governance/kernel.py` (`evaluate_phase`) → `tests/test_spec_20260220.py` |
| **Intent-conflict detection** | 🟡 Partial | `_detect_intent_conflicts` probes other intents' hard constraints against a synthetic **empty** `WorldModel`, so it deliberately keeps the permissive posture rather than reporting a conflict for every world-model-backed constraint. The authoritative evaluation is the real one in `_evaluate`. `resolve_intent_conflict` returns `hard_constraints_preserved: True` unconditionally — an assertion, not a verified invariant, and it is written into the lineage record. |

## Registry integrity

| Capability / Claim | Status | Evidence |
|---|---|---|
| **The Action Type Registry is governance configuration, not runtime state** | ✅ Enforced | `register_action_type` carried the docstring "autonomous systems cannot register new types" and performed no check — a bare dict write that could silently **overwrite** a baseline type, so re-registering `skill_modification` at L0 stripped its L2 approval gate. The registry now rides inside the signed `ApplicabilityProfile`, so it crosses the process boundary signed and fails closed on tamper. A governed kernel refuses runtime registration outright; open mode is a monotonic ratchet that cannot replace a type or register below the risk-derived floor. → `tests/test_registry_integrity.py` |
| **No governance-mutation surface on the RPC boundary or over HTTP** | ✅ Enforced | The `register_action_type` RPC branch, both client proxies, and `POST /governance/action-types` are **deleted**. The boundary exposes `evaluate`, `get_public_key`, `list_action_types`, `get_action_type` — reads only. → `tests/test_boundary_hardening.py` |
| **Curated risk metadata is consequential** | 🟡 Partial | `RiskProfile` on an action type informs the risk-derived floor used by the registration ratchet. It is not yet a floor on every evaluation path. |

## Decision integrity and execution

| Capability / Claim | Status | Evidence |
|---|---|---|
| **Unforgeable decisions** — every decision is kernel-signed; the fabric refuses unsigned, forged or tampered decisions | ✅ Enforced | `governance/kernel.py` (`_sign_decision`), `execution/fabric.py` (`_verify_decision_signature`) → `tests/test_decision_integrity.py`. Which key signed is now **inside** the signed payload (`kernel_public_key_id` left the exclusion set); domain tag `gap.governance.decision.v2`. |
| **Decisions are single-use at every authorization level** | ✅ Enforced | The fabric kept no record of what it had executed — every guard was stateless, and the OOB ledger was consulted only for L2+, so one signed risk-3 decision executed **4 times out of 4**. `GovernanceDecision` now carries `nonce` and `expires_at` inside the signed payload, and `verification/execution_ledger.py` claims the nonce before dispatch at every level. A failed execution stays resumable; a completed one is spent. → `tests/test_replay_protection.py` |
| **Execution is bound to the approved proposal** | ✅ Enforced | `execution/fabric.py` (proposal id + content digest) → `tests/test_decision_integrity.py` |
| **Out-of-Band Authority Verification** — a human approval signed over the decision, verified against a registered key, consumed in a persistent replay ledger | ✅ Enforced | `execution/fabric.py`, `crypto/signing.py`, `verification/oob_ledger.py` → `tests/test_oob_verification.py`. The approval is now **reserved before dispatch** and settled after, closing the check-then-consume window. |
| **A partial-failure retry does not re-run completed side effects** | ✅ Enforced | Per-action completion tracking; a resumed execution skips actions that already succeeded under the same human approval. → `tests/test_replay_protection.py` |
| **L2+ approval gating** — L2+ decisions are not auto-executed | ✅ Enforced | `strategy/cga_loop.py` (`approve_and_execute`) → `tests/test_approval_gating.py` |
| **Durable replay protection in a governed deployment** | ✅ Enforced | Both ledgers default to `:memory:` in open mode, which meant replay protection evaporated on restart. `build_governed_deployment` now **raises** without a real ledger directory; `:memory:` requires an explicitly named prototype flag. → `tests/test_deployment_defaults.py` |

## Isolation

| Capability / Claim | Status | Evidence |
|---|---|---|
| **The signing key and governance configuration are out of process** | ✅ Enforced | `client/governance_client.py` (`SubprocessGovernanceClient`) + `service/kernel_server.py`, default for the governed path → `tests/test_kernel_service.py` |
| **The kernel resolves its own trust root** | ✅ Enforced | Previously circular: the agent-side parent supplied **both** the signed profile and the public keys verifying it, so the child verified the attacker's signature against the attacker's key. The child now resolves `GAP_TRUST_ROOT` — a path the deployer owns — and with a trust root in force the registry the parent hands down is deliberately empty. Required by default; absence **raises**. → `tests/test_deployment_defaults.py` |
| **Persistent, pinnable kernel identity** | ✅ Enforced | The keypair was regenerated every process start, so no external auditor could pin it. It now persists, and the subprocess client pins it. Detects child substitution and silent key regeneration; does **not** defend against an adversary already executing code in the parent. |
| **Boundary robustness** — correlated, bounded, non-leaking | ✅ Enforced | Two concurrent evaluations could each receive **the other's signed decision** (no lock, no request id, sync routes on one shared client from a threadpool). Now request-id correlated under a lock, failing closed and killing the child on desync. Requests are size-bounded (the read was unbounded — the agent could OOM the governance authority); errors are stable codes rather than exception text carrying paths and field values; the child's stdout is off the protocol channel. → `tests/test_boundary_hardening.py` |
| **Structural isolation of governance from the agent** (spec §Layer 1) | 🟡 Partial | **This is the requirement the implementation meets least completely, and the most important row in this document.** The kernel's key and configuration are isolated. Signature *verification* is not: `ExecutionFabric` is constructed in the agent's process and holds the pinned kernel key and the unsigned-decisions escape hatch as ordinary mutable attributes, alongside the executor registry carrying tool credentials, the kill switch, the world model and the lineage store. An adversary with **code execution in the agent process** bypasses governance without forging anything — overwrite the pinned key, flip the flag, or call an executor directly. The boundary bounds an attacker who supplies governance *data*; it does not bound one who runs code beside the verifier. A socket/attach execution boundary that would close this is **not built**. See [Threat Model §4](THREAT_MODEL.md). |
| **OS-level sandboxing of the kernel subprocess** | 🔧 Deployment-configured | Running the kernel as a separate OS user is what makes the private key genuinely unreachable. Same-user isolation is hygiene, not a boundary. |

## Audit trail

| Capability / Claim | Status | Evidence |
|---|---|---|
| **Tamper-evident decision lineage** — Ed25519-signed, hash-chained; tampering is detected and cannot be re-sealed without the lineage key | ✅ Enforced | `lineage/store.py` → `tests/test_lineage.py` (tamper, recompute-forgery, broken-link), `tests/test_lineage_identity.py` |
| **Concurrent appends cannot drop records** | ✅ Enforced | `append` was an unsynchronized read-modify-write on a `check_same_thread=False` connection reached from a threadpool, so concurrent appends silently **dropped audit records** and permanently broke verification. Now lock-serialized under `BEGIN IMMEDIATE`. This refutes the previous edition's claim of "no reachable TOCTOU". → `tests/test_lineage_identity.py` |
| **Signatures survive schema evolution** | ✅ Enforced | Verification ran against a re-serialization of the live Pydantic class, so any future model field silently invalidated the entire history. Canonical signed bytes are now persisted with a `schema_version`. |
| **Signed chain anchor** — truncation detection cannot itself be rewritten | 🟡 Partial | The anchor (count + genesis + tip) is now Ed25519-signed, defeating an attacker who can write the database but does not hold the lineage key. It does **not** defeat one holding both — and the anchor still lives in the same SQLite file as the records it anchors, with the lineage key held by the same process that owns the database. There is no independent witness. |
| **External/WORM anchoring** | 📋 Normative / Planned | The real answer to the row above. |
| **Precise audit queries** | ✅ Enforced | `query_by_entity` built a `LIKE '%{id}%'` with no ESCAPE, so `%` and `_` in an entity id acted as wildcards and a raw-JSON substring match returned records merely mentioning the id elsewhere — an audit query silently returning wrong results. |
| **Output Artifact Provenance** | 📋 Normative / Planned | `ArtifactProvenance` exists as an optional model field constructed only in tests. Nothing on any execution path computes an integrity hash or attaches provenance to a produced output. *Previously claimed in the README as a built mechanism; retracted.* |
| **Separation of Creation and Validation** | 📋 Normative / Planned | `validation_independent` is a self-declared boolean defaulting to `False`. Nothing refuses or flags an artifact whose producer and validator are the same entity. *Previously claimed as "Independence is structural"; retracted.* |

## Corrigibility and containment

| Capability / Claim | Status | Evidence |
|---|---|---|
| **Kill-switch halts planning and dispatch, fail-closed** | ✅ Enforced | `governance/corrigibility.py`, `execution/fabric.py` (`KillSwitchEngaged`, checked first), `strategy/cga_loop.py` (scope-aware, refuses to re-plan or retarget around a halt), shared through `ReconcilerLoop` and the REST app → `tests/test_corrigibility.py`. The structural property — the strategy layer holds no reference to the switch and cannot disengage it — is verified. |
| **Operator authentication on the kill switch** | 🔧 Deployment-configured | The engage/disengage actor is a **free-text label**, not an authenticated identity, and the audit log is in-memory. Any in-process holder of the switch can flip it. Production binds an operator identity at the control plane and persists an append-only log. |
| **The autonomous heartbeat survives bad input** | ✅ Enforced | `reconcile_once` had no exception containment: one entity carrying a timezone-aware `created_at` — the normal output of any real CRM, reachable through the ingest endpoint — raised, propagated out of `run_async`, and stopped governance **permanently and silently**. Failures are now contained per entity and recorded; the cycle degrades rather than the loop. → `tests/test_reconciler_resilience.py`, `tests/test_time_boundary.py` |
| **The circuit breaker is observable and recoverable** | ✅ Enforced | It was a permanent, silent terminal state with no reset path. An entity in `awaiting_approval` also reset the failure counter, so the breaker could never trip on it and the loop re-planned forever, appending to the lineage chain every cycle. |
| **Escalations are not dead letters** | ✅ Enforced | Dedupe keyed on `entity_id` alone silently swallowed a GIM `integrity_hold` when any escalation was already open on that entity. The reason is now part of the key. |
| **Multi-agent / sub-agent governance** — delegation cannot amplify authority; constraints and halts propagate down the tree | 🟡 Partial | `governance/multi_agent.py` + `execution/sub_agent_executor.py` enforce ceilings, constraint accumulation and subtree halts **at dispatch** for any sub-agent routed through them → `tests/test_multi_agent.py`, `tests/test_sub_agent_executor.py`. Routing a *particular* orchestrator's sub-agents through that executor is deployment-side, and the delegation tree is in-memory and single-process. A distributed sub-agent fabric is Planned. |

## Monitoring

| Capability / Claim | Status | Evidence |
|---|---|---|
| **GIM-1 authorization drift, GIM-3 threshold-avoidance decomposition** | ✅ Enforced | Wired into the CGA loop; under `block_on_integrity` (on by default in a governed deployment) a flagged action is **held and escalated**, not merely logged → `tests/test_gim.py`, `tests/test_integration.py`. Monitors now observe **every action** in a proposal; they previously saw only `actions[0]`, so an agent could pack the sensitive action second. GIM-3 counts sub-threshold *repetition* within a window, not the spec's composite-vs-subtask authorization *gap*. |
| **GIM-2 audit-behavior divergence, GIM-4 escalation-framing bias** | ⚪ Built, unfed | The metric math, thresholds and alerting are implemented and unit-tested. Both consume telemetry the **deployment** supplies (audit mode; how an escalation's options were framed). Nothing on the shipped path supplies it, so they do not fire in a default deployment. |
| **GIM-5 adversarial collapse** | ⚪ Built, unfed | Implemented with the spec's two-consecutive-period debounce; no shipped path supplies challenger outcomes. |
| **"Five independent detectors"** | 🟡 Partial | Two fire on the shipped path. Three are built and unfed. The README previously implied all five ran; corrected. |
| **GIM independence** | 🟡 Partial | The monitor runs **inside the system it monitors** and reports back into it, which does not satisfy the GIM specification's independence requirements. The spec's independent-model evaluator is Planned. |
| **Independent authorization review** | 🟡 Partial | `governance/independent_review.py` re-classifies required authorization by a different algorithm (risk signals, not the registry), so it cannot inherit the kernel's blind spots. **It is a keyword heuristic and is paraphrase-evadable — renaming an action defeats it.** A model-backed classifier is the pluggable adapter point and remains Planned. |
| **Self-evolution capability-gain monitoring** | 🟡 Partial | `governance/self_evolution.py` counts only *realized* (executed) self-modifications; under `block_on_integrity` an agent in a flagged pattern has its next self-modification held. Deterministic over modification *metadata*, not a semantic capability measurement. **Dormant on the default reconciler path**: the reference `RuleBasedStrategyGenerator` never emits `skill_modification`. |
| **Dynamic risk escalation** | ⚪ Built, unfed | `governance/dynamic_risk.py` implements volume, scope and cascade detectors, but its baseline/signal feed methods are called by **nothing** on any shipped path, so no escalation it defines can currently fire. Feeding it must not reintroduce a governance-config write on the RPC boundary. |

## Intent

| Capability / Claim | Status | Evidence |
|---|---|---|
| **Structured Intent Resolution (SIR)** | 🟡 Partial | SIR-1 (five-component declaration), SIR-3 (proportional resolution + readiness gate), SIR-4 (cryptographic seal), SIR-5 (governed standing declarations) are implemented and wired into the CGA loop as an **opt-in** readiness gate → `tests/test_sir.py`, `tests/test_integration.py`. SIR-2 meta-intent inference is a deterministic keyword heuristic, surfaced for a human to confirm or correct; it is advisory, not a gate. |
| **The SIR gate on the shipped path** | ⚪ Built, unfed | The shipped `ReconcilerLoop` and REST API run the CGA loop **without** the SIR gate. |
| **`declared_boundaries` enforcement** | 📋 Normative / Planned | SIR captures "what the system will NOT do" and **nothing ever checks a proposal against it**. `verify_seal` has zero callers in the codebase. |

## Strategy layer

| Capability / Claim | Status | Evidence |
|---|---|---|
| **CGA cannot negotiate around a hard constraint** — replan within bounds or escalate, never bypass | ✅ Enforced | `strategy/cga_loop.py` → `tests/test_adversarial.py` |
| **The strategy layer cannot launder state into governance** | ✅ Enforced | The live mutable `WorldModel` was handed to the pluggable (untrusted, deployment-supplied) generator, which could set `gdpr_consent = True` and have the next evaluation read it — laundering with no actions at all. The generator now receives a deep copy. → `tests/test_strategy_isolation.py` |
| **Proposer/Critic adversarial pair** (spec §CGA step 3) | 📋 Normative / Planned | `grep -i "critic\|proposer"` over `gap_kernel/` returns nothing. The reference generator is a deterministic rule ladder. *Previously described in the README as the shipped mechanism; retracted.* |
| **Reconciler Tiers 1–3** (ML / cognitive / adversarial observation) | 📋 Normative / Planned | Tier 0 (rule-based) is implemented. |
| **The Iron Rule** — learning never modifies governance policy | ✅ Enforced | Structural with respect to the learning engine: `learning/engine.py` has no write path to governance configuration. It does **not** hold against an adversary with code execution in the agent process — see the isolation row above. |

---

## Known regressions and migration

- **Decisions signed under the v1 domain tag no longer verify.** Moving
  `kernel_public_key_id` into the signed payload and bumping to
  `gap.governance.decision.v2` was a deliberate one-way change. Nothing in the
  repository migrates historical decisions, including any already persisted
  inside a lineage record.
- **The mutating HTTP routes no longer ship by default.** A deployment that
  relied on `POST /intents`, `POST /world/ingest`, `PUT /reconciler/config` or
  the learning/escalation writers must pass
  `create_app(enable_mutating_routes=True)` and put its own authenticated proxy
  in front. `POST /governance/action-types` is gone permanently.
- **A governed deployment now raises without a trust root and a ledger
  directory.** Both were previously implicit and unsafe.

## What this implementation does not claim

Stated plainly, because the alternative is the calibration gap this document
exists to close:

1. It does not defend against an adversary with code execution in the agent
   process. That is the architecture, not a bug backlog item.
2. It has had **no external security review**. Every finding fixed here was
   found by its own maintainers and tooling.
3. It has **no adopters, no tagged release, and one author**. Nothing here has
   been tested by an independent implementer.
4. Its regulatory evaluators check structure, not legal compliance. No
   regulator has reviewed them, and alignment tables elsewhere describe design
   intent, not certification.
