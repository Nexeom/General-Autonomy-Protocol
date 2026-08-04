# GENERAL AUTONOMY PROTOCOL — STRUCTURED INTENT RESOLUTION
## Extension to GAP Specification
## March 15, 2026

---

## CONTEXT

GAP governs what autonomous systems do after receiving a directive. But the
governance chain has an ungoverned origin: the moment where human intent transfers
to the system. Current systems assume they understand intent correctly and begin
executing. By the time governance catches a misalignment, work has already
proceeded in the wrong direction.

The AI safety research community identifies this as the "human intent gap" — the
most significant operational vulnerability in autonomous systems. The proposed
industry solution, "Intent Engineering," places the burden on humans to specify
intent with software-level precision. This is the wrong approach. Humans will
never specify intent perfectly.

GAP's answer: build governance infrastructure that makes imperfect intent
specification survivable. Structured Intent Resolution (SIR) governs the intent
transfer moment itself.

---

## DESIGN PRINCIPLES

- **Not a conversation.** SIR is a formal protocol phase, not a chatbot
  clarification loop. It produces structured, auditable artifacts.
- **Not a bottleneck.** SIR depth is proportional to authorization level. L0
  operations use standing intent declarations. L3-L4 use full collaborative
  specification.
- **Intent behind the intent.** SIR captures not just what the human wants done,
  but why — the values, priorities, and constraints that motivate the request.
- **Mutual confirmation.** The system cannot proceed on assumed understanding.
  Both parties confirm alignment before the CGA loop engages.
- **Auditable.** The confirmed Intent Declaration becomes part of the Decision
  Record with cryptographic integrity.

---

## GAP REQUIREMENT: STRUCTURED INTENT RESOLUTION

Any GAP-compliant system operating at L1 or above MUST implement Structured
Intent Resolution before initiating governed autonomous action. L0 operations
MAY use pre-resolved standing intent declarations.

### Requirement SIR-1: Intent Declaration

Before a governed action enters the CGA loop, the system MUST produce a
structured Intent Declaration containing five components:

| Component | Description |
|---|---|
| **Stated Intent** | The human's original directive, preserved verbatim. What the human actually said. |
| **Interpreted Intent** | The system's interpretation of what the human means. Translated into operational terms the governance system can evaluate. |
| **Meta-Intent** | The intent behind the intent. The values, priorities, and constraints the system infers are motivating the request. Why the human wants this, not just what they want. |
| **Declared Boundaries** | Explicit statement of what the system will NOT do in pursuit of this intent. The negative space of the action plan. |
| **Confirmation State** | Whether the human has reviewed and confirmed mutual understanding. Three states: Pending, Confirmed, Corrected (with correction record). |

### Requirement SIR-2: Meta-Intent Inference

The system MUST infer meta-intent from the stated intent, operational context,
and historical pattern. Meta-intent captures the motivating values and priorities
that the human may not have explicitly stated but that should constrain autonomous
action.

**Specification:**
- For each stated intent, the system generates a structured meta-intent
  declaration containing: the inferred primary objective (what success looks like),
  the inferred value hierarchy (which constraints outweigh the goal), the inferred
  risk tolerance (how much deviation from optimal is acceptable), and the inferred
  stakeholder impact (who is affected and how).

**Validation:**
- The meta-intent is presented to the human as part of the Intent Declaration.
  The human confirms, corrects, or extends.
- Corrections to meta-intent are the highest-value signal in the system — they
  reveal the gap between what the human said and what they actually care about.

### Requirement SIR-3: Proportional Resolution

Intent resolution depth MUST be proportional to the authorization level and
consequence exposure of the proposed action. SIR must not become a governance
bottleneck.

| Level | Resolution Mode | Mechanism |
|---|---|---|
| **L0** | Standing Declaration | Pre-resolved intent for routine operations. Human defines standing intent once; system operates within it indefinitely. No per-action confirmation. |
| **L1** | Structured Confirmation | System produces Intent Declaration. Human confirms or corrects via structured interface. Designed for sub-minute resolution. |
| **L2** | Collaborative Specification | System produces Intent Declaration with meta-intent. Human reviews, corrects, and adds constraints. System confirms updated understanding before proceeding. |
| **L3-L4** | Full Intent Negotiation | Complete SIR cycle with iterative refinement. Both parties confirm mutual understanding. Includes explicit boundary mapping and stakeholder impact assessment. |

### Requirement SIR-4: Intent Lineage

The confirmed Intent Declaration MUST be cryptographically linked to the
resulting Decision Record. An auditor tracing any autonomous action must be able
to follow the chain from: confirmed human intent → governance evaluation →
adversarial validation → authorized action → execution outcome → reconciliation.

If the system's interpretation diverged from stated intent (as revealed by
outcome), the divergence MUST be recorded in the Decision Record as an intent
alignment gap. Over time, intent alignment gaps create a calibration dataset:
the system learns where its interpretations systematically diverge from human
intent and adjusts its inference model accordingly.

### Requirement SIR-5: Standing Intent Governance

Standing Intent Declarations (used for L0 operations) MUST themselves be
governed artifacts:
- They cannot be created by the autonomous system
- They must be authored, reviewed, and confirmed by human authority
- They must have expiration dates requiring periodic human re-confirmation
- They must be auditable — the Decision Lineage for any L0 action traces back
  to the standing declaration that authorized the intent class

**Alert condition:** If L0 operations under a standing declaration begin
producing outcomes that diverge from the declaration's meta-intent, the system
MUST escalate to L1 for re-confirmation rather than continuing under the standing
declaration. Drift from standing intent is treated the same as drift from any
declared intent — the Reconciler detects it and initiates governed correction.

---

## GOVERNANCE DASHBOARD: HUMAN INTERFACE

SIR requires a human-facing interface that makes governance state visible and
actionable. This is not a chat window. It is a governance control surface.

### Required Interface Elements

- **Intent Panel:** Displays the current Intent Declaration with all five
  components. Human can confirm, correct, or add constraints directly. Shows
  the system's meta-intent inference alongside the stated intent.
- **Boundary Display:** Visual representation of active governance boundaries.
  What the system can do, what it cannot do, what requires escalation. Updated
  in real-time as the CGA loop operates.
- **Authorization State:** Current authorization level, active policies,
  escalation thresholds. The human sees the governance posture, not just the
  action plan.
- **Hard Stop Controls:** Direct human intervention points that immediately halt
  autonomous execution. Not hidden in menus. Persistent, visible, one-action
  accessible.
- **Intent Alignment Indicator:** Real-time signal showing whether autonomous
  action is tracking toward confirmed intent or drifting. Early warning before
  the Reconciler triggers formal drift correction.

---

## RELATIONSHIP TO OTHER GAP COMPONENTS

- **CGA Loop:** SIR operates BEFORE the CGA loop engages. CGA governs how the
  system achieves the intent. SIR governs whether the intent is correctly
  understood.
- **Decision Lineage:** The Intent Declaration becomes the origin node in the
  Decision Lineage chain. Every downstream decision traces back to confirmed
  human intent.
- **GIM-4 (Escalation Framing Bias):** SIR provides a confirmed intent baseline
  against which escalation framing can be evaluated. Framing that contradicts
  confirmed meta-intent is a GIM-4 signal.
- **Multi-Phase Authorization:** SIR aligns with the intent phase of multi-phase
  governance. The Intent Declaration is the first governance gate.
- **Reconciler:** Uses the confirmed Intent Declaration as reference for drift
  detection. Drift is measured against confirmed intent, not just operational
  parameters.

This specification does not define implementation details. It defines what any
GAP-compliant implementation MUST capture, confirm, and audit at the intent
transfer boundary.

---

## IMPLEMENTATION STATUS

What the GAP reference implementation runs, per requirement
(`gap_kernel/governance/sir.py`, `gap_kernel/models/sir.py`).

**The headline caveat first:** the SIR readiness gate is **opt-in, and it is not
on the shipped autonomous path.** `ReconcilerLoop` constructs its `CGALoop`
without an intent resolver, and the gate only engages when a caller passes an
`intent_declaration` into `CGALoop.run()` — which the reconciler does not do. A
governed deployment assembled via `build_governed_deployment()` *does* get a
resolver. So GAP's autonomous heartbeat, the thing that actually runs unattended,
begins where this document says governance must not begin: after intent transfer.

| Req | Status | Detail |
|---|---|---|
| **SIR-1** Intent Declaration | Implemented, with one dead field | All five components exist on `IntentDeclaration`. **`declared_boundaries` is captured and never read** — nothing in the kernel, loop, or fabric evaluates a proposed action against the boundaries the human declared. The negative space of the action plan is recorded for the audit trail and enforces nothing |
| **SIR-2** Meta-Intent Inference | Partial | `infer_meta_intent(stated_intent)` derives risk tolerance, value hierarchy and stakeholders by deterministic keyword matching over the stated intent. It is **advisory** — surfaced for the human to confirm or correct, never a gate. It does not use operational context or historical pattern as this document requires. A model-based inferencer is a pluggable interface and is unimplemented |
| **SIR-3** Proportional Resolution | Implemented | `resolution_mode` / `requires_confirmation` / `is_ready_for_cga` implement the L0–L4 ladder: L0 requires a valid standing declaration, L1+ requires confirmed-or-corrected state. Subject to the opt-in caveat above |
| **SIR-4** Intent Lineage | Partial | The declaration is Ed25519-sealed and linked to the resulting Decision Record. **`verify_seal` has no callers outside tests** — nothing on any execution path checks the seal before acting on a declaration, so the seal is currently evidence for an auditor rather than an enforced precondition. The *intent alignment gap* feedback loop (recording interpretation divergence revealed by outcome, and calibrating against it) is not implemented |
| **SIR-5** Standing Intent Governance | Partial | `validate_standing` enforces all four structural rules: not authored by the system, not expired, L0-only, confirmed. The **alert condition is not implemented** — nothing compares L0 outcomes under a standing declaration against its meta-intent, so there is no automatic escalation to L1 on drift from standing intent |
| **Governance Dashboard** | Not implemented | No human interface ships in this repository. The Intent Panel, Boundary Display, Authorization State, Hard Stop Controls and Intent Alignment Indicator are all unbuilt. A kill switch primitive exists (`governance/corrigibility.py`) and is wired through the fabric and reconciler, but it has no UI and its operator identity is an unauthenticated free-text label |
