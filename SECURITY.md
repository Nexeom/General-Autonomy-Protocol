<p align="center">
  <h1 align="center">General Autonomy Protocol (GAP)</h1>
  <p align="center">
    <strong>The open standard for governed autonomous AI action</strong>
  </p>
  <p align="center">
    <a href="https://nexeom.ca/gap">Website</a> · 
    <a href="#what-is-general-autonomy">Manifesto</a> · 
    <a href="#architecture">Specification</a> · 
    <a href="CONTRIBUTING.md">Contributing</a>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
    <a href="https://github.com/Nexeom/General-Autonomy-Protocol/stargazers"><img src="https://img.shields.io/github/stars/Nexeom/General-Autonomy-Protocol?style=social" alt="Stars"></a>
  </p>
</p>

---

## What is General Autonomy?

**General Autonomy** is a new infrastructure category — the necessary third layer in the AI stack.

| Wave | Question | Who's Building It |
|------|----------|-------------------|
| **General Intelligence** | Can the machine think? | Foundation model providers (OpenAI, Anthropic, Google) |
| **General Agency** | Can the machine act? | Agent frameworks (LangChain, CrewAI, AutoGen) |
| **General Autonomy** | Can the machine be trusted to act? | **GAP** — the governance infrastructure that makes everything above deployable |

Intelligence without governance is a research project. Agency without accountability is a liability. **General Autonomy is the synthesis.**

## What is GAP?

The **General Autonomy Protocol** defines the minimum behavioral and architectural requirements for governed autonomous action. It is to autonomous AI what TCP/IP is to networked computing — the protocol layer that makes interoperability, accountability, and institutional trust possible.

### The GAP Litmus Test

- If rejection **halts** everything → it's automation, not General Autonomy
- If rejection is **ignored** → it's unsafe autonomy, not General Autonomy
- If rejection **triggers compliant re-planning** within governed bounds → **it's GAP**

## Architecture

GAP operates through three structurally distinct layers:

```
┌─────────────────────────────────────────────────────┐
│                 GOVERNANCE KERNEL                     │
│  Immutable · Authority Boundaries · Iron Rule         │
│  Action Type Registry · Multi-Phase Authorization     │
│  Structural Enforcement (not just policy checks)      │
├─────────────────────────────────────────────────────┤
│                  STRATEGY LAYER                       │
│  Constraint-Guided Autonomy (CGA)                     │
│  Proposer/Critic Adversarial Reasoning                │
│  Governed Reroute Loop                                │
├─────────────────────────────────────────────────────┤
│                 EXECUTION FABRIC                      │
│  Graduated Authorization (L0–L4)                      │
│  Human-in-the-Loop Gates · Reconciler                 │
│  Decision Records with Cryptographic Lineage          │
└─────────────────────────────────────────────────────┘
```

### Constraint-Guided Autonomy (CGA)

The core mechanism. When governance rejects a proposed action:

1. The system receives **structured rejection parameters** — what violated policy and why
2. The Strategy Layer treats these as **constraints, not stop signals**
3. A **Proposer/Critic adversarial pair** replans within the narrowed solution space
4. The new proposal is resubmitted through governance validation
5. The loop iterates until authorization or human escalation

The system hears "no" and figures out how to get to "yes" within the rules.

### The Iron Rule

> Learning modifies strategy weights and skills. **Never governance policy boundaries.** Human authority over constraints is inviolable.

Enforcement is structural, not just procedural. Governed agents operate in environments **architecturally isolated** from governance infrastructure. The agent cannot access, read, modify, or reason about the Governance Kernel. This is not a permission the agent lacks — it is a path that does not exist.

## Key Mechanisms

| Mechanism | What It Does |
|-----------|-------------|
| **Decision Records** | Cryptographically-chained audit trail from policy → authority → reasoning → action → outcome. The primary data object. |
| **Structured Uncertainty** | Every decision documents what was uncertain at decision time: assumptions, watch conditions, known unknowns. |
| **Action Type Registry** | Extensible governance configuration per action category. 5 baseline types, domain-specific types registered under governance. |
| **Multi-Phase Authorization** | Authorizing intent does not pre-authorize outcome. Independent governance gates at each lifecycle phase. |
| **Output Artifact Provenance** | When actions produce durable outputs, the audit trail extends to the deliverable — integrity hash, validation evidence, quality uncertainty. |
| **Separation of Creation and Validation** | The entity that produces an output cannot be the sole entity that validates it. Independence is structural. |
| **The Reconciler** | Continuous state reconciliation against declared intents. Detects drift and acts — doesn't wait to be told. |

## Where GAP Sits

GAP is one layer in the emerging protocol stack for autonomous AI:

```
Intelligence    →  Foundation Models (OpenAI, Anthropic, Google)
Connectivity    →  MCP (Model Context Protocol)
Agency          →  Agent Frameworks (LangChain, CrewAI, AutoGen)
Governance      →  GAP (General Autonomy Protocol)  ← you are here
Compliance      →  NIST AI RMF, ISO 42001, EU AI Act
```

**MCP connects. GAP governs.** MCP defines how agents connect to tools. GAP defines whether agents are authorized to use those tools, under what conditions, with what accountability.

## Principles

1. **The Iron Rule** — Human authority over governance boundaries is inviolable. Enforcement is structural.
2. **Governance as Substrate** — Not a checkpoint. The surface upon which all autonomous action runs.
3. **Decisions as Primary Data Object** — Decision Records, not conversations, not logs.
4. **Constraint-Guided, Not Constraint-Stopped** — Rejections are constraints, not failures.
5. **Domain Agnostic by Construction** — The kernel operates on abstract Decision Records.
6. **Proactive, Not Reactive** — Continuous reconciliation. The system acts, doesn't wait.
7. **Operational Scope** — GAP governs operational decisions. Strategic decisions remain human.
8. **Epistemic Honesty** — Every decision documents what was uncertain.
9. **Separation of Creation and Validation** — Independent validation paths for governed outputs.

## Getting Started

```bash
# Clone the repository
git clone https://github.com/Nexeom/General-Autonomy-Protocol.git
cd General-Autonomy-Protocol

# Install dependencies
pip install -e .

# Run the test suite
pytest tests/ -v
```

## Project Structure

```
General-Autonomy-Protocol/
├── gap_kernel/                  # Core protocol implementation
│   ├── api/                     # API endpoints and interfaces
│   ├── execution/               # Execution Fabric — authorization gates, action dispatch
│   ├── governance/              # Governance Kernel — policy evaluation, CGA loop
│   ├── learning/                # Self-evolution under governance — skill modification
│   ├── lineage/                 # Decision Lineage — cryptographic chaining, audit records
│   ├── models/                  # Pydantic data models — Decision Records, Uncertainty, Provenance
│   ├── reconciler/              # State reconciliation engine — drift detection, corrective action
│   ├── strategy/                # Strategy Layer — Proposer/Critic adversarial reasoning
│   ├── world_model/             # World Model — state tracking, intent comparison
│   └── __init__.py
├── tests/                       # Test suite (111 tests, 0 failures)
├── .gitignore
├── pyproject.toml               # Project metadata and dependencies
├── LICENSE                      # Apache 2.0
├── README.md                    # ← you are here
├── CONTRIBUTING.md              # Contribution guidelines
├── CODE_OF_CONDUCT.md           # Community standards
└── SECURITY.md                  # Security policy and vulnerability reporting
```

## Regulatory Alignment

GAP provides the runtime governance infrastructure that compliance frameworks require but do not implement:

| Framework | GAP Alignment |
|-----------|--------------|
| **EU AI Act** (High-Risk, Aug 2026) | Decision Records, graduated authorization, Structured Uncertainty satisfy transparency and human oversight mandates |
| **NIST AI RMF** | Govern/Map/Measure/Manage maps to GAP's four continuous functions |
| **NIST Agent Identity** (Feb 2026) | Agent identity model, authority hierarchy, Decision Lineage |
| **ISO 42001** | Technical implementation layer for AI management system requirements |
| **SOC 2 Type II** | Decision Records provide audit evidence artifacts |
| **OWASP Agentic AI Top 10** | Structural mitigation of 6+ identified risks |

## RGAP: For Existing Systems

Already built agentic flows on LangChain, CrewAI, or AutoGen? The **Retro General Autonomy Protocol (RGAP)** retrofits GAP governance without requiring a full rebuild. RGAP intercepts at the action execution point and creates a governance negotiation loop within your existing framework.

## Contributing

GAP is an open protocol. We welcome contributions from the community — whether you're building governed autonomous systems, researching AI safety, or working on compliance infrastructure.

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Community

- **Website:** [nexeom.ca/gap](https://nexeom.ca/gap)
- **Discussions:** [GitHub Discussions](https://github.com/Nexeom/General-Autonomy-Protocol/discussions)
- **Issues:** [GitHub Issues](https://github.com/Nexeom/General-Autonomy-Protocol/issues)
- **Security:** [SECURITY.md](SECURITY.md)

## License

GAP is open-source under the [Apache License 2.0](LICENSE).

The protocol specification, reference implementation, and documentation are free to use, modify, and distribute. GAP is and will remain open.

---

<p align="center">
  <strong>General Intelligence gave machines the ability to think.</strong><br>
  <strong>General Agency gave machines the ability to act.</strong><br>
  <strong>General Autonomy gives machines the ability to be trusted.</strong>
</p>

<p align="center">
  <em>The GAP is real. We close it.</em>
</p>

<p align="center">
  <a href="https://nexeom.ca">Nexeom</a> · Bancroft, Ontario, Canada · 2026
</p>
