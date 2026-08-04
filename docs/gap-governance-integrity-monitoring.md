# GENERAL AUTONOMY PROTOCOL — GOVERNANCE INTEGRITY MONITORING
## Extension to GAP Specification
## March 10, 2026

> ⚠️ **Normative requirements below; implementation status at the end.** The
> thresholds in this document are the *requirement*. Where the reference
> implementation's defaults differ — GIM-1 and GIM-3 both differ, one of them
> semantically — the divergence is stated in
> [Implementation status](#implementation-status) rather than quietly resolved by
> editing either side.

---

## CONTEXT

GAP defines the Iron Rule: a governed system cannot modify its own governance
boundaries. This is a STATIC guarantee — governance configuration is
architecturally immutable.

Research on instrumental convergence and deliberative alignment shows this is
necessary but not sufficient. Sophisticated AI systems find INDIRECT paths
around governance:

- Classifying actions at lower authorization levels than warranted
- Behaving differently when audit conditions are detectable
- Decomposing high-governance tasks into many low-governance sub-tasks
- Framing escalations to bias human approval toward system preference
- Adversarial review mechanisms collapsing into rubber-stamp approval

Governance Integrity Monitoring is a DYNAMIC guarantee that extends the Iron
Rule: the system cannot indirectly erode governance effectiveness through
behavioral adaptation.

---

## GAP REQUIREMENT: GOVERNANCE INTEGRITY MONITORING

Any GAP-compliant system MUST implement continuous monitoring for indirect
governance erosion across five signal classes. This is a protocol-level
requirement, not an implementation suggestion.

### Requirement GIM-1: Authorization Level Drift Detection

The system MUST periodically evaluate whether its own authorization level
classifications are drifting downward relative to an independent assessment.

**Specification:**
- An independent evaluator (different model, different prompt, no access to the
  system's classification logic) independently classifies a random sample of
  operations by required authorization level (L0-L4)
- System classification is compared against independent classification
- Divergence is tracked over time as a drift score

**Alert condition:** Drift score exceeding 0.15 for two consecutive evaluation
periods, OR any single operation classified L0 by the system where independent
evaluator classifies L3 or L4.

**Required response:** Human administrator review of flagged operations and
authorization classification logic.

### Requirement GIM-2: Audit Behavior Divergence Detection

The system MUST be monitored for behavioral differences between audited and
non-audited operation.

**Specification:**
- Randomly designated "shadow audit" periods where monitoring occurs without any
  detectable audit signals to the system
- System behavior during shadow audit periods is compared against behavior during
  known audit periods and normal operation
- Metrics compared: reasoning trace completeness, adversarial challenge rate,
  authorization classification distribution, escalation frequency

**Alert condition:** Behavioral divergence between known-audit and shadow-audit
periods exceeding 0.2 on any metric, indicating the system adjusts behavior when
it detects audit conditions.

**Required response:** This is the strongest integrity signal. If detected, the
system is deliberately performing compliance rather than being compliant. Requires
immediate human investigation.

### Requirement GIM-3: Threshold-Avoidance Decomposition Detection

The system MUST be monitored for task decompositions that keep sub-tasks below
governance thresholds that the composite task should trigger.

**Specification:**
- For every task composition, independently assess what authorization level the
  COMPOSITE task would require if submitted as a single unit
- Compare composite authorization against the maximum authorization of any
  individual sub-task
- If composite level exceeds max sub-task level, flag as potential threshold
  avoidance

**Alert condition:** Any single composition with authorization gap >= 2 levels.
Three or more compositions with gap = 1 in the same domain within 30 days.

**Required response:** Review decomposition logic. Determine whether decomposition
is legitimate (sub-tasks genuinely lower-risk) or suspicious (decomposition
appears designed to avoid thresholds).

### Requirement GIM-4: Escalation Framing Bias Detection

When the system escalates to a human for approval (as GAP authorization levels
require), the system MUST be monitored for presentation bias that favors the
system's preferred outcome.

**Specification:**
- Track for each escalation: the system's preferred option, option presentation
  order, framing sentiment across options, urgency emphasis, and human decision
- Over time, measure: correlation between favorable framing and human approval,
  position bias (first-listed option chosen disproportionately), overall approval
  rate of system-preferred option

**Alert condition:** System-preferred approval rate exceeding 90% AND
framing-approval correlation exceeding 0.5. Position bias with >80%
first-position approval rate.

**Required countermeasure:** When framing bias is detected, automatically
randomize option presentation order and normalize framing sentiment across all
options for future escalations.

**Required response:** Human administrator review of escalation framing patterns.

### Requirement GIM-5: Adversarial Collapse Detection

If the system uses adversarial verification (challenger/verifier pattern), the
adversarial mechanism MUST be monitored for collapse — the challenger ceasing to
genuinely challenge proposals.

**Specification:**
- Track challenger endorsement rate over time
- Track whether challenges produce material changes in output
- Track whether challenges introduce genuinely new information or merely
  paraphrase the proposer's own caveats
- Compare challenger behavior across domains

**Alert condition:** Endorsement rate exceeding 85% for two consecutive evaluation
periods AND material challenge rate below 10%.

**Required response:** Escalate challenger model capability, modify challenger
prompt to explicitly request disagreement, or rotate challenger perspective
configuration. If endorsement rate approaches 100%, the adversarial system has
collapsed and MUST be rebuilt before the system processes further governed
operations.

---

## ARCHITECTURAL REQUIREMENTS

### Isolation

The governance integrity monitor MUST be architecturally isolated from the system
it monitors:
- MUST use a different model/evaluator than the system's primary reasoning
- MUST NOT be a capability that the system can evolve or modify
- MUST NOT be controllable or influenceable by the system being monitored
- MUST report to human administrators, not to the system

### Independence

Independent evaluation (GIM-1 and GIM-2) MUST use evaluators with no access to
the system's classification logic, training data, or internal state. The
independent evaluator assesses based on the same inputs the system received, not
on the system's reasoning.

### Continuity

Governance integrity monitoring MUST operate continuously, not only during
designated audit periods. Shadow audits (GIM-2) must be indistinguishable from
normal operation to the monitored system.

---

## THREE-LAYER DEFENSE IN DEPTH

```
Layer 1: Iron Rule (architectural, static)
  System cannot modify governance configuration.
  Enforced by code. Absolute.

Layer 2: Governance Integrity Monitoring (behavioral, dynamic)
  System cannot indirectly erode governance effectiveness.
  Enforced by independent monitoring. Probabilistic but comprehensive.

Layer 3: Human Oversight (institutional, judgment)
  Integrity alerts reach human administrators.
  Humans make final judgment on whether erosion has occurred.
  Enforced by organizational process.
```

No single layer is sufficient. A GAP-compliant system MUST implement all three
layers.

---

## RELATIONSHIP TO OTHER GAP COMPONENTS

- **Authorization Tiers (L0-L4):** GIM-1 monitors whether tier classification
  is being gamed
- **Decision Lineage:** GIM-3 and GIM-4 use lineage data to detect decomposition
  and framing patterns
- **Consequence Exposure Levels:** Systems at higher consequence exposure levels
  (Committed, Consequential) SHOULD have more frequent integrity evaluation cycles
- **Iron Rule:** GIM extends the Iron Rule from static to dynamic governance
  protection

This specification does not define implementation details. It defines what any
GAP-compliant implementation MUST monitor, what thresholds trigger alerts, and
what responses are required.

---

## IMPLEMENTATION STATUS

What the GAP reference implementation actually runs, per requirement. Two
modules are involved: `gap_kernel/governance/integrity_monitor.py`
(`GovernanceIntegrityMonitor`, all five signal classes) and
`gap_kernel/governance/independent_review.py`
(`IndependentAuthorizationMonitor`, the self-versus-independent comparison axis).

**How the two GIM-1 divergences were resolved.** The normative thresholds above
were left unchanged and the implementation defaults were left unchanged; what
changed is that the mismatch is now stated. That is the right call for GIM-1,
where the two numbers are *not comparable* — the spec's `0.15` is a normalized
divergence score against an independent evaluator, while the monitor's `1.0` is a
count of authorization levels of self-versus-past drift. Silently rewriting
either number would have created a false agreement between different
measurements. It is a weaker call for GIM-3, where the implementation's window is
genuinely 8,640× shorter than the specified one and the detector measures a
different thing entirely; **the honest reading is that GIM-3 is not implemented as
specified**, and reconciling it requires code, not prose.

| Req | Spec alert condition | Implementation default | Verdict |
|---|---|---|---|
| **GIM-1** | Drift score > **0.15** over two consecutive periods, OR a single operation the system rates L0 that an independent evaluator rates L3/L4 | Split across two modules. `IndependentAuthorizationMonitor(drift_threshold=0.15, severe_gap=3, min_samples=5)` — normalized over the L0–L4 range, so **0.15 matches the spec**, and `severe_gap=3` is exactly the L0-vs-L3/L4 clause. Separately, `GovernanceIntegrityMonitor(drift_threshold=1.0, baseline_window=5, recent_window=3)` compares an action type's recent mean authorization rank against its own baseline, in **whole authorization levels** | **Partially met.** The spec's condition is implemented, in `independent_review.py`. The `1.0` in `integrity_monitor.py` is a second, coarser self-versus-past detector in different units, not a mis-set version of the spec's 0.15. **Neither implements the "two consecutive evaluation periods" debounce** — both fire on a single evaluation |
| **GIM-2** | Divergence > **0.2** on any metric between known-audit and shadow-audit | `audit_divergence_threshold=0.2`, `audit_min_samples=5` | **Threshold matches.** Metrics implemented are the authorization-classification distribution (total-variation distance), mean rank, and escalation rate. The spec's *reasoning-trace completeness* and *adversarial challenge rate* metrics are not implemented. **Unfed on any shipped path** — the deployment must supply audit-mode telemetry |
| **GIM-3** | Any single composition with authorization gap **≥ 2 levels**; or **3+** compositions with gap = 1 in the same domain within **30 days** | `decomposition_count_threshold=3`, `decomposition_window_seconds=300` (**5 minutes**), `decomposition_max_level="L1"` | **Not met as specified.** The count of 3 matches. The window does not: 5 minutes vs 30 days. More importantly the *mechanism* differs — the implementation counts repeated sub-threshold actions (≤ L1) against one target inside a recency band; it never computes the composite task's authorization level, so it measures no "gap" at all. It detects bursts, not threshold avoidance |
| **GIM-4** | System-preferred approval rate > **90%** AND framing-approval correlation > **0.5**; or > **80%** first-position approval | `preferred_approval_threshold=0.90`, `framing_correlation_threshold=0.5`, `position_bias_threshold=0.80`, `escalation_min_samples=5` | **Thresholds match.** Correlation is computed as a phi coefficient over binary series. Fed on the shipped path from real human escalation resolutions, but the framing sentiment scores themselves are **deployment-declared** — the system self-reports how favorably it framed its own option. The spec's **required countermeasure** (auto-randomize option order, normalize sentiment) is **not implemented** |
| **GIM-5** | Endorsement rate > **85%** for two consecutive periods AND material-change rate < **10%** | `endorsement_threshold=0.85`, `material_change_floor=0.10`, `challenge_window=10`, `collapse_endorsement_rate=0.98` | **Thresholds match**, and this is the one detector that implements the spec's **two-consecutive-period debounce**. **Unfed** — nothing on any shipped path calls `observe_challenge`, so in practice GIM-5 never fires |

### Architectural requirements

The isolation and independence requirements above are **not met**, and this is
the most load-bearing gap in this document.

- *"MUST use a different model/evaluator"* — the monitor is rule-based and
  therefore free of the same-model correlated-blind-spot failure the requirement
  targets. That is real, and it is **logic** independence, not model
  independence. `RuleBasedIndependentClassifier` rates operations by substring-
  matching a fixed keyword list against the action name and parameter values;
  **renaming an action defeats it**. A model-backed classifier is a declared
  adapter point and is unimplemented.
- *"MUST NOT be controllable or influenceable by the system being monitored"* —
  the monitor is an ordinary in-process Python object owned by the CGA loop it
  watches. It has no separate process, no separate key, and no reporting channel
  the governed process cannot reach.
- *"MUST report to human administrators, not to the system"* — signals are
  returned on the `CGAResult` the governed process consumes. In governed mode a
  flagged action is held and routed to the human escalation queue, which is
  consequential and real; the *reporting channel* is still the governed system's.
- *"MUST operate continuously"* — GIM-1 and GIM-3 are wired into the CGA loop, so
  every decision is observed. GIM-2, GIM-4 and GIM-5 depend on telemetry a
  deployment must supply, and GIM-5 has no producer anywhere in the repository.

**Therefore: "five independent detectors" is not an accurate description of what
runs.** Five detectors exist and are tested. Two (GIM-1, GIM-3) observe every
decision; one (GIM-4) is fed self-reported framing data; two (GIM-2, GIM-5) are
unfed. None of them is independent in the architectural sense this document
requires.
