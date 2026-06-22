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
| **Process / hardware isolation of the kernel** — the kernel runs out-of-process; the agent cannot `import` it | 📋 Normative / Deployment | The signing boundary means the execution layer needs only the kernel's *public* key, never the kernel object — making an out-of-process split straightforward — but the reference impl is single-process. |
| **Policy Tier 1 regulatory floor** — loaded from a signed, runtime-immutable Applicability Profile; always active; cannot be weakened by lower tiers (Fix 3) | ✅ Implemented & Verified | `governance/profile.py`, `governance/kernel.py` (`_tier1_floor`) → `tests/test_tier_enforcement.py` |
| **Authorization comparator** — rank-based `granted ≥ required` | ✅ Implemented & Verified | `governance/kernel.py` (`_satisfies_auth`/`_max_auth`) → `tests/test_tier_enforcement.py` |
| **Out-of-Band Authority Verification** — a human approval signature over the Decision Record id+expiry, verified against a registered key, consumed in a persistent replay ledger (Fix 4) | ✅ Implemented & Verified | `execution/fabric.py`, `crypto/signing.py`, `verification/oob_ledger.py` → `tests/test_oob_verification.py` |
| **L2+ approval gating** — L2+ decisions are not auto-executed; execution requires an attached human approval (Fix 4 supply side) | ✅ Implemented & Verified | `strategy/cga_loop.py` (`approve_and_execute`) → `tests/test_approval_gating.py` |
| **Tamper-evident decision lineage** — Ed25519-signed, hash-chained; tampering is detected and cannot be re-sealed without the lineage key (Fix 5) | ✅ Implemented & Verified | `lineage/store.py` → `tests/test_lineage.py` (incl. tamper / recompute-forgery / broken-link) |
| **Governance Integrity Monitoring** — GIM-1 (authorization drift) + GIM-3 (threshold-avoidance decomposition), independent rule-based detectors (Fix 6) | 🟡 Partial | `governance/integrity_monitor.py` → `tests/test_gim.py`. GIM-2 / GIM-4 / GIM-5 and a separate-model classifier are **Normative / Planned**. |
| **CGA cannot negotiate around a hard constraint** — re-plan within bounds or escalate, never bypass (SA-1 / SA-5) | ✅ Implemented & Verified | `strategy/cga_loop.py` → `tests/test_adversarial.py` |
| **Structured Uncertainty / Decision Records** | ✅ Implemented & Verified | `models/governance.py`, `governance/kernel.py` → `tests/test_spec_20260220.py` |
| **Reconciler Tiers 1–3** (ML / cognitive / adversarial observation) | 📋 Normative / Planned | Tier 0 (rule-based) implemented; Tiers 1–3 reserved for production. |
| **Structured Intent Resolution (SIR)** | 🟡 Partial | `governance/sir.py`, `models/sir.py` → `tests/test_sir.py`. SIR-1 (five-component declaration), SIR-3 (proportional resolution + readiness gate), SIR-4 (cryptographic seal + decision link), SIR-5 (governed standing declarations: human-authored, expiring) implemented. SIR-2 meta-intent inference is a pluggable placeholder, and the resolver is standalone (not yet wired into the CGA loop). |

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
