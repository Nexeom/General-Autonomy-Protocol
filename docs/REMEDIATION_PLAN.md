# GAP Remediation Roadmap — "Build to Earn Every Claim"

## Context

**Why this exists.** The March 2026 governance & safety audit
([`gap-governance-safety-audit-2026-03.md`](gap-governance-safety-audit-2026-03.md))
found that GAP has a strong conceptual model wrapped in structural-guarantee
language the artifact does not yet earn: ~70% of the load-bearing safety
mechanisms are spec-only or fail **open**. The audit's headline is that *the
spec/implementation gap is itself the top governance failure* — a deployer
reading the README would conclude the system *cannot* be configured to break the
law, *cannot* have its governance bypassed, and *is* continuously monitored, none
of which the current artifact does.

**Decision (2026-06-22).** Close the gap by **building** the missing mechanisms so
the "structurally incapable / cannot / immutable" claims become literally true —
*not* by re-leveling the prose.

**Inputs that make this concrete.** Three artifacts already did the expensive
analysis:
1. The audit's 7 ordered remediations (Fix 1–7) — `gap-governance-safety-audit-2026-03.md`.
2. The verified cross-reference (June 2026) of where reusable primitives live
   across 8 Nexeom repos — same file, CROSS-REFERENCE section + addendum.
3. A fresh, file-grounded map of GAP's *current* kernel (June 2026) — captured
   below; the audit's original line numbers were stale, these are verified against
   the head of `claude/gap-kernel-implementation-25Zt0`.

**Intended outcome.** A sequenced, dependency-ordered build program where each
phase has exact targets, a verified reuse source, and a test that proves the claim
now holds.

---

## Current state (verified against branch HEAD)

| Fix | Mechanism | Current state | Key target(s) |
|---|---|---|---|
| 1 | Fail-closed defaults | **Fails open.** `_generic_constraint_check` returns `False` (unknown constraint = satisfied); `action_type_id` is `Optional` and the registry gate is skipped when it's `None`; only 3 constraint categories have evaluators. | `gap_kernel/governance/kernel.py:203`, `:582`, `:596`, `:123-134`; `gap_kernel/api/app.py` evaluate endpoint |
| 2 | Structural isolation (Iron Rule) | **Convention only.** `GovernanceKernel` is an ordinary importable class in one process; `_dynamic_risk_engine` is reachable/mutable; strategy calls the kernel by direct method call; execution checks a verdict *enum*, not a signature. | `gap_kernel/governance/kernel.py:443`; `gap_kernel/strategy/cga_loop.py:303`; `gap_kernel/execution/fabric.py` execute; `gap_kernel/api/app.py` (optional wrapper) |
| 3 | Policy tier enforcement | **Spec-only.** `Constraint` has no `tier` field; no `PolicyTier` enum; auth compare is inline `.value >`; no signed/immutable Tier-1 profile. | `gap_kernel/models/intent.py:24-30`; `gap_kernel/governance/kernel.py` auth-level + override logic |
| 4 | Real OOB verification | **Stub.** `_verify_oob_authority` checks that two string fields are non-empty; replay protection is an in-memory `set`. No cryptographic signature, no persistence. | `gap_kernel/execution/fabric.py:122-157`; `gap_kernel/models/governance.py` OOB fields |
| 5 | Signed external lineage | **Partial.** SHA-256 hash chain (not a signature), SQLite `:memory:` default (no external anchor / WORM), in-process verifier, **zero tamper tests**. | `gap_kernel/lineage/store.py:30`, `:84`, `:186-217`; `tests/test_lineage.py` |
| 6 | Governance Integrity Monitoring | **Not started.** 0 lines; spec only in `docs/gap-governance-integrity-monitoring.md` (GIM-1..GIM-5). SIR likewise spec-only. | new `gap_kernel/governance/integrity_monitor.py`; `tests/test_gim.py` |
| 7 | Conformance / maturity statement | **Missing.** The spec asserts structural guarantees throughout with no normative-vs-verified distinction (the public `README.md` carries condensed echoes). | `docs/PROTOCOL_SPECIFICATION.md:57,120,126,131,188-212,234,365,435-436,558`; new conformance matrix |
| SA-5 | Adversarial assurance | **Absent.** 133 tests, ~95% happy-path; no tamper test, no constraint-negotiation test, no kernel-bypass test. | `tests/` (new adversarial suite) |

Reconciler Tiers 1–3 (ML/cognitive/adversarial) are intentionally "reserved for
production" (`gap_kernel/reconciler/loop.py`) and are **out of scope** here; Tier 0
rule-based drift is implemented and sufficient for this program.

---

## Build sequence (dependency-ordered)

The audit ranked fixes by leverage; "build to earn" adds real dependencies (a
shared crypto substrate underpins OOB, signed lineage, signed decisions, and the
signed Tier-1 profile). Phases are ordered so each builds on the last. Effort uses
the audit's own S/M/L sizing.

### Phase A — Fail-closed foundation (Fix 1) · **S** · no deps
The cheapest, highest-safety change; everything else assumes the evaluator denies
by default.
- `kernel.py:203` `_generic_constraint_check` → return `True` (unknown constraint = violation).
- `kernel.py:596` make the Action Type Registry check unconditional; reject when `action_type_id` is absent/unregistered (drop the `Optional` bypass, `kernel.py:582`).
- `kernel.py:123-134` add a fail-closed fallback for unhandled constraint *categories* (the 5 spec-only regulatory categories must deny, not pass).
- `api/app.py` require `action_type_id` in the evaluate request.
- **Reuse:** pattern from `Evairi-Master/packages/adk-core/src/action-classification.ts` (declared tier = upper bound; recompute; reject if `computed > declared`).
- **Verify:** new tests asserting unknown constraints, missing `action_type_id`, and unhandled categories all → `REJECTED`.

### Phase B — Cryptographic substrate (Fix 4) · **M** · no deps
Build the Ed25519 sign/verify module + persistent replay ledger once; Phases C, D,
E reuse it.
- New `gap_kernel/crypto/signing.py` — Ed25519 `sign(decision_id)` / `verify(decision_id, sig, pubkey)`.
- New `gap_kernel/verification/oob_ledger.py` — append-only SQLite (UNIQUE on `decision_id,signature`) replacing the in-memory `set`.
- `models/governance.py` — add `human_approval_signature`, `human_approver_public_key_id`, `human_approval_timestamp`, `human_approval_valid_until`.
- `fabric.py:122-157` — replace string-presence checks with: signature present → freshness window → **cryptographic verify over the Decision Record ID** → persistent replay check.
- **Reuse (Python, tamper-tested — closest to drop-in):** `Evolving Agent V1 — …(V2)/identity/signing_engine.py` (Ed25519). Pattern ref: `Nexeom-Protocol-Library/evairi/.../marketplace/verification.ts` (verify over canonical input).
- **Verify:** forged signature rejected; expired approval rejected; replay rejected across a fresh `ExecutionFabric`.

### Phase C — Tier enforcement (Fix 3) · **M** · depends on B
- `models/intent.py` — add `PolicyTier` enum (1 regulatory_floor / 2 org_policy / 3 operational) and `tier` on `Constraint`.
- `kernel.py` — add a real ordering comparator (`satisfies(granted, required)` via an explicit rank map) and enforce **Tier 3 ≤ Tier 2 ≤ Tier 1** at evaluation time (fail-closed if violated).
- New Tier-1 profile loader that reads an **Ed25519-signed, runtime-immutable Applicability Profile** (verified via Phase B), so the regulatory floor cannot be weakened in-process.
- **Reuse:** `Evairi-Master/packages/contracts/src/authz.ts:44-57` (`AUTHZ_RANK` + `satisfiesAuthzRequirement`, *called* at `governance/src/gap.ts:184`) — the missing piece GAP must add is the signed/immutable profile wrapper around it.
- **Verify:** a config attempting Tier 3 below Tier 1 is rejected; a tampered (unsigned) profile is rejected; `granted < required` denies.

### Phase D — Signed external lineage + tamper tests (Fix 5) · **M** · depends on B
- `lineage/store.py:84` — replace bare SHA-256 with an HMAC/Ed25519 signature over each record (reusing Phase B); keep the prior-hash chain.
- Move off `:memory:` to an append-only/WORM-style store with an external anchor option; make `verify_chain_integrity` runnable by an independent verifier.
- `tests/test_lineage.py` — add the missing **tamper tests** (mutate a record / break a `prior_record_hash` → assert verification fails). Closes part of SA-5.
- **Reuse:** `AiTrium/packages/decision-lineage-verifier` (`verify.ts` + `hmac.ts`, importable HMAC hash-chain + tamper tests); `Xstatic/.../substrate-stss/transparency-log.ts` (immutability triggers).
- **Verify:** the tamper tests above; the "tamper-evident" README claim becomes adversarially checked.

### Phase E — Structural isolation boundary (Fix 2) · **L** · depends on B
The hardest lift; makes "a path that does not exist" true.
- Run the kernel as a **separate service** (subprocess/Unix-socket/HTTP) — `gap_kernel/service/kernel_server.py` + `gap_kernel/client/governance_client.py`.
- Strategy/reconciler consume the **client**, not an imported class (`cga_loop.py:303`, `reconciler/loop.py`); remove the in-process import path / optional kernel from `api/app.py`.
- **Sign every `GovernanceDecision`** (Phase B); `fabric.py` execute verifies the kernel signature before acting (forged decisions rejected).
- **Reuse:** `AtriumDesktopBridge/server/cognitiveCore.ts` (out-of-process JSON-RPC/stdio skeleton); `Nexeom-Protocol-Library/evairi/.../runtime-host/src/capabilities.ts` (capability-based access); `…/governance/src/iron-rule.ts` (DB-layer RESTRICT trigger for the audit store).
- **Verify:** adversarial test — in-process code cannot forge or mutate a decision the executor will accept; the kernel is not importable from the agent process.

### Phase F — Skeletal GIM (Fix 6) · **L** · depends on E
- New `gap_kernel/governance/integrity_monitor.py` implementing at least **GIM-1 (authorization drift)** and **GIM-3 (threshold-avoidance decomposition)** as real detectors over real decision/lineage data.
- Run the monitor **out-of-process with model/key independence** (leverages Phase E) to satisfy G-3 (no correlated blind spots).
- `tests/test_gim.py` — drift + decomposition detection tests.
- **Reuse:** `Evairi-Engine/packages/gim/` (`threshold-avoidance.ts`, `authorization-drift.ts`, 128/128 tests) + `…/foundry/src/drift/zscore.ts` (z-score + chi-square) — adapt the algorithms to GAP's Python contracts.
- **Verify:** a decomposition that keeps sub-tasks one tier below threshold is flagged; sustained downward auth drift is flagged.

### Phase G — Adversarial assurance + conformance (SA-5 + Fix 7) · **S–M** · cross-cutting, finalized last
- Build the adversarial suite that proves the claims: lineage tamper, CGA negotiating around a hard constraint (must fail), threshold-decomposition (Phase F), OOB spoof/replay (Phase B), kernel-bypass/forged-decision (Phase E).
- **Fix 7 as a *living* conformance matrix:** seed it at Phase A start (every claim tagged *normative requirement* vs *implemented & verified*), and flip rows to "verified" as each phase lands. Because we are *earning* the claims, the strong specification language (`docs/PROTOCOL_SPECIFICATION.md:57,120,131,190,194,206,234,435-436,558`, echoed in `README.md`) stays — but is gated behind the matrix until its phase is green.
- **Verify:** full regression (currently ~133 tests) green at every phase boundary; the matrix shows no claim marked "verified" without a passing adversarial test.

---

## Reuse map (verified primitives → GAP)

| Fix | Verified source (path) | Action |
|---|---|---|
| 1 | `Evairi-Master/packages/adk-core/src/action-classification.ts` | Adapt (recompute-and-reject pattern) |
| 4 | `Evolving Agent V1 — …(V2)/identity/signing_engine.py` (Ed25519, Python, tamper-tested) | Import/adapt |
| 3 | `Evairi-Master/packages/contracts/src/authz.ts:44-57` | Adapt comparator; **build** the signed-profile wrapper (absent everywhere) |
| 5 | `AiTrium/packages/decision-lineage-verifier` (HMAC); `Xstatic/.../substrate-stss` | Import/adapt |
| 2 | `AtriumDesktopBridge/server/cognitiveCore.ts`; `Nexeom-Protocol-Library/evairi/.../runtime-host/src/capabilities.ts`, `…/governance/src/iron-rule.ts` | Adapt patterns |
| 6 | `Evairi-Engine/packages/gim/*`, `…/foundry/src/drift/zscore.ts` | Adapt algorithms |
| 7 | n/a | Author new (no ecosystem conformance matrix exists) |

Note: ecosystem primitives are TypeScript/Postgres while GAP is Python, so most are
**adapt the pattern**, not drop-in — except the Ed25519 `signing_engine.py`, which
is already Python. **Genuinely absent everywhere** (must be built, no reuse): the
signed/runtime-immutable Tier-1 profile (Fix 3), real OOB crypto binding (Fix 4),
and a normative-vs-verified conformance matrix (Fix 7).

---

## Effort & sequencing summary

| Phase | Fix | Effort | Depends on | Parallelizable with |
|---|---|---|---|---|
| A | 1 fail-closed | S | — | — (do first) |
| B | 4 crypto substrate | M | — | A |
| C | 3 tier enforcement | M | B | D |
| D | 5 signed lineage + tamper tests | M | B | C |
| E | 2 isolation boundary | L | B | F (start) |
| F | 6 skeletal GIM | L | E | — |
| G | SA-5 + 7 conformance | S–M | A–F (each row gated) | runs throughout |

Critical path: **A → B → E → F**. C and D can run in parallel after B. G is
continuous.

---

## Out of scope (note, don't build here)
- Reconciler Tiers 1–3 (ML/cognitive/adversarial) — audit accepts Tier 0.
- Full Structured Intent Resolution (SIR) — related but not one of the 7 fixes; track separately.
- Production key management / HSM integration beyond a pluggable keystore interface.

## Verification (how we'll know it's earned)
- Per-phase tests listed above; **full suite green at every phase boundary** (regression gate).
- The Phase-G adversarial suite is the capstone: no structural-guarantee claim is marked "verified" in the conformance matrix until its adversarial test passes (tamper fails, negotiation around a hard constraint fails, decomposition detected, forged/replayed authorization rejected, kernel un-importable from the agent process).
- Run: `pip install -e .` then `pytest` from the repo root (current suite ~133 tests).

---

## Status

This is a **planning artifact**. No kernel code has been modified to produce it.
Building the phases is a separate, explicitly-authorized step. The current state
table above reflects the head of `claude/gap-kernel-implementation-25Zt0` as of
2026-06-22.
