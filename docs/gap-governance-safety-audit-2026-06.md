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

> **Update (2026-06-23):** recommendations #2/#3/#4 from this re-audit have since
> been built, reviewed, and merged. The "Deferred by design" items below for
> **rec #4** (corrigibility/kill-switch, GIM-2/4/5, the multi-agent surface) are
> now built and adversarially tested — see the [Closing re-audit](#closing-re-audit--after-recommendations-2--3--4-2026-06-23)
> at the end of this document for the updated, verified scorecard (G-1, SA-1, SA-4
> upgraded to *Substantially addressed*). The text below reflects the state *before*
> that closing tranche.

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
   governance surface — deliberately, not as stubs. *(Addressed in the closing
   re-audit below: corrigibility/kill-switch (SA-4) is built and shipped on the
   autonomous path; GIM-2/4/5 and a multi-agent sub-agent surface are built and
   adversarially tested, honestly scoped as deployment-consulted detectors with
   the independent-**model** evaluator still Normative/Planned.)*

---

## CLOSING RE-AUDIT — after recommendations #2 / #3 / #4 (2026-06-23)

All four recommended next steps above were then executed, each as its own
adversarially-reviewed PR (rec #1 #9/#10, rec #2/#3 #11, rec #4 corrigibility #12,
GIM-2/4/5 #13, multi-agent #14). The nine findings were **re-scored a second time
against current `main`, and every re-score was independently adversarially
verified** (a skeptic agent tried to show each score over- or under-credited;
all nine were judged honest, none adjusted). Suite: **283 passing**.

### Updated scorecard

| Finding | Prior (2026-06) | Now | What moved |
|---|---|---|---|
| G-1 law-overstatement | Partially | **Substantially** ⬆ | rec #3 retired the spec's unqualified "No" → *structural relative to the signed floor*; the coverage residual (3 of 8 regulatory categories have evaluators) keeps it short of Resolved. |
| G-2 in-process kernel | Substantially | Substantially | Real out-of-process boundary remains opt-in; not changed by this tranche. |
| G-3 monitor independence | Partially | Partially | New GIM detectors are *coverage*, not *independence*: the monitor is still in-process, loop-owned, no separate key/channel. |
| G-4 scalable oversight | Partially | Partially | GIM-4 framing-bias **math** now exists + tested, but is unwired/unfed on the shipped path; system-authored approval summaries unchanged. |
| SA-1 CGA gaming pressure | Partially | **Substantially** ⬆ | GIM made consequential (hold + escalate for drift/decomposition) on the governed path; core constraint-completeness limit remains. |
| SA-2 fails open | Substantially | Substantially | Governed posture + classifier make strict typing usable; the omission bypass is still closed only when a deployer selects governed mode. |
| SA-3 unsupervised high-stakes | Unchanged | Unchanged | Every new control adds *more* human gating/containment — corroborates SA-3's thesis rather than solving it (deferred by design). |
| SA-4 threat-model gaps | Partially | **Substantially** ⬆ | Corrigibility/kill-switch built + **shipped** (fabric + loop + reconciler + REST share one switch; CGA cannot retarget around a halt); GIM-3 decomposition now consequential; multi-agent surface (no authority amplification, constraint accumulation, subtree halt, cross-agent decomposition) built + tested. Residuals: the multi-agent registry is *consulted*, not wired into the shipped single-agent path; kill-switch operator authority is a free-text label (not cryptographically authenticated); a self-evolution capability-gain monitor is still unbuilt. |
| SA-5 assurance | Substantially | Substantially | +67 genuinely adversarial tests (retarget-around-halt, mean-preserving distribution evasion, in-place ceiling amplification, monotonic-window eviction). Residuals unchanged: no coverage/mutation/fuzz quantification; regulatory categories still largely untested; some new tests assure *consulted* (not default-enforced) components. |

**Tally:** 0 Resolved · **6 Substantially addressed** · 2 Partially addressed · 1
Unchanged (by design). Three findings upgraded since the prior re-audit (G-1,
SA-1, SA-4).

### The one cross-cutting residual

The dominant remaining theme is **default-path wiring**, not missing mechanisms.
GAP's autonomous heartbeat (`ReconcilerLoop` + `create_app`) still runs *without*
an integrity monitor, and the new GIM-2/4/5 detectors and the multi-agent
`SubAgentRegistry` have no call site outside their own modules and tests — they
are surfaces a deployment must consult and feed. The notable exception is
**corrigibility**, which this tranche wired all the way through the shipped path
(one `app.state.kill_switch` guarding the fabric and the reconciler). Closing the
rest to "Resolved" is a wiring-and-infrastructure program (route the autonomous
path through a governed assembly by default; supply audit-mode / framing /
challenger telemetry; bind cryptographic operator identity to the kill-switch;
add an independent-model GIM evaluator and a self-evolution monitor), each
honestly tracked in `CONFORMANCE.md` rather than asserted.

### Verdict

The build-to-earn arc is **complete for everything implementable in-repo**: every
audit finding now has real, adversarially-tested enforcement behind it, and every
gap between the spec's certainty and the artifact's delivery is either closed or
explicitly marked. What remains is genuinely infrastructure- and
deployment-dependent (independent-*model* evaluators, a distributed sub-agent
fabric, default-governed wiring, hardware isolation, Reconciler Tiers 1–3, SIR-2)
— deferred, disclosed, and not stubbed.

---

## FRONTIER WAVE — closing the in-repo residuals (2026-06-23)

After the closing re-audit, the three highest-leverage *in-repo* residuals it
named were built (each its own adversarially-reviewed PR), further lifting the
findings above:

| Item | PR | Finding lift |
|---|---|---|
| **Default process isolation** | #17 | **G-2** — the out-of-process boundary is now the *default* for the governed path (`build_governed_deployment` / `create_app` run the kernel, with its signing key, in a child process by default; `isolated=False` opt-out). The prior G-2 residual was specifically "the boundary is opt-in / the live app uses the in-process kernel." |
| **Self-evolution capability-gain monitor** | #18 | **SA-4** — the "self-evolution capability-gain has no dynamic monitor" sub-gap is closed: `SelfEvolutionMonitor` (velocity / privilege-accrual / surface) holds a self-modification consequentially when the agent is in a flagged pattern, over *realized* modifications. |
| **Regulatory-category evaluators** | #19 | **G-1 / SA-5** — concrete evaluators now cover **7 of 8** categories (was 3): transparency, anti-discrimination, financial AML, healthcare minimum-necessary, safety — each a structural gate with per-category adversarial tests, narrowing the floor's coverage gap. |

Each review surfaced and fixed real defects before merge — most notably: the
isolation client leaked a temp file on a failed spawn (now deterministic cleanup);
the self-evolution monitor counted *approved-but-unexecuted* self-mods as realized
gain (now counts only on execution); the AML/PHI evaluators *crashed* on malformed
input and parsed thresholds by first-match from free text, mistaking a "45 CFR"
citation for a threshold (now a structured `Constraint.threshold` field, fail-closed
coercion). Suite: **336 passing**.

**Honestly remaining (Normative / Planned, not stubbed):** independent-*model* GIM
evaluators; an out-of-process / separately-keyed GIM with a human-only reporting
channel (the deeper half of G-3); GIM-4 framing telemetry fed on the shipped path
(G-4); a distributed sub-agent fabric and live-executor wiring of the sub-agent
registry; coverage / mutation / fuzz quantification (SA-5); IP/content (Cat 8) as a
kernel evaluator versus provenance tracking; SIR-2 meta-intent inference; Reconciler
Tiers 1–3; hardware/OS-sandbox isolation; and a semantic (model-level) capability
measurement for SA-4. These have genuine infrastructure dependencies and are
disclosed in `CONFORMANCE.md` rather than simulated.
