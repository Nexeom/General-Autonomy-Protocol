# GENERAL AUTONOMY PROTOCOL — GOVERNANCE INTEGRITY MONITORING
## Extension to GAP Specification
## March 10, 2026

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
