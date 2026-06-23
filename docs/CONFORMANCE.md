# GAP Conformance & Maturity Statement

This document is the single source of truth for **what the GAP reference
implementation actually enforces and verifies today**, versus what the
specification states as a normative requirement. It exists because the audit's
top finding was a *calibration gap*: the spec's certainty ("cannot",
"structurally incapable", "immutable") exceeded what the artifact delivered. A
deployer must be able to tell a built-and-tested guarantee from an aspiration.

**Read the [protocol specification](PROTOCOL_SPECIFICATION.md) as the normative
standard. Read this matrix for the implementation's earned status.**

## Status legend

| Status | Meaning |
|---|---|
| ✅ **Implemented & Verified** | Enforced in code and covered by passing tests (including adversarial tests where the claim is a safety property). |
| 🟡 **Partial** | A real, working mechanism exists but the full normative requirement is not yet met; the gap is stated. |
| 📋 **Normative / Planned** | A specified requirement not yet implemented, or a property delivered by deployment topology rather than this codebase. |

## Matrix

| Capability / Claim | Status | Evidence (impl → tests) |
|---|---|---|
| **Fail-closed evaluation** — an unrecognized constraint or unhandled category is treated as a violation, never silently allowed (Fix 1 / SA-2) | ✅ Implemented & Verified | `governance/kernel.py` (`_check_constraint_violation`, `_CONSTRAINT_EVALUATORS`) → `tests/test_fail_closed.py` |
| **Strict action typing** — a missing/unregistered `action_type_id` is rejected (Fix 1) | ✅ Implemented & Verified | `governance/kernel.py` (`strict_action_typing`, auto-on under a profile) → `tests/test_fail_closed.py` |
| **Temporal authority fails closed** — a malformed schedule does not silently disable a constraint | ✅ Implemented & Verified | `governance/kernel.py` (`_is_constraint_active`) → `tests/test_fail_closed.py` |
| **Unforgeable decisions** — every decision is kernel-signed; the fabric refuses unsigned/forged/tampered decisions (Fix 2) | ✅ Implemented & Verified | `governance/kernel.py` (`_sign_decision`), `execution/fabric.py` (`_verify_decision_signature`) → `tests/test_decision_integrity.py` |
| **Process isolation of the kernel** — the kernel runs out-of-process behind a constrained API; the agent holds only a client (public key + request channel) | ✅ Implemented & Verified | Genuine boundary: `client/governance_client.py` **SubprocessGovernanceClient** (kernel — with private key + registry — in a separate OS process; the agent holds only the public key + a stdio channel) + `service/kernel_server.py` → `tests/test_kernel_service.py` (real subprocess boundary). **Now the default for the governed path:** `build_governed_deployment(isolated=True)` and `create_app(isolated=True)` run the governed kernel out of process by default — the signed profile + public-key registry cross via a temp file, the child re-verifies the signature and **fails closed on tamper**, and the client is a complete drop-in (evaluate + public key + action-type registry). `isolated=False` is an explicit in-process opt-out for embedding/tests. The `InProcessGovernanceClient` is a same-process **convenience, not** an isolation boundary (documented as such). Hardware/OS-sandbox isolation of the subprocess remains a deployment concern. |
| **Policy Tier 1 regulatory floor** — loaded from a signed, runtime-immutable Applicability Profile; always active; cannot be weakened by lower tiers (Fix 3) | ✅ Implemented & Verified | `governance/profile.py`, `governance/kernel.py` (`_tier1_floor`) → `tests/test_tier_enforcement.py` |
| **Regulatory Constraint Category evaluators** — concrete checks for **all 8** spec categories: data privacy (Cat 1), communications (Cat 2), transparency / AI-interaction disclosure (Cat 3), anti-discrimination / fairness gate (Cat 4), financial / AML+sanctions (Cat 5), healthcare / minimum-necessary PHI (Cat 6), safety boundary (Cat 7), IP/content risk + provenance (Cat 8) | 🟡 Partial | `governance/kernel.py` (`_CONSTRAINT_EVALUATORS`) → `tests/test_regulatory_categories.py` (per-category violation + compliant cases). **Honestly scoped — STRUCTURAL gates, not legal adjudication:** each evaluator checks that the required structural element is *present and declared* on the action (e.g. a fairness evaluation was performed, AML+sanctions screening ran, AI disclosure occurred, PHI access is justified, the safety boundary is respected) — it verifies the **gate**, trusting the (domain-specific) Strategy Layer to populate the metadata truthfully; it does **not** adjudicate whether the fairness result *passed*, whether a disclosure was *adequate*, or whether content infringes. Jurisdiction-specific content within each category is deployment-configured. Numeric thresholds (AML floor, PHI record cap) are read from a **structured `Constraint.threshold` field**, never guessed from the free-text description (so a statutory citation like "45 CFR 164.514" is not mistaken for a threshold); a present-but-malformed amount / record count **fails closed**; the AML gate recognizes a financial transaction only via the declared `transaction_amount` (the Action Type Registry is the primary governed gate on financial action types). An unrecognized constraint still fails closed. |
| **Authorization comparator** — rank-based `granted ≥ required` | ✅ Implemented & Verified | `governance/kernel.py` (`_satisfies_auth`/`_max_auth`) → `tests/test_tier_enforcement.py` |
| **Out-of-Band Authority Verification** — a human approval signature over the Decision Record id+expiry, verified against a registered key, consumed in a persistent replay ledger (Fix 4) | ✅ Implemented & Verified | `execution/fabric.py`, `crypto/signing.py`, `verification/oob_ledger.py` → `tests/test_oob_verification.py` |
| **L2+ approval gating** — L2+ decisions are not auto-executed; execution requires an attached human approval (Fix 4 supply side) | ✅ Implemented & Verified | `strategy/cga_loop.py` (`approve_and_execute`) → `tests/test_approval_gating.py` |
| **Tamper-evident decision lineage** — Ed25519-signed, hash-chained; tampering is detected and cannot be re-sealed without the lineage key (Fix 5) | ✅ Implemented & Verified | `lineage/store.py` → `tests/test_lineage.py` (incl. tamper / recompute-forgery / broken-link) |
| **Governance Integrity Monitoring** — all five signal classes as independent, deterministic rule-based detectors: GIM-1 (authorization drift), GIM-2 (audit-behavior divergence), GIM-3 (threshold-avoidance decomposition), GIM-4 (escalation framing bias), GIM-5 (adversarial collapse) (Fix 6 + rec #4) | 🟡 Partial | `governance/integrity_monitor.py` → `tests/test_gim.py`. GIM-1/GIM-3 are wired into the CGA loop so every decision is observed and signals surface on the result; in **governed mode the loop holds and escalates** an action GIM-1/3 flags (`block_on_integrity`), making them consequential rather than advisory (`tests/test_integration.py`). This is wired onto the **shipped autonomous path**: a governed `create_app` / `ReconcilerLoop` runs the heartbeat with one shared, bounded monitor and `block_on_integrity` on, so a held action is recorded in lineage and routed to a human via `open_escalations` / `GET /escalations/open` (and is resolvable — not a dead letter) (`tests/test_reconciler.py`). GIM-3 detection is **recency-relative** (a burst self-clears once it ages past the window, so a target is never held forever) and the monitor's observation stores are **count-bounded** (`max_history`); a held target counts toward the reconciler's circuit breaker and is not re-queued every cycle. Note GIM-3 currently counts sub-threshold **repetition** within a window (not the spec's composite-vs-sub-task authorization *gap*), so `decomposition_max_level` should sit at/below the reconciler's base action level to avoid flagging routine repeated correction; observation timestamps are wall-clock, so on a simulated clock a deployment should thread logical time. GIM-2/GIM-4/GIM-5 compute the spec's alert math over telemetry the **deployment supplies** (audit mode, escalation framing, challenger outcomes) — the detectors and thresholds are built and tested, but the telemetry feed and the consequential wiring are deployment-side. GIM-2 compares the authorization-classification **distribution** (total-variation distance, catching a mean-preserving shift), mean rank, and escalation rate; GIM-5 enforces the spec's **two-consecutive-period** debounce. **Honestly scoped:** the spec's *independent-model* evaluator for GIM-1/GIM-2 (a different model re-classifying the same inputs) and GIM-2's trace-completeness / challenge-rate metrics remain **Normative / Planned**; what is implemented is the metric math, thresholds, and alerting fed by whatever evaluator the deployment wires in. |
| **Default-safe governed deployment** — a fail-closed posture that REQUIRES the industry-specific regulatory floor and forces on the universal primitives (kernel-signature verification, strict action typing, the SIR gate, a corrigibility kill-switch) | ✅ Implemented & Verified | `governance/deployment.py` `build_governed_deployment()` + `governed=True` on the kernel and loop → `tests/test_deployment.py`. The floor's *content* is industry-specific (deployment config); *requiring* one is universal (enforced — refuses to run without it). The open/prototype constructors remain permissive by design. |
| **Corrigibility / kill-switch** — a human-engaged switch halts the system: the Execution Fabric refuses to dispatch (raising `KillSwitchEngaged`, fail closed, checked first) and the CGA loop refuses to plan, returning a clean `halted` result; CGA does **not** re-plan a path around a halt, including by retargeting. Scoped global or per-entity, and wired through GAP's autonomous heartbeat — the `ReconcilerLoop` and the REST API share one switch (`app.state.kill_switch`) across the loop and fabric (SA-4) | 🟡 Partial | `governance/corrigibility.py` (`KillSwitch`), `execution/fabric.py` (halt check + `KillSwitchEngaged`), `strategy/cga_loop.py` (scope-aware halt, no-retarget), `reconciler/loop.py` + `api/app.py` (shared switch) → `tests/test_corrigibility.py`. **Honestly scoped:** the engage/disengage actor is a free-text label, **not** cryptographically authenticated, and the audit log is in-memory (production binds an operator identity and persists/append-only-signs the log). The structural property — the strategy layer holds no reference to the switch and cannot disengage it — *is* verified. Corrigibility propagation across a sub-agent delegation tree is covered by the multi-agent surface row below. |
| **Multi-agent / sub-agent governance** — delegation cannot amplify authority (a sub-agent's authorization ceiling is rank-bounded by its parent, monotone non-increasing; registering above the parent fails closed), constraints propagate downward and only accumulate, a halt propagates to the whole subtree, and threshold-avoidance decomposition is detected across ≥2 agents (rec #4) | 🟡 Partial | `governance/multi_agent.py` (`SubAgentRegistry`) → `tests/test_multi_agent.py`. **Honestly scoped:** this is a registry/detector the deployment **consults** — `authorize()` / `is_halted()` / `check_cross_agent_decomposition()` express and check the invariants, but wiring them into a live multi-agent executor (so every sub-agent action routes through the bound) is deployment-side, not yet enforced in this codebase's single-agent CGA loop. The single-process delegation tree is in-memory; a distributed sub-agent fabric is **Normative / Planned**. |
| **Self-evolution capability-gain monitoring** — a dynamic monitor over the agent's *realized* self-modifications: SE-VELOCITY (burst of self-modifications), SE-PRIVILEGE (self-modification authorization trending upward — the agent granting itself more privilege), SE-SURFACE (breadth of distinct capabilities modified) (SA-4) | 🟡 Partial | `governance/self_evolution.py` (`SelfEvolutionMonitor`) → `tests/test_self_evolution.py`. Wired **consequentially**: the CGA loop feeds it a self-modification only once that modification has actually **executed** (an approved-but-awaiting-OOB or escalated self-mod gains no capability and is not counted), and under `block_on_integrity` an agent already in a flagged pattern has its next self-modification **held** (`integrity_hold`). Velocity/surface are recency-relative (self-clearing); state is count-bounded; privilege uses GIM-1's drift idiom. Defaulted on in `build_governed_deployment` / governed `create_app` / `ReconcilerLoop`. **Honestly scoped:** deterministic over self-modification *metadata*, not a semantic capability measurement (a model-level capability eval is **Normative / Planned**); on the shipped governed reconciler path the hold is **dormant unless a self-modifying strategy generator is wired** (the default `RuleBasedStrategyGenerator` never emits `skill_modification`). |
| **CGA cannot negotiate around a hard constraint** — re-plan within bounds or escalate, never bypass (SA-1 / SA-5) | ✅ Implemented & Verified | `strategy/cga_loop.py` → `tests/test_adversarial.py` |
| **Structured Uncertainty / Decision Records** | ✅ Implemented & Verified | `models/governance.py`, `governance/kernel.py` → `tests/test_spec_20260220.py` |
| **Reconciler Tiers 1–3** (ML / cognitive / adversarial observation) | 📋 Normative / Planned | Tier 0 (rule-based) implemented; Tiers 1–3 reserved for production. |
| **Structured Intent Resolution (SIR)** | 🟡 Partial | `governance/sir.py`, `models/sir.py` → `tests/test_sir.py`. SIR-1 (five-component declaration), SIR-3 (proportional resolution + readiness gate), SIR-4 (cryptographic seal + decision link), SIR-5 (governed standing declarations: human-authored, expiring) implemented and wired into the CGA loop as an opt-in readiness gate — the loop will not engage until the intent is confirmed/corrected (L1+) or backed by a valid standing declaration (L0) (`tests/test_integration.py`). **Note:** the shipped `ReconcilerLoop` and REST API run the CGA loop *without* the SIR gate (opt-in). SIR-2 meta-intent inference remains a pluggable placeholder. |

## Summary

As of this revision the reference implementation **earns** the core structural
claims it makes for fail-closed evaluation, unforgeable decisions, a signed
immutable regulatory floor, real cryptographic OOB verification with approval
gating, tamper-evident lineage, and non-negotiable hard constraints — each
backed by adversarial tests. The remaining items above are explicitly marked
*Partial* or *Normative/Planned* so no deployer mistakes aspiration for
assurance. The test suite is the live evidence; this matrix is updated as each
phase lands.

## Security review (June 2026)

An adversarial review of this remediation code confirmed 14 defects, all since
fixed and regression-tested:

- decision-signature verification now **fails closed by default** (no kernel key
  configured ⇒ execution refused, unless an explicit prototype opt-out is set);
- execution is **bound to the approved proposal** (a decision authorizes its
  specific proposal, defeating decision/proposal confusion);
- the lineage chain detects **head / tail / whole-chain truncation** via a chain
  anchor (count + genesis + tip), not just per-record/neighbour checks;
- the **OOB approval binds** the decision, proposal, authorization level, and
  approver key into the signed message, with an optional **per-approver authority
  ceiling**; the approval is consumed only **after a successful dispatch**;
- the Tier-1 floor is forced **HARD** (a SOFT floor cannot silently fail to
  enforce); the cost-ceiling evaluator **fails closed** when its amount cannot be
  parsed.

Residual, honestly scoped: the lineage chain anchor lives in the same SQLite as
the records (production anchors it in external/WORM storage); the per-approver
authority ceiling and the kernel-key wiring are deployment configuration; GIM-2 /
GIM-4 / GIM-5 and a separate-model classifier remain Normative / Planned.

### Corrigibility review (SA-4)

A focused adversarial review of the kill-switch primitive confirmed 3 defects,
all since fixed and regression-tested:

- GAP's autonomous heartbeat (`ReconcilerLoop` + the REST API) wired the
  kill-switch into **nothing** — an engaged switch never halted the production
  path. One shared switch now flows through the reconciler, every CGA loop it
  spawns, and the fabric (`app.state.kill_switch`).
- the CGA loop's halt check was **global-only**, so a per-entity halt let the
  loop plan and then crash with an uncaught error, and a "find a path to yes"
  generator could **retarget around** the halt. The loop check is now
  scope-aware (symmetric with the fabric) and refuses to re-plan around a halt.
- the advertised halt exception (`KillSwitchEngaged`) was **never raised** (the
  fabric raised a generic `ExecutionError`); it is now the actual halt signal and
  subclasses `ExecutionError` so existing handlers still catch it.

The 7 rejected findings were correctly rejected: the execution model is
synchronous and single-threaded (no reachable TOCTOU), and the authority/audit
"over-claims" are honestly-disclosed skeleton boundaries (no operator
authentication, in-memory log) already scoped in the matrix row above.
