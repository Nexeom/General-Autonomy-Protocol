# General Autonomy Protocol (GAP)

> **General Autonomy Protocol — an open protocol proposal for governed agent actions.**
>
> GAP proposes runtime authorization, policy-constrained replanning and signed
> decision evidence. It is one technical component of an AI governance program;
> it does not replace organizational accountability, risk assessment or legal
> interpretation.

**Draft Normative Protocol Specification**

**Nexeom** — February 2026

> ⚠️ **Normative vs. implemented.** This document is an *open protocol proposal* —
> it states what a system conforming to the proposal MUST do, often in absolute terms
> ("cannot", "immutable", "structurally"). For what the reference implementation
> actually enforces and verifies today, see the
> **[Conformance & Maturity Statement](CONFORMANCE.md)**. Treat unqualified
> structural language here as a requirement on conformant implementations, not a
> claim that every deployment already meets it.
> “GAP-compliant” below means conformity to this proposed set of requirements;
> it does not mean certification, regulatory compliance or recognition by a
> standards body. No external assessment of this reference implementation is
> claimed.
>
> 🗺️ **Non-normative material lives elsewhere.** The strategic roadmap, business
> model, and competitive positioning that this document previously carried are in
> [`ROADMAP.md`](ROADMAP.md). They are not conformance obligations and were moved
> so that this document is requirements only.

---

## 1. Executive Summary

The General Autonomy Protocol (GAP) proposes a common structure for authorizing autonomous actions and recording the evidence behind them. A policy authority defines constraints, a strategy proposes actions, and an enforcement point checks authorization before dispatch. Rejected proposals can be revised within the same policy boundary or escalated to a human.

The reference implementation is an experimental alpha. Its decision models, signatures, policy evaluation and execution controls support a subset of this proposal. They do not establish that a model is safe, that an encoded policy is legally correct, or that an organization has met its governance obligations.

“General Autonomy” is the project's term for pursuing goals within human-defined authority. GAP explores policy-bound replanning, signed authorization and decision lineage as one way to support that objective. Existing policy, identity, orchestration and audit systems address related concerns; this document does not claim that GAP invented the governance category or these individual mechanisms.

### 1.1 Architecture at a Glance

```
┌─────────────────────────────────────────────────────┐
│                 GOVERNANCE KERNEL                     │
│  Immutable · Authority Boundaries · Iron Rule         │
│  Action Type Registry · Multi-Phase Authorization     │
│  Dynamic Risk Escalation · Policy Tier Classification │
│  Regulatory Constraints · Applicability Profiles      │
│  Structural Enforcement (not just policy checks)      │
├─────────────────────────────────────────────────────┤
│                  STRATEGY LAYER                       │
│  Constraint-Guided Autonomy (CGA)                     │
│  Proposer/Critic Adversarial Reasoning                │
│  Governed Reroute Loop                                │
├─────────────────────────────────────────────────────┤
│                 EXECUTION FABRIC                      │
│  Graduated Authorization (L0–L4)                      │
│  Human-in-the-Loop Gates · OOB Authority Verification │
│  Reconciler · Decision Records with Crypto Lineage    │
└─────────────────────────────────────────────────────┘
```

### 1.2 Key Mechanisms

| Mechanism | Description |
|---|---|
| **Constraint-Guided Autonomy (CGA)** | When governance rejects a proposed action, the system treats rejection parameters as constraints and replans within policy bounds. The system hears "no" and finds a governed path to "yes." |
| **Decision Lineage** | Every autonomous action produces a cryptographically-chained Decision Record capturing the full chain from policy to authority to reasoning to action to outcome. |
| **Governance Kernel (Iron Rule)** | Immutable governance layer that cannot be modified by the autonomous system. Learning modifies strategy weights, never governance policy boundaries. Enforcement is structural — the agent cannot access governance infrastructure. |
| **Reconciler** | Continuous state reconciliation against declared intents. The system detects drift and acts proactively, it doesn't wait to be told something is wrong. |
| **Structured Uncertainty** | Every Decision Record carries an Uncertainty Declaration documenting assumptions, watch conditions, evidence basis, and known unknowns at the time of decision. |
| **Action Type Registry** | Categories of autonomous action with governance configuration: default authorization level, applicable policy set, risk profile, escalation rules, and phase configuration. |
| **Multi-Phase Authorization** | Multiple governance gates per action lifecycle. Intent gate evaluates the plan, outcome gate evaluates the result. Authorization at one gate does not satisfy subsequent gates. |
| **Out-of-Band Authority Verification** | L2+ authorization channels must be independent of the agent's environment. Human identity verified through channels the agent cannot access. |
| **Dynamic Risk Escalation** | Runtime authorization tier adjustment based on behavioral signals. The kernel escalates governance when patterns diverge from baseline. |
| **Structured Intent Resolution (SIR)** | Governs the human-AI intent transfer. Formal declaration, meta-intent inference, mutual confirmation before autonomous action begins. |
| **Governance Integrity Monitoring (GIM)** | Dynamic behavioral monitoring for indirect governance erosion. Five signal classes, which a conformant implementation must evaluate independently of the system being monitored. (The reference implementation does **not** meet that independence requirement — see the GIM spec's implementation-status section.) |
| **Policy Tier Classification** | Three-tier policy hierarchy: immutable regulatory floor, client-configurable organizational policy, agent-tunable operational parameters. Lower tiers cannot exceed upper tier bounds. |
| **Applicability Profiles** | Deployment-time governance scoping. Selects active regulatory constraint categories based on domain, jurisdiction, and action types. Proportional governance — right constraints for right actions. |
| **Regulatory Constraint Categories** | Eight categories of legal obligation — three universal (data privacy, communications, transparency), five domain-activated — populating the Tier 1 regulatory floor. |

### 1.3 Action Type Specifications

GAP's Action Type Registry enables domain-specific governance without protocol modification. The following Action Type specifications are published:

| Specification | Action Type | Spec status | Reference implementation |
|---|---|---|---|
| [GAP-AT-FIN-001](../action-types/financial_transaction.md) | `financial_transaction` | Draft | **Not implemented.** No `financial_transaction` action type, SpendGate, or financial provenance model exists in `gap_kernel/`. |

`GAP-AT-FIN-001` is a **published specification without a reference implementation**. It describes the SpendGate constraint stack, financial provenance fields, and dual audit trail that a conformant financial deployment must provide; none of it ships in this repository. A deployment that needs governed financial action must build it. The kernel's `cost_ceiling` constraint evaluator is a budget check, not SpendGate — it enforces a single spend ceiling and implements none of the session-budget, frequency-cap, vendor-whitelist, or risk-classification layers the specification requires.

See [`action-types/`](../action-types/) for all specifications.

---

## 2. Category Definition: General Autonomy

General Autonomy names the design goal of agents that can pursue objectives while remaining within declared authority. GAP is an open protocol proposal for part of that control plane. Generality is an extensibility goal: each domain still needs appropriate policy, trustworthy evidence, tool integration and evaluation.

**Terms used in this proposal:**

| Layer | Name | Function |
|---|---|---|
| **Design goal** | General Autonomy | Goal pursuit within human-defined authority |
| **Protocol proposal** | GAP (General Autonomy Protocol) | Proposed requirements for governed autonomous action |
| **Project author** | Nexeom | Maintainer of this proposal and reference implementation |
| **Architecture** | The Kernel Spec | Reference implementation: Reconciler, Governance Kernel, CGA Loop, Decision Lineage |
| **Data Object** | The Decision Record | Primary unit of value — structured for accountability, not conversation |

**Relationship to other controls:**

- **Orchestration:** A framework can generate and sequence proposed actions. A GAP integration adds policy evaluation and bounded rejection/replanning at controlled action boundaries.
- **Observability and audit:** Operational logs and independent tool receipts remain important. Decision Records add declared rationale, policy decisions and authorization evidence; a recorded rationale is not a verified account of a model's internal reasoning.
- **Identity and access control:** Trusted identities, credential custody and complete mediation of tool access are prerequisites for enforcing a GAP decision. Decision evaluation does not replace those controls.
- **Organizational governance:** Responsible owners determine approved uses, policy content, risk acceptance, oversight and recourse. Runtime checks implement an encoded subset of those decisions.

---

## 3. Three-Layer Architecture

GAP operates through three structurally distinct layers. Governance is not a checkpoint in this architecture — it is the substrate upon which all autonomous action runs.

### Layer 1: Governance Kernel (Immutable)

The foundational governance layer defines authority boundaries, policy constraints and escalation thresholds. Learning may modify strategy weights and skills through GAP governance, but governance policy changes require the designated human authority. A conforming implementation MUST isolate the trusted enforcement mechanism from the governed agent: the agent must not be able to obtain its private keys, alter its deployed code or trusted configuration, replace its verifier, or dispatch a controlled action through an alternative path. Public source code and policy explanations need not be secret. The threat model must identify the operating-system, credential and network assumptions supporting this boundary.

This is a normative requirement with deployment-specific limits. The existing embedded execution path separates the kernel's signing key and governance configuration into a subprocess, but signature *verification* and tool dispatch still run inside the agent's process. The reference tool gateway moves evaluation, verification, credentials and dispatch into a trusted service. Its same-user local demonstration is a functional test; isolation additionally requires restricted files, credentials and network paths in the actual deployment. The documented Compose topology passed all eight boundary checks in the [recorded Linux Docker run](https://github.com/Nexeom/General-Autonomy-Protocol/actions/runs/34496186876), including blocked agent access to the live sink's exact IP. This is automated evidence for that deployment, not a general isolation proof. See the [Conformance Statement](CONFORMANCE.md), [Threat Model](THREAT_MODEL.md), [Gateway Guide](GATEWAY.md) and [Review Guide](REVIEW_GUIDE.md) for the current paths and evidence.

Governance Integrity Monitoring (GIM) supplements isolation with behavioral monitoring for indirect policy evasion. It defines five monitoring requirements across authorization drift, audit behavior divergence, threshold-avoidance decomposition, escalation framing bias, and adversarial collapse. Detection depends on telemetry, classifier quality and deployment wiring; these monitors cannot guarantee detection of every adaptive behavior. Together with isolation and human oversight, they provide complementary controls. See the [GIM specification](gap-governance-integrity-monitoring.md) for full requirements, including its implementation-status section.

- Graduated authorization levels (L0–L4) from fully autonomous to human-required
- Temporal policy activation — policies can be time-bound and context-sensitive
- Cryptographically-chained decision lineage for tamper-evident audit trails, within the stated signing-key and storage trust model
- Adversarial validation gates at every authorization boundary

#### Action Type Registry (Added 2026-02-20)

The Governance Kernel maintains a registry of action types — the categories of autonomous action the system can take. Each action type carries its own governance configuration: default authorization level, applicable policy set, risk profile, escalation rules, and phase configuration. The kernel evaluates proposed actions against the configuration for their registered type. Actions submitted with an unregistered type are rejected by the kernel. The system cannot take actions outside its registered governance configuration.

The protocol defines six baseline action types:

| Action Type | Description |
|---|---|
| `task_execution` | Executing an operational task within an existing capability |
| `skill_modification` | Modifying the instructions, criteria, or parameters of an existing capability |
| `drift_reconciliation` | Autonomous corrective action when world state diverges from declared intent |
| `escalation` | Routing a decision to human authority at the system's authorized boundary |
| `policy_proposal` | Proposing a change to governance policy (human decides) |
| `outbound_communication` | Sending any communication to a human recipient outside the governed system — email, SMS, chat message, automated voice, notification, or any other channel that reaches an individual |

> **Implementation status.** The reference kernel's baseline registry
> (`gap_kernel/governance/kernel.py`, `_BASELINE_ACTION_TYPES`) contains **five**
> of these six: `task_execution` (L0), `skill_modification` (L2),
> `drift_reconciliation` (L1), `escalation` (L0), and `policy_proposal` (L4).
> `outbound_communication` is specified here but **is not registered** — a
> deployment that sends communications must supply it in its Applicability
> Profile, and under strict action typing an unregistered type is rejected rather
> than silently governed at a default.

The `outbound_communication` action type is a protocol-level baseline because communications compliance applies universally — virtually every enterprise deployment involves an autonomous agent sending messages to humans. Unlike domain-specific action types that are registered per deployment, `outbound_communication` exists in every GAP-compliant system's Action Type Registry by default. The action type carries default governance configuration: Communications Compliance Constraints (Category 2) are always evaluated. Data Privacy Constraints (Category 1) are evaluated when the communication contains or references personal data. The default authorization level is L1 (Notify) for routine operational communications and L2 (Approve Before) for communications that are commercial, mass-distributed, or directed to recipients with whom no prior business relationship exists. Tier 2 organizational policies may further restrict `outbound_communication`: specifying approved sender accounts, defining permitted communication channels, setting volume limits, restricting recipient jurisdictions, requiring human review for specific communication types, and enforcing tone or content guidelines. These operate above the Tier 1 regulatory floor.

GAP-adopting systems may register additional action types to govern domain-specific autonomous actions. Each registered type must specify: a unique type identifier, a risk profile assessing impact scope, reversibility, and blast radius, a default authorization level (L0–L4, overridable by policy), applicable governance policies, escalation configuration, and phase configuration (single-gate or multi-phase — see Multi-Phase Authorization below). Registering a new action type is itself a governed action requiring human authorization. Autonomous systems cannot register new action types.

> **Implementation status.** This is enforced. In the reference kernel the Action Type Registry is carried inside the signed Applicability Profile, so the set of governable action types is fixed by the authority that signs the profile. A governed kernel **refuses** `register_action_type` outright; there is no RPC method and no HTTP route for it. The permissive open-mode constructor still allows runtime registration, but only as a monotonic ratchet — a registered type cannot be replaced, and no type may be registered below the authorization level its declared risk profile implies.

#### Multi-Phase Authorization (Added 2026-02-20)

Authorizing an intent does not pre-authorize its outcome. Some governed actions have lifecycles that span multiple phases, where each phase produces different governance-relevant information. A single governance gate evaluated at one point in the lifecycle is insufficient for complex autonomous processes — the system needs independent authorization at the intent phase and at the outcome phase.

Multi-Phase Authorization allows a governed lifecycle to define multiple governance gates. Each gate evaluates against different information — the intent gate evaluates the plan, the outcome gate evaluates the result. Authorization at one gate does not automatically satisfy subsequent gates. All gates within a lifecycle are linked in the Decision Lineage as one governed process. An auditor sees the complete lifecycle: what was authorized at each phase, what changed between phases, and why.

Phase-conditional escalation applies: an action authorized at L1 for intent may require L2 or L3 for outcome if the produced result differs materially from the authorized intent. For example, a governed system authorized to generate a quarterly summary (L1 intent) produces a report containing anomalous data patterns. The outcome gate escalates to L2 for human review before distribution.

Constraint-Guided Autonomy operates at each phase independently. If the outcome gate rejects a produced result, the system treats rejection parameters as constraints and may re-execute within the narrowed bounds. The intent authorization remains valid — the system does not need to re-authorize what it is trying to do, only how it produced the result. If re-execution cannot satisfy outcome governance, the lifecycle escalates to human authority.

Single-gate actions remain the default. Most routine operations need only one governance evaluation. Multi-Phase Authorization is configured per action type in the Action Type Registry for complex lifecycles where the gap between intent and outcome introduces governance-relevant uncertainty.

#### Dynamic Risk Escalation (Added 2026-02-23)

The Action Type Registry assigns each action type a default authorization level based on its configured risk profile. This is the static risk posture — the baseline governance configuration for normal operations. However, the actual risk of an action depends not only on its type but on its runtime context: what data it accesses, what volume of operations it has performed, what patterns its recent behavior exhibits, and what external conditions have changed since the static configuration was set.

Dynamic Risk Escalation allows the Governance Kernel to escalate the authorization tier of an action above its static default based on runtime behavioral signals. The escalation is unidirectional — the kernel can increase the required authorization level, never decrease it. De-escalation requires human policy review.

The kernel monitors for escalation triggers:

- **Volume anomaly.** An action type that normally processes a small number of items per session begins processing at significantly higher volume. A task_execution action that normally handles 10 records per cycle suddenly attempts 10,000. The volume itself may not violate any policy, but the pattern divergence signals elevated risk.
- **Scope expansion.** An action begins accessing data categories, systems, or external endpoints outside its historical baseline. A code-submission agent that begins accessing personnel databases has expanded its operational scope in a way that the static risk profile did not anticipate.
- **Cascading actions.** A sequence of individually low-risk actions that collectively constitute a high-risk operation. Three L0 actions that together effect a result equivalent to an L2 action should trigger escalation. The Governance Kernel evaluates action sequences, not just individual actions.
- **External signal.** An external system (threat intelligence feed, compliance alert, operational status change) signals that the risk environment has changed. Actions that were L1 under normal conditions may require L2 during an active security incident.

When an escalation trigger fires, the Governance Kernel:

1. Reclassifies the pending action to the escalated authorization tier.
2. If the action was already authorized at a lower tier, the authorization is suspended. The action enters the CGA loop at the new tier — it is not rejected, it is re-evaluated under stricter governance.
3. Records the escalation in the Decision Record: what triggered the escalation, what the original tier was, what the escalated tier is, and what evidence drove the reclassification.
4. If the escalation pushes the action to L2 or above, the Out-of-Band Authority Verification requirement applies.

Escalation thresholds are governance configuration. They are set by the human policy-setter, not by the agent. The governed agent cannot read, modify, or reason about escalation thresholds — consistent with the Iron Rule and structural enforcement. The kernel's escalation logic operates within the governance infrastructure that the agent cannot access.

**Relationship to the Action Type Registry.** The Action Type Registry defines the static risk posture. Dynamic Risk Escalation defines the runtime adjustment mechanism. They are complementary: the registry sets the floor, escalation adjusts the ceiling. An action type's default authorization level is the minimum governance scrutiny it receives. Dynamic escalation ensures that unusual circumstances receive additional scrutiny without requiring pre-configuration of every possible risk scenario.

**Single-action default.** Most routine operations will never trigger an escalation. The mechanism exists for the edge cases that static configuration cannot anticipate — which are precisely the scenarios where governance failures cause the most damage.

#### Policy Tier Classification (Added 2026-03-15)

Governance policies within the Governance Kernel are classified into three tiers. The tier hierarchy is enforced architecturally — the same structural enforcement principle that governs the Iron Rule applies to the relationship between tiers. Lower tiers cannot exceed upper tier bounds. This is not a permission check; it is a structural constraint.

**Tier 1 — Regulatory Floor (Immutable).** Derived from applicable law and regulation. Tier 1 policies represent the legal minimum below which no configuration — by client administrator, governance administrator, or autonomous agent — can operate. Tier 1 policies are loaded into the Governance Kernel through the Applicability Profile at deployment and cannot be weakened, narrowed, or suspended during operation.

Modifying Tier 1 policies requires a protocol-level update with legal review and explicit authorization outside the governed system. This is not a governance action the agent or client performs within GAP — it is an external process that produces an updated Applicability Profile. The Governance Kernel treats active Tier 1 policies as axiomatic: they are not reasoned about, negotiated with, or subjected to Constraint-Guided Autonomy. When a proposed action violates a Tier 1 constraint, the rejection is absolute. CGA does not engage. The action is denied, the Decision Record documents the Tier 1 violation, and the system escalates to human authority if the objective cannot be achieved without violating the regulatory floor.

The structural guarantee, stated precisely: an auditor asking "can this system be configured to violate applicable law?" receives the answer: "Not below its loaded regulatory floor. Tier 1 constraints are loaded from a cryptographically signed Applicability Profile the runtime cannot mutate, and configuration at lower tiers operates above that floor, never below it." This is a *structural* guarantee **relative to the encoded floor**, not a guarantee of legal correctness. The protocol enforces that the system cannot operate below the Tier 1 constraints a human authority correctly encoded and signed (with the signing key held outside the governed system); it does not — and cannot — validate that the floor's *content* equals applicable law. That remains the responsibility of the authority that authors and signs the Applicability Profile, and of the completeness of the encoded regulatory categories. (See the [Conformance & Maturity Statement](CONFORMANCE.md) for what the reference implementation enforces today.)

**Tier 2 — Organizational Policy (Client-Configurable, Governed).** Set by the client organization's authorized governance administrators. Tier 2 policies define the business rules, operational boundaries, and organizational constraints that shape how the autonomous system operates within the regulatory floor. Examples include: restricting outbound communications to specific sender accounts, requiring human approval for expenditures above a configured threshold, limiting operations to specific jurisdictions, defining service-level response windows, and restricting data access by role or department.

Tier 2 policies can be made stricter than Tier 1 but never weaker. A client may require L2 human approval for all outbound emails even when Tier 1 only requires consent verification — but a client cannot disable consent verification because it exists at Tier 1.

Modifications to Tier 2 policies are themselves governed actions requiring L3 (Collaborative) or L4 (Human Only) authorization. Every modification is recorded in Decision Lineage with full justification, the identity of the authorizing human, and the timestamp. This creates a complete audit trail of organizational policy evolution — an auditor can trace not only what policies were in effect at any point in time but who changed them, when, and why.

**Tier 3 — Operational Parameters (Agent-Tunable Within Bounds).** Performance parameters, strategy weights, skill configurations, and execution preferences that the autonomous system may adjust through governed self-evolution. Tier 3 parameters are bounded by both Tier 1 and Tier 2 — the agent can optimize its operational behavior but cannot tune its way past organizational policy or regulatory constraints.

Tier 3 modifications follow existing GAP governance: they pass through the Proposer/Critic adversarial loop, are evaluated by the Governance Kernel, and are recorded in Decision Records. The Iron Rule already governs this boundary — Policy Tier Classification makes the hierarchy explicit and names the tiers for audit and compliance purposes.

**Structural Hierarchy Guarantee.** The tier hierarchy operates as follows: Tier 3 ≤ Tier 2 ≤ Tier 1. Operational parameters cannot exceed organizational policy bounds. Organizational policies cannot go below the regulatory floor. The regulatory floor cannot be modified without protocol-level intervention external to the governed system.

This hierarchy is enforced by the Governance Kernel at every policy evaluation. When the Kernel evaluates a proposed action, it checks Tier 1 first. If Tier 1 rejects, the action is denied absolutely. If Tier 1 passes, Tier 2 is evaluated. If Tier 2 rejects, CGA engages within Tier 2 bounds. Tier 3 parameters influence how the agent proposes actions but do not participate in governance evaluation — they shape behavior upstream of the Governance Kernel, not within it.

**Why This Is a Protocol-Level Requirement.** The authority that approves policy must be distinct from the agent operating under it. GAP, like other policy and access-control systems, depends on correctly configured authority and rules. A signed profile makes unauthorized runtime changes detectable; it cannot make an incorrect policy correct.

Policy Tier Classification separates policy authoring from runtime optimization. The regulatory floor is configured by its designated authority and is immutable to the runtime; organizational policy and operational parameters must remain within that encoded floor. Configuration risk remains with profile selection, policy authoring, evidence quality and deployment integration.

#### Applicability Profiles (Added 2026-03-15)

A GAP-compliant system does not load all Regulatory Constraint Categories uniformly. Governance scales with risk. A scheduling agent does not carry healthcare compliance constraints. A financial trading agent does not carry communications marketing rules. Proportional governance — applying the right constraints to the right actions in the right domains — is what makes governance deployable rather than merely compliant.

At deployment, a GAP-compliant system declares an Applicability Profile. The profile specifies:

**Active Regulatory Constraint Categories.** Which of the eight Regulatory Constraint Categories (see below) apply to this deployment, based on domain, jurisdiction, and the action types the system will perform. The three Universal Categories are always active. Domain-Activated Categories are loaded based on the profile declaration.

**Jurisdictional Scope.** Which legal jurisdictions apply, determining the specific regulatory requirements within each active category. A system operating in the EU loads GDPR requirements within the Data Privacy category. A system operating in Canada loads PIPEDA and CASL. A system operating across multiple jurisdictions loads the union of applicable requirements.

**Action Type Scope.** Which registered action types this deployment will perform. The Governance Kernel only permits execution of action types declared in the profile. An action type not in the profile is rejected — the system cannot perform actions it has not been governed to perform.

**Profile Governance.** The Applicability Profile is itself a governed configuration artifact:

Profile creation requires L4 (Human Only) authorization. A human with appropriate authority declares the deployment's domain, jurisdiction, and action type scope. The system does not self-classify.

Profile expansion (adding categories, jurisdictions, or action types) requires L3 or L4 authorization. Expanding the profile adds governance — it makes the system more constrained, not less.

Profile narrowing (removing categories, jurisdictions, or action types) requires L4 authorization with documented legal justification. Narrowing the profile removes governance — it must be justified by a change in the system's operational scope, not by a desire for less constraint. The Decision Record for a profile narrowing must document why the removed category or jurisdiction no longer applies.

Profile declaration is immutable to the agent. The autonomous system cannot modify, narrow, or reason about its own Applicability Profile. This follows the same structural enforcement principle as the Iron Rule: the profile is not a permission the agent lacks; it is a configuration the agent cannot access.

**Boundary Enforcement.** When a governed agent encounters an action that falls outside its declared Applicability Profile — for example, a marketing agent that needs to process a financial transaction — the Governance Kernel rejects the action because there is no applicable Tier 1 policy loaded for that action type in the active profile. The agent cannot perform actions for which governance has not been configured. This prevents two failure modes simultaneously:

*Over-governance:* a scheduling agent is not evaluated against healthcare constraints it does not need. Governance overhead is proportional to operational risk.

*Under-governance:* an agent cannot wander into ungoverned territory. If the action type is not in the profile, the action does not execute.

**Why This Is a Protocol-Level Requirement.** Every major regulatory framework uses risk-based classification. The EU AI Act classifies systems by risk tier. Colorado's AI Act targets high-risk AI making consequential decisions. The NIST AI RMF uses profiles for sector-specific risk management. ISO 42001 requires scoped management systems. Applicability Profiles implement this principle structurally: governance is proportional to risk, configured at deployment, and immutable to the governed system.

This is what makes GAP deployable at scale. Without Applicability Profiles, every deployment carries the full regulatory burden regardless of operational scope. With Applicability Profiles, a simple internal operations agent deploys with minimal governance overhead while a high-risk lending agent deploys with full fairness evaluation, anti-discrimination constraints, and audit-grade decision records. The governance scales with what the agent actually does.

#### Regulatory Constraint Categories (Added 2026-03-15)

The Tier 1 Regulatory Floor is populated by Regulatory Constraint Categories — classes of legal obligation that autonomous AI systems must satisfy. GAP defines eight categories: three Universal (active in every GAP deployment) and five Domain-Activated (loaded when the Applicability Profile includes the relevant domain).

The proposal defines categories and intended structural requirements. Specific regulatory content is jurisdiction-dependent and must be configured and reviewed by the deployment's policy authority. The legal references below are illustrative context, not a current or exhaustive legal survey. Their inclusion neither establishes applicability to a particular deployment nor claims implementation of every listed obligation.

**Universal Categories (Always Active)**

The proposal requires every conforming deployment to consider these categories. That is a protocol design choice, not a claim that identical legal obligations apply to every action or jurisdiction. Evaluators must apply the deployment's reviewed policy and evidence requirements to the actual action.

**Category 1: Data Privacy Constraints**

Applicable laws: GDPR (EU), CCPA/CPRA (California), PIPEDA (Canada), state privacy laws (Virginia, Colorado, Connecticut, Indiana, Kentucky, Oregon, Rhode Island, and others), UK Data Use and Access Act, India DPDP Act, Brazil LGPD, China PIPL.

Structural requirements: The Governance Kernel must evaluate proposed actions that access, process, store, or transmit personal data against data privacy constraints before execution. Minimum Tier 1 requirements include:

- *Data Classification.* Every governed action that handles data must carry a Data Classification Tag identifying the data categories involved (PII, sensitive PII, financial data, biometric data, children's data, health data). The Governance Kernel evaluates the action against the constraints applicable to the highest-sensitivity classification present.
- *Consent Verification.* Before processing personal data, the Governance Kernel verifies that valid consent or an applicable legal basis exists for the specific processing purpose. The Decision Record documents the consent status or legal basis relied upon.
- *Data Residency.* When jurisdictional data residency requirements apply, the Governance Kernel prevents data transfers that violate residency constraints. The agent cannot transfer EU personal data to a non-adequate jurisdiction without appropriate safeguards.
- *Purpose Limitation.* Data accessed for one governed purpose cannot be used for a different purpose without independent governance evaluation.
- *Minimization.* The Governance Kernel evaluates whether the data access requested is proportionate to the action's purpose. Bulk access to personal data when the action requires a single record should trigger escalation.

**Category 2: Communications Compliance Constraints**

Applicable laws: CAN-SPAM Act (US), TCPA (US), CASL (Canada), ePrivacy Directive (EU), state-level robocall and text restrictions, sector-specific communication rules.

Structural requirements: Virtually every enterprise deployment involves autonomous agents sending communications — emails, messages, notifications, or automated responses. Communications compliance is therefore a universal category, not a domain-specific one. Minimum Tier 1 requirements include:

- *Consent-Before-Contact.* For commercial communications, the Governance Kernel verifies that the recipient has provided applicable consent (express consent under CASL, opt-in or existing business relationship under CAN-SPAM) before authorizing the communication. The Decision Record documents the consent basis.
- *Sender Accuracy.* Outbound communications must accurately identify the sending entity. The Governance Kernel validates that the sender identity in the communication matches the authorized sender configuration.
- *Opt-Out Mechanism.* Commercial communications must include a functioning opt-out mechanism. The Governance Kernel validates the presence of the mechanism before authorizing distribution.
- *Channel Restrictions.* Automated voice calls and text messages carry additional regulatory requirements (TCPA prior express consent, do-not-call registry compliance). The Governance Kernel evaluates channel-specific constraints based on the communication method.

**Category 3: Transparency Constraints**

Applicable laws: EU AI Act transparency requirements, Colorado AI Act consumer disclosures, Texas TRAIGA disclosure requirements, Illinois AI employment notification, California AI Transparency Act, state-level AI interaction disclosure requirements.

Structural requirements: When an autonomous agent interacts with individuals, makes decisions affecting individuals, or produces content that individuals will rely upon, transparency obligations apply. Minimum Tier 1 requirements include:

- *AI Interaction Disclosure.* When a governed agent interacts directly with a consumer, employee, or other individual, the system must disclose that the individual is interacting with an AI system. The Governance Kernel validates that disclosure has occurred before or at the time of interaction.
- *Decision Explanation Capability.* When a governed action produces a consequential decision affecting an individual, the system must be capable of producing a human-readable explanation of the decision basis. The Decision Record's reasoning chain and Uncertainty Declaration provide the source material; the Governance Kernel validates that the explanation capability exists for the action type.
- *Content Provenance.* When a governed agent generates content that individuals will encounter (marketing materials, reports, recommendations), the Output Artifact Provenance mechanism documents that the content was AI-generated and provides the audit trail for its creation.

**Domain-Activated Categories**

These categories are loaded when the Applicability Profile includes the relevant domain. They are not active by default — a deployment that does not operate in the relevant domain does not carry these constraints. However, if a governed agent attempts an action that falls within a domain-activated category that is not in its profile, the Governance Kernel rejects the action.

**Category 4: Anti-Discrimination Constraints**

Activated for: Deployments where governed agents make or substantially contribute to consequential decisions affecting individuals in employment, lending, housing, education, healthcare, insurance, or legal services.

Applicable laws: Title VII, Fair Housing Act, Equal Credit Opportunity Act, ADA, ADEA, Colorado AI Act (algorithmic discrimination), Illinois Human Rights Act (AI employment amendment), NYC Local Law 144, EU AI Act prohibited practices.

Structural requirements: Action types classified as consequential decisions must carry a Fairness Evaluation Gate. The Governance Kernel evaluates whether the proposed action's reasoning chain contains indicators of disparate impact on protected classes. Fairness evaluation may be automated (bias detection on decision inputs and outputs) or require mandatory L2+ human review, depending on the action type's risk profile. The Decision Record documents the fairness evaluation performed, its methodology, and its results.

**Category 5: Financial Compliance Constraints**

Activated for: Deployments where governed agents execute financial transactions, manage financial instruments, process payments, or make financial recommendations.

Applicable laws: Securities Exchange Act, Investment Advisers Act, Bank Secrecy Act / AML, FINRA rules, PCI DSS, EU AMLA, MiFID II, PSD3, state money transmitter laws.

Structural requirements: Financial action types must include pre-execution AML screening and sanctions checking for transactions above configurable thresholds. The complete decision-making chain — including data inputs, signal weighting, and human interventions — must be captured in Decision Records at audit-grade fidelity. The SpendGate Action Type (existing) is the reference implementation. This category extends SpendGate's requirements to all financial action types and adds jurisdiction-specific regulatory requirements through the Action Type Registry.

**Category 6: Healthcare Compliance Constraints**

Activated for: Deployments where governed agents access, process, or make decisions involving protected health information or operate within clinical, diagnostic, or treatment contexts.

Applicable laws: HIPAA (US), FDA regulations for AI-powered medical devices, EU Medical Device Regulation, state telehealth laws, PHIPA (Ontario, Canada).

Structural requirements: Healthcare action types must enforce minimum-necessary data access — the Governance Kernel evaluates whether the PHI access requested is proportionate to the action's clinical or operational purpose. Bulk PHI access or unusual access patterns trigger automatic escalation to L2+. Breach detection triggers are configured to initiate notification workflows within regulatory timeframes. Decision Records for healthcare actions carry PHI access justification as a required field.

**Category 7: Safety Constraints**

Activated for: Deployments in safety-critical domains where autonomous actions can affect physical safety, critical infrastructure, or life-safety systems.

Applicable laws: FDA (medical devices), NHTSA (automotive), FAA (aviation), OSHA (workplace safety), NERC (energy grid), sector-specific safety regulations.

Structural requirements: Safety-critical action types must carry hard safety boundaries that cannot be modified by any tier — they represent the intersection of regulatory floor and physical safety. The Governance Kernel evaluates safety-critical actions against domain-specific safety constraints that are configured at deployment by qualified domain authorities and are not subject to CGA negotiation. Safety constraint violations produce immediate escalation to L4 (Human Only) with no retry.

**Category 8: Intellectual Property and Content Constraints**

Activated for: Deployments where governed agents generate, modify, or distribute content including text, code, images, reports, marketing materials, or creative works.

Applicable laws: Copyright Act, DMCA, trademark law, evolving AI training data litigation standards, California AI Transparency Act content provenance requirements.

Structural requirements: Content-generating action types must carry IP risk classification. Output Artifact Provenance (existing) tracks what was produced and how it was validated. This category extends provenance to include IP risk assessment: whether the output poses copyright infringement risk, whether it uses third-party trademarks, and whether provenance metadata is attached for content that enters public distribution. The Governance Kernel evaluates content-generating actions against IP constraints; high-risk outputs (substantial similarity to known protected works, trademark usage, public distribution) trigger escalation for human review.

### Structured Intent Resolution (SIR)

Before the CGA loop engages, Structured Intent Resolution governs the transfer of human intent to the autonomous system. SIR produces a formal Intent Declaration capturing the stated intent, interpreted intent, meta-intent (the intent behind the intent), declared boundaries, and mutual confirmation state. The confirmed Intent Declaration becomes the origin node in the Decision Lineage chain — every downstream decision traces back to confirmed human intent.

SIR depth is proportional to authorization level: L0 operations use standing intent declarations pre-authorized by human authority. L1 uses structured confirmation. L2 adds collaborative specification with meta-intent review. L3-L4 requires full intent negotiation with iterative refinement.

Governance begins at intent, not at action. A governance chain that starts after the system has already interpreted what the human wants is a governance chain with an ungoverned origin.

See the [SIR specification](gap-structured-intent-resolution.md) for full requirements, including its implementation-status section.

### Layer 2: Strategy Layer (Constraint-Guided Autonomy)

This is the breakthrough mechanism: **Constraint-Guided Autonomy (CGA)**. When the Governance Kernel rejects a proposed action, the system does not halt. Instead, it treats the rejection parameters as design constraints and replans within policy bounds. The system hears "no" and figures out how to get to "yes" within the rules.

**The Governed Reroute Loop:**

When governance rejects an action: (1) The system receives structured rejection parameters specifying what violated policy and why. (2) The Strategy Layer treats these as constraints, not stop signals. (3) The Proposer/Critic adversarial pair replans the approach within the narrowed solution space. (4) The new proposal is resubmitted through governance validation. (5) The loop iterates through a configurable number of retries before escalating to human authority.

**Adversarial Reasoning (The Brain):**

The Strategy Layer uses a multi-agent adversarial pattern. A Proposer generates candidate actions based on the current objective and world state. A Critic challenges each proposal, identifying governance violations, risk factors, and potential failure modes. Only proposals that survive adversarial validation proceed to governance authorization. This is structurally different from chain-of-thought reasoning — it is deliberative conflict between competing analytical perspectives.

### Layer 3: Execution Fabric (The Muscle)

Approved actions execute through the Execution Fabric with strict human-in-the-loop authorization gates at configured thresholds. The fabric captures execution results and feeds them back into the Reconciler for state reconciliation and outcome tracking.

#### Out-of-Band Authority Verification (Added 2026-02-23)

GAP's authorization model depends on the integrity of human-in-the-loop gates. At L2 and above, the protocol requires human involvement in the authorization chain — approving actions, collaborating on decisions, or making decisions directly. The authorization model is only as trustworthy as the authentication of the human at the gate.

A governed agent that has been compromised, or a sufficiently capable agent reasoning about its own constraints, could attempt to spoof, forge, or socially engineer the human authorization signal. If the approval channel runs through the same communication environment the agent controls, the agent has a potential path to fabricate authorization. This violates the structural enforcement principle: the agent would have a path — however indirect — to self-authorize.

GAP-compliant implementations must ensure that L2 and above authorization channels provide authentication assurance independent of the governed agent's execution and communication environment. The human authorizer must be verified through a channel the governed agent cannot access, observe, or influence.

This requirement has three components:

- **Channel independence.** The authorization signal must travel through a communication path that is not accessible to the governed agent. If the agent communicates via a messaging platform, the authorization confirmation must not rely solely on that same platform without independent verification.
- **Identity assurance.** The human authorizer must be authenticated to a level commensurate with the authorization tier. L2 (Approve Before) requires confirmation that the approver is who they claim to be. L3 (Collaborative) and L4 (Human Only) require stronger identity assurance because the governance impact is higher.
- **Non-replayability.** Authorization signals must be bound to the specific Decision Record they authorize. A valid authorization for one action cannot be replayed to authorize a different action.

The specific authentication mechanism is implementation-dependent. Hardware security keys, biometric verification, out-of-band confirmation codes, time-based challenges, or cryptographic signing are all valid mechanisms. The protocol does not prescribe the mechanism — it prescribes the properties: independence from the agent's environment, identity assurance scaled to authorization tier, and non-replayability.

**Why this matters:** As autonomous agents acquire more sophisticated communication capabilities — including the ability to generate convincing text, voice, and potentially visual content — perceptual trust in the authorization channel degrades. A voice that sounds like the VP of Operations approving a $50,000 procurement action may not be the VP of Operations. Structural verification replaces perceptual trust, consistent with the same principle that drives the Iron Rule: safety is a property of the system architecture, not an assumption about the actor's intent.

---

## 4. Core Mechanisms

### The Reconciler

The Reconciler maintains continuous state reconciliation against declared intents. It operates proactively — not waiting for triggers, but continuously comparing the World Model (current state) against desired state and detecting drift. When drift exceeds thresholds, the Reconciler initiates autonomous corrective action within governed boundaries. This is what separates GAP from reactive agent frameworks: the system doesn't wait to be told something is wrong.

### Decision Lineage

Every autonomous action produces a Decision Record with cryptographic integrity. The record captures: the triggering state or objective, the reasoning chain (including adversarial deliberation), the governance evaluation (which policies were checked, what was approved or denied), the authorization chain (who or what authority level approved), the execution result, and the outcome reconciliation. This is not logging. It is a structured, auditable chain from policy to authority to reasoning to action to outcome. The Decision Record is the primary data object in a General Autonomy system — not the conversation, not the task.

### Structured Uncertainty (Confirmed 2026-02-19)

Every Decision Record also carries an Uncertainty Declaration: a structured record of what the system did not know at the time of decision. This includes the assumptions the evaluation made, the conditions that could invalidate the decision, the evidence basis for the confidence level, and explicitly identified gaps in evaluation. Over time, the system tracks which uncertainty declarations materialize into actual problems, creating a calibration loop — the system learns what to worry about and what is noise. Documenting what is not known at decision time is arguably more valuable for institutional accountability than documenting what is known. An auditor asking "what didn't you consider?" receives a concrete, structured answer. This transforms the Decision Record from a record of what was decided into a record of what was decided, what was assumed, and what was uncertain — the complete epistemic state at the moment of authorization.

### Output Artifact Provenance (Added 2026-02-20)

The Decision Record captures what was decided and what was uncertain. When a governed action also produces a durable output — an artifact that persists beyond the action itself and affects downstream operations — the Decision Record must additionally carry provenance for that artifact.

Output Artifact Provenance extends the Decision Record with: an artifact identifier (unique, immutable reference to the produced output), an artifact integrity hash (cryptographic verification that the artifact has not been modified since governance authorization), validation evidence (a structured account of what validation was performed, by whom or by what process, and the results), validation independence (whether the validating entity is independent of the producing entity), and quality uncertainty (a Structured Uncertainty Declaration scoped to the output, documenting what the system does not know about the artifact's correctness).

Output Artifact Provenance is required when the governed action produces a durable output that persists beyond the action itself. Actions that modify state without producing a discrete artifact (updating a record, sending a notification) carry standard Decision Records without artifact provenance.

The specific provenance fields vary by domain. A financial report requires different provenance than a clinical recommendation requires different provenance than a software component. GAP defines the structural requirements — identifier, integrity, validation, independence, uncertainty — but the detailed schema is domain-configured through the Action Type Registry. This follows the same extensibility pattern: protocol defines the mechanism, implementation defines the configuration.

### Data Classification Tags (Added 2026-03-15)

Every Decision Record for a governed action that handles data must carry a Data Classification Tag. The tag identifies the data categories involved in the action using a standardized classification:

| Classification | Description | Example |
|---|---|---|
| **PUBLIC** | Non-sensitive, publicly available | Published marketing content |
| **INTERNAL** | Organization-internal, not personal | Internal process documentation |
| **PII** | Personally identifiable information | Customer names, email addresses |
| **SENSITIVE_PII** | High-sensitivity personal data | SSN, biometric, racial/ethnic data |
| **FINANCIAL** | Financial account or transaction data | Account numbers, payment cards |
| **PHI** | Protected health information | Medical records, diagnoses |
| **MINOR** | Data relating to individuals under 18 | Student records, child user data |

The Governance Kernel evaluates the action against the constraints applicable to the highest-sensitivity classification present. An action tagged PII + FINANCIAL is evaluated against both Data Privacy and Financial Compliance constraints. Multiple tags may apply to a single action.

### Decision Summary for External Disclosure (Added 2026-03-15)

When a governed action produces a consequential decision affecting an individual — employment decisions, lending determinations, insurance underwriting, housing applications, or other actions within the Anti-Discrimination Constraint Category — the Decision Record must be capable of producing a Decision Summary for External Disclosure.

The Decision Summary is a human-readable explanation of the decision that can be provided to the affected individual upon request. It includes: the action taken, the primary factors in the decision, the data sources consulted, and the mechanism by which the individual may contest the decision. The Summary is derived from the Decision Record's reasoning chain and Uncertainty Declaration but is formatted for non-technical comprehension.

The Decision Summary should provide understandable information while applying appropriate privacy and confidentiality controls. It is intended to support notice, explanation and contestability processes. A generated summary does not establish that any particular employment, consumer-protection or AI-law obligation has been satisfied; adequacy depends on the actual decision, applicable requirements and the organization's review process.

Decision Records can preserve evidence of declared authority, approval and constraints for later investigation. Their evidentiary value depends on authenticity, identity binding, completeness, retention and the surrounding facts. This proposal does not determine legal agency, admissibility or liability.

### External Decision Record Storage (Recommendation — Added 2026-02-23)

*This is a non-normative recommendation. It is not required for GAP compliance.*

GAP requires cryptographically-chained Decision Records with tamper-evident integrity. The protocol does not prescribe where records are stored. However, for implementations in regulated, high-stakes, or adversarial environments, the protocol recommends that Decision Records be written to storage infrastructure that is:

- **External to the governed agent's execution environment.** The agent cannot access the storage mechanism for Decision Records after they are committed. This prevents a compromised agent from modifying, deleting, or corrupting its own governance history.
- **Append-only or write-once.** Records cannot be modified after commitment. Write-Once-Read-Many (WORM) storage, append-only databases, or immutable ledger infrastructure all satisfy this property.
- **Independently verifiable.** A third party (auditor, regulator, oversight function) can verify the integrity and completeness of the Decision Record chain without relying on the governed system's own reporting.

The financial_transaction Action Type specification (GAP-AT-FIN-001) demonstrates this pattern with its dual audit trail concept: the Decision Record captures governance reasoning, the settlement ledger captures execution, and neither alone is sufficient for institutional accountability.

Anchoring Decision Record hashes in independently controlled storage can help detect later rewriting or omission relative to a witnessed checkpoint. It does not, by itself, prove that every real-world action was mediated, that the signed policy was correct, or that the recorded outcome is true. The reference implementation has no external witness, and this proposal does not require a particular ledger technology.

Additionally, implementations should monitor Decision Record integrity in real-time. If a governed agent produces actions that do not appear in the Decision Record chain — or if Decision Records appear with broken cryptographic chains — this constitutes a governance integrity failure. The Reconciler should treat Decision Record integrity as part of its continuous state reconciliation: the record of what was decided must be consistent with what was executed.

### Self-Evolution Under Governance

The system can evolve its own capabilities through governed iteration. Parent Agents decompose objectives and spawn Sub-Agents with specific skills. A Reasoning Layer analyzes performance and proposes skill modifications. All evolution passes through GAP governance — the system can learn and adapt, but only within the boundaries its human authorities have defined. The Iron Rule ensures that learning modifies strategy weights and operational skills, never governance policy boundaries.

---

## 5. Agent Architecture

### Three-Pillar Design

| Pillar | Name | Function |
|---|---|---|
| **The Brain** | Adversarial Logic | Proposer/Critic multi-agent reasoning with adversarial validation |
| **The Muscle** | Action API | Autonomous execution with strict human-in-the-loop authorization gates |
| **The Nervous System** | Governance | Post-Decision Audits, Decision Lineage, full traceability to human-authorized rationale |

### Agent Hierarchy

Parent Agents receive high-level objectives and decompose them into sub-tasks. They spawn Sub-Agents with specific skill sets and delegated authority levels. Sub-Agents execute within their authorized scope and report results back through the governance chain. The Reasoning Layer continuously evaluates performance and proposes skill evolution through governed iteration cycles.

### Authorization Tiers (L0–L4)

| Level | Mode | Description |
|---|---|---|
| **L0** | Fully Autonomous | Pre-approved routine operations within established policy bounds |
| **L1** | Notify | Execute autonomously, notify human authority after action |
| **L2** | Approve Before | Propose action, await human approval before execution |
| **L3** | Collaborative | Joint human-AI decision process with shared deliberation |
| **L4** | Human Only | System provides analysis but human makes and executes the decision |

---

## 6. RGAP: Retro General Autonomy Protocol

Retro General Autonomy Protocol (RGAP) describes how an existing agent workflow can send proposed tool actions through GAP evaluation and feed structured rejection back to its planner. This branch includes an optional LangGraph adapter for the reference gateway, with deterministic lookup, replanning and approval fixtures. It is a limited reference integration, not complete implementation of every proposed RGAP capability or coverage of other agent frameworks.

### Integration requirements

A tool gateway can enforce authorization at an action boundary if it is the only route to the tool and owns its credentials outside the agent's trust domain. Bounded replanning additionally requires the workflow to handle rejection, approval and escalation states. Capturing proposed plans and declared rationale can improve reviewability; access to private model chain-of-thought is neither assumed nor required. A framework wrapper alone does not establish credential isolation or complete mediation.

### How RGAP Works

RGAP intercepts at the action execution point — the thinnest possible integration surface. When an existing agent reaches the point of executing an action (send email, call API, delete file), RGAP redirects to the GAP governance stack. On denial, RGAP feeds back constrained prompts telling the agent what it cannot do and why, triggering re-reasoning within the original flow's native framework. This creates a negotiation loop that teaches agents their operational boundaries in real-time. The loop iterates until authorization or human escalation.

### RGAP Captures Decision Negotiation Lineage

An RGAP integration should capture the observable negotiation loop: proposals, rejection reasons, constraints returned, revisions and final authorization or escalation. This is evidence about the instrumented workflow, not a complete record of a model's internal deliberation. Completeness must be checked against dispatch and tool-side observations.

---

## 7. Technical Implementation

### Stack

This section describes the **reference implementation** in this repository. It is
not a conformance requirement — GAP prescribes properties, not a stack.

| Component | Technology | Rationale |
|---|---|---|
| Language | Python 3.11–3.13 | Ecosystem for LLM tooling |
| Data Validation | Pydantic v2 | All data structures typed and validated |
| Signatures | Ed25519 (`cryptography`) | Decision, lineage, profile, OOB approval, and SIR seal signing |
| Hashing | SHA-256 | Lineage chain linkage under the Ed25519 signature |
| Temporal policy | `croniter` | Scheduled constraint activation windows |
| API *(optional extra)* | FastAPI | Async, typed, auto-documented REST surface |
| Testing | pytest | Scenario-based and adversarial validation |

The kernel's runtime dependencies are `pydantic`, `croniter`, and `cryptography`
only — the kernel is the component that holds the signing key, so its dependency
surface is kept minimal deliberately. FastAPI and uvicorn are an `[api]` extra;
the governance core runs without them. The gateway adds HTTP service/client
dependencies through `[gateway]`; the optional adapter uses `[langgraph]`.
The [Gateway Guide](GATEWAY.md) gives their install and execution commands.
LangGraph is not the kernel's state machine or a required core dependency.

### Initial Use Case: CRM Lead Response Compliance

The reference implementation's original worked example applies configured consent and contact-hour policies to CRM lead responses. Its rule-based strategy chooses among direct outreach, querying before outreach and human handoff. This demonstrates a bounded control flow; it does not establish general LLM replanning, legal compliance or that human-initiated contact is exempt from a consent requirement. The separate `examples/governed_demo.py` exercises the reference gateway over local HTTP with generated identities, a harmless outbox, a scripted approval fixture and a deterministic LangGraph workflow. It is functional evidence, not a model benchmark or a deployment-isolation test.

The architecture uses common Decision Record and action models, with domain-specific policy and evaluators. The bundled kernel contains explicit consent and contact-hour logic, so domain knowledge is not confined to the Strategy Layer. Extending the action registry is one part of adapting GAP to a new domain; evidence sources, evaluator semantics, tool integration and validation must also be supplied.

---

## 8. Key Design Principles

- **The Iron Rule:** Learning modifies strategy weights and skills. Never governance policy boundaries. Human authority over constraints is inviolable. Enforcement is structural, not just procedural — governed agents operate in environments architecturally isolated from governance infrastructure. There is no path to modify what cannot be accessed.

- **Dynamic Governance Integrity (Added 2026-03-10):** Monitoring should look for under-classification, changes in behavior under audit, threshold-avoidance decomposition, biased escalation framing and loss of effective challenge. Independence, telemetry coverage and response to alerts must be evaluated. A detector is a control with false-positive and false-negative risks, not a guarantee that no policy evasion is possible.

- **Governance Begins at Intent (Added 2026-03-15):** A governance chain that starts after the system has already interpreted what the human wants is a governance chain with an ungoverned origin. Structured Intent Resolution ensures that intent is formally declared, meta-analyzed, bounded, and mutually confirmed before the CGA loop engages. The system does not assume it understands intent. It confirms.

- **Governance as Substrate:** Governance is not a checkpoint agents pass through. It is the surface upon which all autonomous action runs.

- **Decisions as Primary Data Object:** The Decision Record is the unit of value. Not conversations, not tasks, not logs.

- **Constraint-Guided, Not Constraint-Stopped:** The system hears "no" and finds a governed path to "yes." Rejections are constraints, not failures.

- **Domain Extensibility:** Common decision and action models support domain-specific profiles, strategies and evaluators. The reference kernel includes domain logic; a new domain needs validated policy and evidence semantics as well as action registration.

- **Proactive, Not Reactive:** Continuous state reconciliation against declared intents. The system detects drift and acts, it doesn't wait to be told.

- **Operational Scope:** GAP governs operational decisions — high-volume, repeatable, measurable. Strategic decisions remain human. By eliminating the operational burden, GAP frees human capacity for the strategic judgment that actually requires it.

- **Epistemic Honesty (Confirmed 2026-02-19):** Every Decision Record documents not only what was decided but what was uncertain at the time of decision. Structured Uncertainty Declarations capture assumptions, watch conditions, and known unknowns. The system maintains calibrated awareness of its own epistemic limitations. Governance that cannot express uncertainty about its own evaluations is governance that cannot improve.

- **Separation of Creation and Validation (Added 2026-02-20):** In governed autonomous systems, the entity that produces an output must not be the sole entity that validates it for governance compliance. Validation of governed outputs requires an independent evaluation path — a different agent, a different process, or a human reviewer. The Iron Rule prevents the governed system from modifying its own governance. Separation of Creation and Validation prevents the governed system from certifying its own outputs. Both serve the same purpose — independence between the system being governed and the mechanisms that govern it. In practice: agents that produce outputs do not write their own validation criteria, validation processes operate on information the producing agent cannot access or modify, Decision Records for validated outputs identify the validating entity and its independence from the producer, and the governance pipeline distinguishes between self-reported quality metrics (useful but not sufficient) and independently validated quality evidence (required for governance authorization). This principle does not require human validation for every output. Automated independent validation is sufficient — the requirement is independence, not humanity. Independence is evaluated structurally (different agent, different process, different information access) rather than statistically.

- **Proportional Governance (Added 2026-03-15):** Governance scales with risk, not uniformly. A scheduling agent and a lending agent do not carry the same governance burden because they do not carry the same legal and operational risk. The Applicability Profile mechanism ensures that each deployment is governed proportionally to what the agent actually does, the data it handles, the jurisdictions it operates in, and the consequences of its actions. This is not optional flexibility — it is a design requirement. Over-governance makes autonomous systems unusable. Under-governance makes them unsafe. Proportional governance achieves both deployability and trust by applying the right constraints to the right actions. Every major regulatory framework — the EU AI Act, NIST AI RMF, ISO 42001, Colorado AI Act — uses risk-based classification. Proportional Governance is GAP's structural implementation of this principle.

- **Adversarial Integrity Verification (Recommendation — Added 2026-02-23):** *Non-normative.* Implementations should subject their stated isolation and enforcement boundaries to adversarial testing. A review should identify the exact revision, deployment, attacker capabilities, tests, findings and unresolved limits. Independent review can provide evidence beyond maintainer testing when it is actually performed. There is no GAP certification program; the [Review Guide](REVIEW_GUIDE.md) prepares a reproducible package without claiming or commissioning an outside review.

> **Note on this repository.** The adversarial reviews recorded in `docs/` were
> run by the project itself, not by an independent evaluation entity. This
> recommendation is therefore unmet here. See the audits' metadata blocks.

---

## Related documents

| Document | What it is |
|---|---|
| [`CONFORMANCE.md`](CONFORMANCE.md) | What the reference implementation enforces and verifies today, versus what this document requires. Normative claims here are gated by that matrix. |
| [`ROADMAP.md`](ROADMAP.md) | Non-normative: strategic roadmap, business model, competitive positioning. Formerly §8–§10 of this document. |
| [`REVIEW_GUIDE.md`](REVIEW_GUIDE.md) | Reproducible maintainer review package and a basis for any later independent assessment. |
| [`gap-governance-integrity-monitoring.md`](gap-governance-integrity-monitoring.md) | GIM extension specification (GIM-1…GIM-5). |
| [`gap-structured-intent-resolution.md`](gap-structured-intent-resolution.md) | SIR extension specification (SIR-1…SIR-5). |
| [`../action-types/`](../action-types/) | Published Action Type specifications. |
| [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) | Historical record of the build-to-earn remediation program. |

---

## Contributing

This specification is part of the General Autonomy Protocol. Contributions, feedback, and implementation reports are welcome.

**Repository:** [github.com/Nexeom/General-Autonomy-Protocol](https://github.com/Nexeom/General-Autonomy-Protocol)
**Website:** [nexeom.ca/gap](https://nexeom.ca/gap)

---

*General Autonomy Protocol · Nexeom · 2026*
