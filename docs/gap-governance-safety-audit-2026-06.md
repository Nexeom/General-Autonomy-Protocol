# GENERAL AUTONOMY PROTOCOL — POST-REMEDIATION RE-AUDIT
## Independent Re-Assessment After the Remediation Program
## June 2026

---

## AUDIT METADATA

| Field | Value |
|---|---|
| **Re-audit date** | June 2026 |
| **Scope** | Current `main` (remediation PRs #4–#8 + composed-system fixes) — `gap_kernel/`, `tests/` (216 passing), `docs/CONFORMANCE.md` |
| **Baseline** | [`gap-governance-safety-audit-2026-03.md`](gap-governance-safety-audit-2026-03.md) (this document updates it; the baseline remains the historical record) |
| **Method** | Each March finding (G-1…G-4, SA-1…SA-5) was independently re-scored against the current code, and a cross-system review (end-to-end fail-closed coherence, cross-module cryptographic consistency, contract/conformance accuracy) was run. Every reassessment and finding was adversarially verified. |
| **Verdict** | Calibration gap largely closed; load-bearing mechanisms built and adversarially verified; remaining gaps are honestly scoped (deployment-gated or deferred). |

---

## EXECUTIVE SUMMARY

The March baseline found that ~70% of GAP's load-bearing safety mechanisms were
documentation-only or failed **open**, and that the gap between the spec's
certainty and the artifact's assurance was *itself* the most significant finding.

The remediation program closed most of that gap. Across five merged PRs plus this
re-audit's fixes, the mechanisms the baseline rated spec-only or fail-open are now
**built and adversarially verified**: fail-closed evaluation, unforgeable
kernel-signed decisions with proposal-content binding, a signed runtime-immutable
Tier-1 floor, real Ed25519 Out-of-Band approval with a per-approver ceiling and a
persistent replay ledger, tamper-evident signed lineage (incl. truncation
detection), an out-of-process kernel boundary, a rule-based Governance Integrity
Monitor, Structured Intent Resolution, and an adversarial test suite. **Four
adversarial review rounds (including this one) found and fixed 20+ real defects.**

Crucially, the project now carries an honest [Conformance & Maturity
Statement](CONFORMANCE.md) that separates *implemented-and-verified* from
*normative/planned* — so the specific failure the baseline named (confidence
exceeding assurance) is structurally guarded against.

**What remains** falls into two honest buckets, neither hidden:

1. **Available, not default.** Several controls are real and tested but **opt-in**
   rather than enforced by default — process isolation (the live API/reconciler
   still call the in-process kernel), strict action typing (defaults off unless a
   profile is loaded), the SIR readiness gate (not wired into the shipped
   reconciler/REST paths), and GIM signals (computed/surfaced but **advisory** —
   nothing yet *blocks* on them).
2. **Deferred by design.** Trustworthy **unsupervised** high-consequence action
   (SA-3), multi-agent/sub-agent **collusion** and agent-level **corrigibility/
   shutdown** (SA-4), the independent-model GIM detectors (GIM-2/4/5), and SIR-2
   meta-intent inference remain future work with genuine infrastructure
   dependencies — deliberately **not** stubbed, to avoid re-introducing the
   safety-washing the baseline flagged.

---

## RE-SCORED FINDINGS

Status legend: **Resolved** · **Substantially addressed** · **Partially
addressed** · **Unchanged**. Every row was adversarially verified against source.

| # | March finding (condensed) | Now | What changed | Residual (honest) |
|---|---|---|---|---|
| **G-1** | "Structurally incapable of violating law" overstated | Partially addressed | Signed, always-active, fail-closed Tier-1 floor is real (`governance/profile.py`, `kernel.py`, `test_tier_enforcement.py`). | A signature proves authorship, **not legal correctness**; only 3 of 8 regulatory categories have concrete evaluators; and the normative spec still answers "can this violate law?" with an unqualified "No" (the *Conformance* doc is calibrated; the spec is gated by its normative-vs-implemented disclaimer). |
| **G-2** | Structural enforcement gap (in-process, importable kernel) | Substantially addressed | Real out-of-process boundary (`SubprocessGovernanceClient` + `service/kernel_server.py`) + unforgeable kernel-signed decisions verified fail-closed (`test_kernel_service.py`, `test_decision_integrity.py`). | The boundary is **opt-in**: the live FastAPI app and reconciler still instantiate the in-process kernel, where `_signing_key_hex`/`_dynamic_risk_engine`/`_action_type_registry` remain reflection-reachable. Default deployments do not get process isolation. |
| **G-3** | Who governs the governor (monitor independence soft) | Partially addressed | A **model-free, rule-based** GIM (drift + decomposition) sidesteps the same-model correlated-blind-spot scenario (`integrity_monitor.py`, `test_gim.py`). | Independence is unenforced: the monitor is an in-process object owned by the loop it watches, with no separate key/process and no human-only reporting channel; the different-model re-classifier (GIM-2) is deferred. |
| **G-4** | Human-in-the-loop scalable-oversight weaknesses | Partially addressed | Real Ed25519 L2+ OOB gate (signature, freshness, registered key, per-approver ceiling, persistent replay) + **proposal-content binding** (this re-audit) closes the content-substitution channel. | Framing bias and **system-generated approval summaries** are unaddressed — there is no agent-independent rendering channel; rubber-stamping is mitigated only by cryptographic friction, not behavioral control. |
| **SA-1** | CGA double-edge (specification-gaming pressure) | Partially addressed | An adversarial test proves CGA **cannot negotiate around a hard constraint**; fail-closed removes the silent-yes amplifier for declared constraints (`test_adversarial.py`). | The structural tension remains: GIM gaming-detection is **advisory** — signals are surfaced on `CGAResult` but nothing consumes them to block; safety still rests on constraint completeness. |
| **SA-2** | Fails open | Substantially addressed | Constraint evaluation fails closed; unregistered action types always reject; `action_type_id` is now threadable through the loop (this re-audit). | The *omission* bypass is closed only under strict action typing, which defaults off (auto-on only when an Applicability Profile is loaded); the shipped wiring does not enable it by default. |
| **SA-3** | Full autonomy safest where least autonomous | Unchanged | Remediations make high-stakes a cryptographically real **human gate** (L2+ cannot auto-execute), which *tightens* supervision. | The hard problem — trustworthy **unsupervised** high-consequence action — is still deferred, not solved. By design. |
| **SA-4** | Threat-model gaps | Partially addressed | GIM-3 decomposition detection is live; SIR governs the intent-transfer moment. | Decomposition detection is **report-only** (never blocks/escalates); multi-agent/sub-agent **collusion** and agent-level **corrigibility/shutdown** are unbuilt (no sub-agent surface exists); self-evolution capability-gain has no dynamic monitor. |
| **SA-5** | Assurance (95% happy-path, no adversarial tests) | Substantially addressed | Every named sub-gap (CGA-cannot-negotiate, lineage tamper, GIM/SIR/tier) now has real passing adversarial tests against genuine enforcement (216-test suite). | The regulatory *categories* still lack dedicated tests; the "~95% happy-path" figure was never re-quantified (no coverage/mutation metric); no fuzzing. |

---

## CROSS-SYSTEM REVIEW

Reviewing the *integrated* system (not per-module) surfaced **7 confirmed** issues
the narrow per-PR reviews could not. Fixed in this re-audit:

- **Content-substitution (G-4, medium):** a decision bound only `proposal.id`; a
  same-id proposal with mutated actions could execute under it. The kernel now
  binds a proposal **content digest** into the signed decision; the fabric
  re-checks it. → `tests/test_decision_integrity.py`.
- **Silent drop of L2+ approvals (medium):** the production `ReconcilerLoop`
  treated `awaiting_approval` as neither success nor escalation, dropping the
  high-stakes action and cooling the entity down as if handled. It now routes
  `awaiting_approval` to the human escalation queue.
- **`action_type_id` not threaded (medium):** the registry/tier/multi-phase gates
  never ran in the composed loop. `CGALoop.run` now threads `action_type_id`.
- **Domain separation (low):** all five signed payloads (decision, OOB, lineage,
  profile, SIR seal) now carry a `_domain` tag — cross-protocol confusion
  resistance, no longer reliant on incidental structural divergence.
- **Contract hygiene (low):** `GovernanceClient` now threads `current_time`
  (drop-in parity with the kernel); `SubprocessGovernanceClient` gained a
  defensive `__del__`.

Documented as residual (not a code bug): the SIR readiness gate is **opt-in** and
not wired into the shipped reconciler/REST pipeline — recorded in `CONFORMANCE.md`.

Refuted (not real): "GIM signals never computed in production" (they are, when the
monitor is wired) and "profile `signing_key_id` not bound" (the signature binds
content; the id only selects the verification key).

---

## VERDICT

### As a design / standard
**Strong, and now substantially *earned*.** The conceptual architecture the
baseline praised is intact, and the mechanisms that make it real are built and
adversarially verified rather than asserted.

### As a safety guarantee today
**Materially stronger, and honestly calibrated.** The structural claims that are
built (fail-closed evaluation, unforgeable+content-bound decisions, signed
immutable floor, real OOB with approval gating, tamper-evident lineage,
out-of-process boundary) are verified. The claims that are deployment-gated
(default process isolation, strict typing, SIR gate, GIM blocking) or deferred
(unsupervised high-consequence action, multi-agent collusion, corrigibility) are
**explicitly marked** in `CONFORMANCE.md` — a deployer can no longer mistake
aspiration for assurance, which was the baseline's core concern.

### Recommended next steps (in leverage order)
1. **Make the built controls the default**, not opt-in. *(Addressed:
   `governance/deployment.py` `build_governed_deployment()` is a fail-closed
   governed posture that requires the industry regulatory floor and forces on
   signature verification, strict action typing, and the SIR gate — distinguishing
   industry-specific policy, which must be configured, from universal primitives,
   which are now enforced. The shipped REST app (`create_app(applicability_profile=…)`)
   and reconciler run governed when a floor is supplied, and log a loud warning when
   running OPEN. The autonomous reconciler is not SIR-gated per drift — SIR
   governs the human intent-transfer at intent registration, not autonomous
   drift-correction — and it auto-derives its governance action type via an
   `ActionTypeClassifier` (`drift_reconciliation` base, escalating to a
   more-restrictive category for sensitive operations), so it no longer relies on
   a configured default.)*
2. **Make GIM consequential**: have the loop/reconciler *act* on integrity signals
   (block/escalate) rather than only surface them. *(Addressed: in governed mode
   the loop now HOLDS an action GIM flags (drift / threshold-avoidance) and routes
   it to a human — `CGALoop(block_on_integrity=…)`, on by default when governed;
   the reconciler queues it as `integrity_hold`. `tests/test_integration.py`.)*
3. **Re-level the residual spec language** (e.g. the unqualified "No" in the
   specification) to match the conformance statement. *(Addressed:
   `PROTOCOL_SPECIFICATION.md` now states the regulatory-floor guarantee as
   *structural relative to the signed floor* — not a guarantee of legal
   correctness — and points to the conformance statement.)*
4. **Build the deferred mechanisms with their infrastructure**: independent-model
   GIM (GIM-2/4/5), agent-level corrigibility/shutdown, and a multi-agent
   governance surface — deliberately, not as stubs.
