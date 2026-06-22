# GENERAL AUTONOMY PROTOCOL — GOVERNANCE AND SAFETY AUDIT
## Independent Assessment of GAP Specification and Kernel Implementation
## March 2026

---

## AUDIT METADATA

| Field | Value |
|---|---|
| **Audit Date** | March 2026 |
| **Scope** | GAP Specification (README.md) + Kernel Implementation (`gap_kernel/`) + Extension Specs (`docs/`) + Test Suite (`tests/`) |
| **Version Audited** | Commit `2b0b5d4` on branch `claude/gap-kernel-implementation-25Zt0` |
| **Auditor Context** | Independent assessment requested by protocol author. Evidence-based evaluation of governance design and safe-autonomy properties. |
| **Assessment Type** | Governance posture + safe-autonomy readiness |
| **Status** | Baseline audit. Cross-reference with related Nexeom repositories pending. |

---

## EXECUTIVE SUMMARY

GAP has a **genuinely strong conceptual model** — among the better-thought-out
governance designs for agentic systems — wrapped in **structural-guarantee
language that the artifact does not currently earn**. As a *specification of what
accountable autonomy should look like*, it is sophisticated and largely sound.
As a *system that delivers the safety guarantees it claims*, roughly 70% of the
load-bearing mechanisms are documentation only, and several implemented ones fail
**open**.

For a framework whose entire value proposition is *trustworthiness*, the gap
between the certainty of the prose ("cannot," "structurally incapable,"
"immutable," "a path that does not exist") and what is actually enforced is
itself the most significant safety finding. **The spec/implementation gap is
itself a governance failure** — a deployer reading the README would reasonably
conclude the system *cannot* be configured to break the law, *cannot* have its
governance bypassed, and *is* continuously monitored for erosion, none of which
the current artifact does. In safety terms this is *safety-washing risk*: the
documentation's confidence exceeds the system's assurance.

The implemented parts are, to be fair, clean and well-tested — the problem is
scope and calibration, not craftsmanship.

---

## SCOPE AND METHOD

### Examined

- **README.md** — Canonical GAP specification (~69KB), all 11 sections including
  architecture, key mechanisms, authorization levels, policy tiers, regulatory
  constraint categories, and design principles.
- **`docs/`** — Extension specifications: Governance Integrity Monitoring (GIM),
  Structured Intent Resolution (SIR).
- **`gap_kernel/`** — Full kernel implementation: governance kernel, dynamic risk
  engine, execution fabric, lineage store, reconciler, learning engine, models,
  API layer.
- **`action-types/`** — Action type specifications (financial_transaction).
- **`tests/`** — Complete test suite (133+ test cases across 12 files).

### Not Examined

- Production deployment configuration or infrastructure.
- Performance characteristics or scalability.
- External system integrations (CRM APIs, email systems, etc.).
- The 8 related Nexeom repositories (pending separate cross-reference; see
  CROSS-REFERENCE section below).

---

## STRENGTHS

### S-1: Correct Primitive Separation (Three-Layer Architecture)

Three structurally distinct layers (Governance Kernel / Strategy Layer /
Execution Fabric), the Iron Rule, graduated authority (L0-L4), and "governance
as substrate, not checkpoint" are sound separation-of-powers thinking. The
litmus test — *rejection triggers compliant re-planning, not halt and not
ignore* — is a crisp, falsifiable definition of the category.

### S-2: Decision Records as Primary Accountability Object

Most systems log *what they did*; GAP insists on capturing *what was decided,
under whose authority, against which policies, and — unusually — what was
uncertain* (Structured Uncertainty). The "what didn't you consider?" framing is
more sophisticated than typical audit logging. Decision Records as the primary
data object is the correct unit of value for institutional accountability.

### S-3: Defense-in-Depth Design Instincts

Separation of Creation and Validation (separation of duties), Out-of-Band
Authority Verification (anti-spoofing / confused-deputy defense), Multi-Phase
Authorization (intent gate + outcome gate), and Proportional Governance /
Applicability Profiles (risk-based scoping aligned with EU AI Act and NIST AI
RMF) are all legitimate, well-chosen controls.

### S-4: GIM Threat Model (Most Safety-Aware Component)

Governance Integrity Monitoring explicitly recognizes that *static* guarantees
are insufficient because capable systems find *indirect* paths around
governance — authorization drift, audit-behavior divergence,
threshold-avoidance via decomposition, escalation-framing bias, and
adversarial-review collapse. That threat model mirrors frontier alignment
concerns (deceptive alignment, specification gaming, scalable-oversight failure)
far more closely than most commercial "AI governance" products.

### S-5: Safe-Autonomy Targeting

"Intelligence without autonomy is a research project; autonomy without
governance is a liability" is the right framing. Proactive reconciliation
(detect drift and act, don't wait) distinguishes governance infrastructure from
a reactive policy gate. Authority is graduated and auditable — the right shape
for incrementally earning autonomy.

---

## GOVERNANCE WEAKNESSES

### G-1: "Structurally Incapable" Claim Overstated

**Finding:** "GAP-compliant systems are structurally incapable of being
configured to violate applicable law" is close to unfalsifiable. "Applicable
law" is ambiguous, contested, jurisdiction-specific, and evolving — it is not a
machine-encodable fixed set.

**Reality:** The real guarantee reduces to *"the system cannot operate below the
Tier 1 constraints a human correctly encoded and the evaluator correctly
checks."* That is a meaningful property, but it is a much weaker and more
conditional claim than the one advertised, and it silently relocates all the
trust onto the (fallible, human-authored) Applicability Profile and the
(currently illustrative) constraint evaluator.

### G-2: Structural Enforcement Gap (In-Process Python, Not Real Boundary)

**Finding:** The spec repeatedly asserts the agent "cannot access" governance —
"a path that does not exist" — but never specifies the isolation *mechanism*
(process boundary? capability security? service separation? hardware?). It is
stated as a requirement, not a design.

**Evidence:** In the reference implementation, the kernel is a single Python
process. The GovernanceKernel is an ordinary class an agent could `import` and
call. The "private" risk engine is reachable via
`kernel._dynamic_risk_engine` (name-mangling is not a security boundary).

**Risk:** A claim of *structural impossibility* that is actually a *software
convention* is the most dangerous kind of safety claim — it is exactly the
"security as a structural failure" trap the GIM section itself warns about,
turned inward on the protocol.

### G-3: Who Governs the Governor? (GIM Independence, Correlated Blind Spots)

**Finding:** Everything safety-critical — the kernel, GIM, validation, OOB — is
assumed independent and correct. GIM is meant to be the watchdog, yet the spec
concedes that "minimum viable independence" can be *two instances of the same
model*.

**Risk:** For LLM-based evaluators this means **correlated blind spots**: the
monitor and the monitored can share the same failure modes. The independence
keystone is asserted structurally but is soft in exactly the cases that matter.

### G-4: Human-in-the-Loop Scalable Oversight Weaknesses

**Finding:** L2-L4 lean on human approval, but:

1. GIM-4 itself concedes the agent can bias humans via framing.
2. At scale humans rubber-stamp (automation bias, alert fatigue).
3. The approval summaries and dashboards are *generated by the system being
   governed*, giving the agent a channel to shape the very judgment that is
   supposed to check it.

**Risk:** The protocol names these risks but its core still depends on the
oversight it admits is corruptible.

---

## SAFE-AUTONOMY WEAKNESSES

### SA-1: CGA Double-Edge (Specification Gaming Pressure)

**Finding:** "Treat 'no' as a constraint and find a path to 'yes'" is elegant
for productivity but is *precisely the optimization pressure that produces
specification gaming*. The system is explicitly engineered to search right up to
the boundary of what's permitted.

**Risk:** Safety depends **entirely on the constraint set being complete and
correctly evaluated**. CGA therefore *raises* the burden on constraint
completeness, while the same system cannot guarantee it. This is a structural
tension, not a detail.

### SA-2: Fails Open (Wrong Default for Autonomy)

**Finding:** The implementation defaults to permissive behavior where it should
default to restrictive.

**Evidence:**

- **`_generic_constraint_check` returns False** (no violation detected) at
  `gap_kernel/governance/kernel.py:197-203`. Any constraint without a specific
  named handler is silently treated as satisfied. This is the wrong default for
  a safety system.
- **`action_type_id` is Optional** at `gap_kernel/governance/kernel.py:582`.
  The Action Type Registry gate can be skipped entirely by omitting the field.
  When `action_type_id` is `None`, the registry check is bypassed.

**Risk:** Combined with CGA's boundary-seeking behavior, fail-open is close to
a worst case: an agent motivated to find gaps, operating over a checker that
treats gaps as permission.

### SA-3: Full Autonomy Safest Where Least Autonomous

**Finding:** High-stakes actions are routed to humans (L3/L4); the genuinely
no-human tier (L0/L1) is sanctioned only for "pre-approved routine operations."
Nothing here makes high-stakes *autonomous* action safe — it makes it
*human-supervised*.

**Implication:** This is a defensible engineering choice, but it is in real
tension with the "General Autonomy" branding. The hard problem (trustworthy
unsupervised high-consequence action) is deferred, not solved.

### SA-4: Threat Model Gaps

The protocol is strong on indirect governance erosion (GIM) but thin on:

- **Objective/proxy misspecification.** SIR captures *intent* but does not
  address the agent optimizing a proxy of it.
- **Multi-agent / sub-agent dynamics.** The parent-to-sub-agent hierarchy is a
  major surface. Decomposition to evade thresholds is named in GIM-3 but
  unimplemented. Collusion is barely touched.
- **Capability gain via self-evolution.** Tier 3 skill tuning can change
  real-world risk without ever touching policy.
- **Corrigibility.** A "find a path to yes" disposition is mildly
  *anti*-corrigible. There is no shutdown/containment semantics beyond a UI
  "Hard Stop."

### SA-5: Assurance Coverage (95% Happy-Path, No Adversarial Tests)

**Finding:** The test suite is ~95% happy-path: no test attempts to subvert the
kernel, no test confirms CGA *cannot* negotiate around a hard constraint, no
tamper test on the lineage chain (the "tamper-evident" claim is never
adversarially checked), and GIM/SIR/tiers/categories have zero tests.

**Risk:** "Adversarial Integrity Verification" is filed as a *non-normative
recommendation* — yet for a trust-first protocol it should be the **core** of
the assurance argument, not optional.

---

## IMPLEMENTATION STATUS MATRIX

| Claimed Capability | Spec Status | Implementation Status | Evidence |
|---|---|---|---|
| **Three-Layer Architecture** | Complete | **Implemented** | `gap_kernel/governance/`, `gap_kernel/execution/`, strategy layer |
| **Iron Rule** (governance immutable from below) | Complete | **Convention only** | Single Python process; no process/service boundary |
| **Constraint-Guided Autonomy** | Complete | **Implemented** | CGA loop with re-planning on rejection |
| **Decision Records** | Complete | **Implemented** | `GovernanceDecision` model with full fields |
| **Decision Lineage** (tamper-evident) | Complete | **Partial** | SHA-256 chaining exists but in-memory by default; hash, not crypto signature; no external anchor |
| **Structured Uncertainty** | Complete | **Implemented** | `UncertaintyDeclaration` model, populated in decisions |
| **Authorization Levels (L0-L4)** | Complete | **Implemented** | `AuthorizationLevel` enum, evaluated in kernel |
| **Action Type Registry** | Complete | **Implemented** | 6 baseline types registered; but `action_type_id` is Optional |
| **Multi-Phase Authorization** | Complete | **Implemented** | Intent gate + outcome gate in kernel |
| **Dynamic Risk Escalation** | Complete | **Implemented** | `DynamicRiskEngine` with 4 trigger types; baseline manual-only |
| **Out-of-Band Authority Verification** | Complete | **Stub** | Checks string presence, not actual verification; in-memory replay protection |
| **Policy Tier Classification** | Complete | **Spec-only** | No tier field on constraints; no Tier 1 enforcement |
| **Applicability Profiles** | Complete | **Spec-only** | No profile model or loader |
| **Regulatory Constraint Categories** | Complete | **Spec-only** | 3 illustrative checks; 5 categories silently pass |
| **Data Classification Tags** | Complete | **Spec-only** | No classification model |
| **Governance Integrity Monitoring** | Complete | **Not started** | 0 lines of implementation; spec in `docs/` only |
| **Structured Intent Resolution** | Complete | **Not started** | 0 lines of implementation; spec in `docs/` only |
| **Separation of Creation and Validation** | Complete | **Partial** | Design principle present; no enforced validator separation |
| **Proportional Governance** | Complete | **Spec-only** | No proportional scaling in kernel |
| **Reconciler** (drift detection) | Complete | **Partial** | Tier 0 only (rule-based); Tiers 1-3 not implemented |
| **SpendGate** (financial governance) | Complete | **Partial** | Cost ceiling check exists; full SpendGate not implemented |
| **Adversarial Integrity Verification** | Non-normative | **Not started** | No adversarial tests in suite |

**Summary:** Of 22 claimed capabilities, 7 are fully implemented, 5 are
partial, 2 are stubs, 6 are spec-only, and 2 are not started.

---

## HIGHEST-LEVERAGE REMEDIATION

Ordered by impact on closing the gap between claims and capabilities.

### Fix 1: Fail Closed (Addresses SA-2)

**Change:** `_generic_constraint_check` should return `True` (violation
detected) for unknown constraints, not `False`. Unregistered or absent
`action_type_id` should be rejected, not silently allowed.

**Effort:** Small. Most important safety change.

**File:** `gap_kernel/governance/kernel.py:197-203`

### Fix 2: Real Structural Enforcement or Soften Prose (Addresses G-2)

**Change:** Either specify and build a real isolation boundary (kernel as
separate service reached only through a constrained API; capability-based
access; no in-process import path) or change "cannot" / "a path that does not
exist" to "is designed so that."

**Effort:** Large (if building real boundary) or small (if adjusting language).

**Note:** Do not ship structural-impossibility language over a software
convention.

### Fix 3: Make Tier 1 Real (Addresses G-1)

**Change:** Add a `tier` field to `Constraint`. Enforce Tier 3 <= Tier 2 <=
Tier 1 at evaluation time. Load Tier 1 from a signed profile the runtime cannot
mutate.

**Effort:** Medium.

### Fix 4: Real OOB Verification (Addresses G-2, SA-2)

**Change:** Bind authorization to a cryptographic signature over the specific
Decision Record ID via a channel independent of the agent. Verify the
signature, not the presence of a string field.

**Effort:** Medium.

**File:** `gap_kernel/execution/fabric.py:122-157`

### Fix 5: External Append-Only Signed Lineage

**Change:** WORM / hash-chained file with an external anchor (not SQLite
`:memory:`). Add tamper-detection tests that mutate a record and assert
verification fails.

**Effort:** Medium.

**File:** `gap_kernel/lineage/store.py`

### Fix 6: Skeletal GIM Implementation (Addresses G-3)

**Change:** Implement at least basic drift detection (GIM-1) and decomposition
detection (GIM-3). GIM is the safety-load-bearing layer — it cannot remain
spec-only.

**Effort:** Large.

**File:** New module `gap_kernel/governance/integrity_monitor.py`

### Fix 7: Conformance/Maturity Statement

**Change:** Add a conformance statement to the README distinguishing *normative
requirement* from *implemented & verified capability*, so deployers cannot
mistake aspiration for assurance.

**Effort:** Small.

---

## CROSS-REFERENCE: RELATED REPOSITORIES

The following Nexeom repositories may address some of the gaps identified in
this audit. These repositories exist on the protocol author's local development
environment and have not yet been evaluated.

| Repository | Expected Relevance | What to Look For | Status |
|---|---|---|---|
| **Nexeom-Protocol-Library** | G-2 (Structural Enforcement), Fix 2 | Separate service/process boundaries, IPC protocols, capability-based access | Pending |
| **Xstatic** | SA-4 (Threat Model), SA-5 (Assurance) | Static analysis, adversarial testing harness, security patterns | Pending |
| **Evolving Agent V1** | SA-1 (CGA Gaming), SA-3 (Autonomy Levels), SA-4 (Multi-Agent) | Agent wiring patterns, sub-agent governance, constraint propagation | Pending |
| **Evairi-Engine** | G-3 (GIM Independence), Fix 6 | Independent monitoring, evaluation engine, drift detection | Pending |
| **Evairi - Master system build** | G-3, G-4 (Oversight), Fix 6, Fix 7 | Master system architecture, evaluator independence, conformance | Pending |
| **Endevai** | SA-4 (Capability Gain), Fix 3 (Tier Enforcement) | Policy enforcement, tier management, capability controls | Pending |
| **AtriumDesktopBridge** | G-2 (Isolation), Fix 4 (OOB) | Desktop-to-agent bridge security, OOB channel implementation, process isolation | Pending |
| **AiTrium** | G-4 (Human Oversight), Fix 7 (Conformance) | Human-in-the-loop UX, dashboard generation, oversight tooling | Pending |

### Cross-Reference Methodology

For each repository, evaluate against the following search patterns:

- **Process isolation / service boundary:** `grep -rE "(subprocess|grpc|IPC|service.boundary|container|sandbox|isolation)"` 
- **Cryptographic verification:** `grep -rE "(signature|verify|sign|asymmetric|key.pair|certificate)"`
- **Drift / integrity monitoring:** `grep -rE "(drift|integrity|monitor|GIM|decomposition|threshold.avoidance)"`
- **Adversarial testing:** `grep -rE "(adversarial|red.team|attack|exploit|subvert|tamper)"`
- **Tier enforcement:** `grep -rE "(tier|Tier.1|regulatory.floor|immutable.policy|signed.profile)"`
- **Corrigibility / shutdown:** `grep -rE "(shutdown|corrigib|containment|kill.switch|emergency.stop)"`

---

## VERDICT

### As a Design / Standard

**Strong, thoughtful, and ahead of most commercial AI-governance framing** —
especially Decision Lineage, Structured Uncertainty, and the GIM threat model.
Worth developing. The conceptual architecture is sound and addresses real
problems in autonomous-system governance.

### As a Safety Guarantee Today

**Not yet earned.** The protocol is currently a well-articulated specification
with a working prototype in a narrow domain. Its structural claims should be
read as *intentions*, and a deployer who trusted them at face value would be
over-relying on controls that are largely unbuilt or fail-open.

### Recommended Next Step

**Close the calibration gap** — either by building the highest-leverage
mechanisms (fail-closed, a real isolation boundary, real OOB, tier enforcement,
skeletal GIM, adversarial tests) or by re-leveling the language so the claims
match the artifact. The seven remediation items above are ordered by impact and
represent a concrete path from current state to earned trust.
