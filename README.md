<p align="center">
  <h1 align="center">General Autonomy Protocol (GAP)</h1>
  <p align="center">
    <strong>A governance kernel that decides whether an autonomous agent may act — and signs the record</strong>
  </p>
  <p align="center">
    <a href="https://nexeom.ca/gap">Website</a> · 
    <a href="#what-is-general-autonomy">Manifesto</a> · 
    <a href="docs/PROTOCOL_SPECIFICATION.md">Specification</a> · 
    <a href="docs/CONFORMANCE.md">Conformance</a> · 
    <a href="CONTRIBUTING.md">Contributing</a>
  </p>
  <p align="center">
    <a href="https://github.com/Nexeom/General-Autonomy-Protocol/actions/workflows/ci.yml"><img src="https://github.com/Nexeom/General-Autonomy-Protocol/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
  </p>
</p>

---

## Status

GAP is a specification plus a Python reference implementation, at `v0.2.0-alpha`.
Concretely:

- **639 tests pass.** CI runs them on Python 3.11 / 3.12 / 3.13 and on Windows,
  and fails the build under 90% line coverage (measured: 93%). A separate CI job
  runs the exact commands in [Getting Started](#getting-started), so the
  documented install path breaks the build when it breaks.
- **No external security review. No tagged release. No adopters to point to.
  One author.** Every claim below is backed by the code and tests in this
  repository, and by nothing else.
- **[docs/CONFORMANCE.md](docs/CONFORMANCE.md) is the source of truth** for what
  is enforced today versus specified-but-not-built. Where this README and the
  conformance statement disagree, the conformance statement wins.

## What is General Autonomy?

**General Autonomy** is the argument this project is built on: that the third
layer of the AI stack is the governance layer, and that it is missing.

| Wave | Question | Who's Building It |
|------|----------|-------------------|
| **General Intelligence** | Can the machine think? | Foundation model providers (OpenAI, Anthropic, Google) |
| **General Agency** | Can the machine act? | Agent frameworks (LangChain, CrewAI, AutoGen) |
| **General Autonomy** | Can the machine be trusted to act? | GAP is this project's answer: a kernel the agent must pass through before it acts |

Intelligence without governance is a research project. Agency without
accountability is a liability. **General Autonomy is the synthesis.**

## What is GAP?

The **General Autonomy Protocol** defines the minimum behavioral and
architectural requirements for governed autonomous action. It is two things:

- **A specification** ([docs/PROTOCOL_SPECIFICATION.md](docs/PROTOCOL_SPECIFICATION.md))
  stating normative requirements — what a governed autonomous system must do.
- **A reference implementation** (`gap_kernel/`) that earns those requirements
  one at a time. The conformance statement records which are earned.

### The GAP Litmus Test

- If rejection **halts** everything → it's automation, not General Autonomy
- If rejection is **ignored** → it's unsafe autonomy, not General Autonomy
- If rejection **triggers compliant re-planning** within governed bounds → **it's GAP**

## Architecture

> 📖 **Full specification:** this README is the overview. The complete GAP
> protocol specification — all 11 sections (three-layer architecture, core
> mechanisms, authorization tiers L0–L4, RGAP, technical implementation,
> roadmap, design principles) — lives in
> **[docs/PROTOCOL_SPECIFICATION.md](docs/PROTOCOL_SPECIFICATION.md)**.
>
> 🔎 **What's actually built and verified** vs. specified as a normative
> requirement: see the
> **[Conformance & Maturity Statement](docs/CONFORMANCE.md)**.

GAP operates through three structurally distinct layers:

```
┌─────────────────────────────────────────────────────┐
│                 GOVERNANCE KERNEL                     │
│  Signed Applicability Profile · Authority Boundaries  │
│  Action Type Registry · Multi-Phase Authorization     │
│  Fail-Closed Evaluation · Ed25519-Signed Decisions    │
├─────────────────────────────────────────────────────┤
│                  STRATEGY LAYER                       │
│  Constraint-Guided Autonomy (CGA)                     │
│  Pluggable Strategy Generator (rule-based reference)  │
│  Governed Reroute Loop                                │
├─────────────────────────────────────────────────────┤
│                 EXECUTION FABRIC                      │
│  Graduated Authorization (L0–L4)                      │
│  Human-in-the-Loop Gates · Reconciler                 │
│  Single-Use Decisions · Decision Lineage              │
└─────────────────────────────────────────────────────┘
```

### Constraint-Guided Autonomy (CGA)

The core mechanism. When governance rejects a proposed action:

1. The system receives **structured rejection parameters** — what violated policy and why
2. The Strategy Layer treats these as **constraints, not stop signals**
3. The **strategy generator** replans within the narrowed solution space
4. The new proposal is resubmitted through governance validation
5. The loop iterates until authorization or human escalation

The system hears "no" and figures out how to get to "yes" within the rules.

The generator is an interface (`StrategyGenerator`). The reference
implementation is deterministic and rule-based. The specification's
Proposer/Critic adversarial pair is **not implemented** — there is no proposer
or critic in `gap_kernel/`.

The generator receives a **deep copy** of the world model, so a proposal cannot
launder state into governance evaluation by mutating what the kernel is about to
read.

### The Iron Rule

> Learning modifies strategy weights and skills. **Never governance policy boundaries.** Human authority over constraints is inviolable.

By default a governed deployment runs the Governance Kernel **out of process**
(`isolated=True`). Precisely what that boundary buys, and what it does not:

**What the subprocess boundary enforces.** The kernel's private signing key and
its governance configuration — the signed Tier-1 regulatory floor and the Action
Type Registry — live in a separate OS process. The RPC surface exposed to the
agent is read-only for governance config: `evaluate`, `get_public_key`,
`list_action_types`, `get_action_type`. There is no method to register an action
type or edit the floor; a governed kernel refuses `register_action_type`
outright. The child re-resolves its own trust root from `GAP_TRUST_ROOT` — a path
the *deployer* owns — and verifies the profile signature itself, so a tampered or
substituted profile fails closed; with a trust root in force the key registry the
parent hands down is deliberately empty. A governed kernel ignores a
caller-supplied `current_time` and reads its own clock. Every decision is
Ed25519-signed with a nonce and expiry **inside** the signed payload, and is
single-use: the `ExecutionLedger` claims the nonce before dispatch, at every
authorization level.

**What it does not enforce.** Signature verification happens in the *agent's*
process. The `ExecutionFabric` is constructed there and holds the pinned kernel
key (`_kernel_public_key_hex`) and the unsigned-decisions escape hatch
(`_allow_unsigned_decisions`) as ordinary mutable attributes, alongside the
executor registry that carries tool credentials, the kill switch, the world model
and the lineage store. An adversary with **code execution in the agent process**
therefore bypasses governance without forging anything: overwrite the pinned key,
flip the unsigned-decisions flag, or call an executor directly. The boundary
bounds an attacker who can supply governance *data*. It does not bound one who
already runs code next to the fabric. A topology that would close this — kernel-
side dispatch, or an attested execution enclave — is **not built**.

The in-process embedding modes (`isolated=False`, or supplying your own
`governance_kernel`) are a convenience for embedding and testing, **not** an
isolation boundary — a co-resident agent reaches co-located objects by
reflection.

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for the full attacker model and
[docs/CONFORMANCE.md](docs/CONFORMANCE.md) for what is enforced versus
deployment-configured.

## Key Mechanisms

| Mechanism | What It Does |
|-----------|-------------|
| **Decision Records** | Hash-chained, Ed25519-signed audit trail from policy → authority → reasoning → action → outcome. The primary data object. Signatures verify against the stored canonical bytes under a schema version, and the chain anchor (count + genesis + tip) is itself signed. **Scope:** the chain is signed by the process that owns the database and the anchor lives in the same SQLite file. There is no external witness — a party that controls the store can rewrite history and re-sign it. |
| **Structured Uncertainty** | Every decision documents what was uncertain at decision time: assumptions, watch conditions, known unknowns. |
| **Action Type Registry** | Governance configuration per action category, carried **inside the signed Applicability Profile**. 5 baseline types. A governed kernel refuses runtime registration; an open/prototype kernel treats the call as a monotonic ratchet — a type may be added, never replaced, and never below the floor its own risk profile implies. |
| **Multi-Phase Authorization** | Authorizing intent does not pre-authorize outcome. Independent governance gates at each lifecycle phase. |
| **Single-Use Authorization** | A signed decision authorizes one execution. The `ExecutionLedger` claims its nonce before dispatch at every level (L0–L4), L2+ human approvals are reserved before dispatch in a separate ledger, and per-action completion tracking means a retry resumes rather than repeating a side effect. A governed deployment requires **durable** ledgers by default — in-memory replay protection evaporates on restart. |
| **Signed Evidence Attestation** | Governance-relevant properties (GDPR consent, geography, jurisdiction, local hour) require an Ed25519 signature by a registered issuer binding the entity, the exact values, the window and the issuer key id. The kernel verifies it itself against issuers resolved from the trust root, and compares values as canonical JSON — a signature over `1` does not certify `True`. An unverifiable value is unevaluable and therefore a violation. **This hardens 2 of the 9 evaluators** — the two that read the world model — and what it is worth depends on the deployment: if the issuer private key sits beside the agent, the agent mints its own consent. It proves an issuer *asserted* a fact, not that the fact is true, and there is no revocation, so replay is bounded by a max-age ceiling rather than closed. |
| **The Reconciler** | Continuous state reconciliation against declared intents. Detects drift and acts. Per-entity exception containment (one malformed entity cannot kill the heartbeat), an observable and resettable circuit breaker, and a `last_contacted` field protected from arbitrary writes that would silence drift detection. |
| **Output Artifact Provenance** | 📋 **Planned.** `ArtifactProvenance` (integrity hash, validation evidence, quality uncertainty) is defined as a model and hangs off `LineageRecord`, but no shipped path populates it — it is constructed only in tests. |
| **Separation of Creation and Validation** | 📋 **Planned.** `ArtifactProvenance.validation_independent` is a self-declared boolean defaulting to `False`. Nothing verifies that the validating entity is independent of the producer, and nothing rejects a self-validated artifact. |

### Governance Integrity & Safety

Beyond the static Iron Rule, the reference implementation builds the *dynamic*
controls that catch indirect governance erosion and contain a misbehaving agent.
Each mechanism below is fail-closed **where it runs**; several are library
components a deployment must wire, and several depend on telemetry the
deployment supplies. The scope column says which. Read
**[docs/CONFORMANCE.md](docs/CONFORMANCE.md)** for the per-claim evidence.

| Mechanism | What It Does | Scope today |
|-----------|-------------|-------------|
| **Corrigibility Kill-Switch** | A human-engaged halt that stops both planning and dispatch, fail-closed. CGA cannot route around it: the loop refuses to plan for a halted scope *before* generating a proposal, and re-checks immediately before dispatch. Scoped global or per-entity; propagates to a sub-agent subtree. | Wired on the shipped path — the `ReconcilerLoop` and REST API share one switch across the loop and the fabric. The no-retarget property is covered by adversarial tests, not proved. The engaging actor is a free-text label, not an authenticated identity, and the audit log is in-memory. |
| **Governance Integrity Monitoring (GIM)** | Five rule-based detectors over the decision stream: authorization drift (GIM-1), audit-behavior divergence (GIM-2), threshold-avoidance decomposition (GIM-3), escalation-framing bias (GIM-4), adversarial collapse (GIM-5). | **Two run unattended.** GIM-1 and GIM-3 are fed automatically by the CGA loop, and in governed mode a flagged action is *held and escalated* rather than logged. GIM-4 fires only when the deployment attaches framing metadata to an escalation. **GIM-2 and GIM-5 have no producer anywhere in `gap_kernel/`** — their math and thresholds are built and tested; nothing feeds them. |
| **Independent Authorization Review** | A separate evaluator re-classifies each operation's required authorization level and flags where the system under-classified (e.g. system L0 vs. independent L4). | Independent of the kernel's registry in *logic* only. The shipped classifier scores an action by risk score plus a keyword scan of the action type and stringified parameters — **renaming an action evades it**. It is not wired into any shipped path; a deployment must construct and call it. A model-backed classifier is the intended adapter and is not implemented. |
| **Self-Evolution Monitor** | Flags capability gain accumulated through self-modification — burst velocity, upward privilege accrual, capability-surface expansion — counting only *realized* (executed) modifications. | Wired and consequential: under `block_on_integrity`, an agent already in a flagged pattern has its next self-modification held. Dormant on the shipped reconciler path unless a self-modifying strategy generator is wired — the default generator never emits `skill_modification`. |
| **Multi-Agent Governance** | Delegation cannot amplify authority (a sub-agent's ceiling is rank-bounded by its parent); constraints and corrigibility propagate down the tree; cross-agent threshold-avoidance decomposition is detected. | `SubAgentExecutor` enforces the bounds at dispatch for any sub-agent routed through it — but **no shipped path routes one**. Wiring an orchestrator through it is deployment-side. The delegation tree is in-memory and single-process. |
| **Structured Intent Resolution (SIR)** | Governs the human→system intent-transfer moment: a five-component intent declaration with confirm/correct, a cryptographic seal, and rule-based meta-intent inference. | Opt-in. The shipped `ReconcilerLoop` and REST API run CGA **without** the SIR gate. `declared_boundaries` — what the system says it will not do — is captured on the declaration and **never enforced**. `verify_seal` has no caller outside tests. Meta-intent inference is advisory, not a gate. |
| **Regulatory Constraint Categories** | Fail-closed structural gates for **all 8** spec categories — data privacy, communications, transparency, anti-discrimination, financial (AML + sanctions), healthcare (minimum-necessary PHI), safety, and IP/content — over a signed Tier-1 regulatory floor. | The floor is Ed25519-signed, verified on load against a trust root the deployer owns, forced HARD, and not writable at runtime. The gates are **structural, not adjudicative**: each checks that the required element is present and declared on the action (a fairness evaluation ran, AML + sanctions screening ran, disclosure occurred), trusting the domain-specific Strategy Layer to populate that metadata truthfully. Thresholds come from a structured field, and a present-but-unparseable value fails closed. |
| **Out-of-Process Kernel (default)** | In a governed deployment the kernel — with its private signing key and its signed governance configuration — runs in a separate OS process by default, behind a read-only RPC surface. An independent trust root and durable replay ledgers are required by default. | The key and the configuration are genuinely out of process, and profile tamper fails closed. **Signature verification is still in the agent's process**, along with the executor registry, kill switch, world model and lineage store — see [The Iron Rule](#the-iron-rule). This bounds a data-supplying attacker, not one with code execution in the agent. |

## Where GAP Sits

GAP is positioned as one layer in the emerging protocol stack for autonomous AI:

```
Intelligence    →  Foundation Models (OpenAI, Anthropic, Google)
Connectivity    →  MCP (Model Context Protocol)
Agency          →  Agent Frameworks (LangChain, CrewAI, AutoGen)
Governance      →  GAP (General Autonomy Protocol)  ← this project
Compliance      →  NIST AI RMF, ISO 42001, EU AI Act
```

**MCP connects. GAP governs.** MCP defines how agents connect to tools. GAP
defines whether agents are authorized to use those tools, under what conditions,
with what accountability. GAP does not integrate with MCP today; the two are
placed on the same diagram as an argument about layering, not a shipped bridge.

## Principles

These are the design principles the specification is written to.
[docs/CONFORMANCE.md](docs/CONFORMANCE.md) records which are enforced in code
today.

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

# Install with the test dependencies
pip install -e ".[dev]"

# Run the test suite
pytest tests/ -v
```

CI runs these exact three commands on every push in a dedicated `quickstart`
job, so a broken install path fails the build rather than the reader.

The kernel core depends only on `pydantic`, `croniter`, and `cryptography`. The
REST surface is an optional extra (`pip install -e ".[api]"`), and its mutating
routes are **off by default** — pass `create_app(enable_mutating_routes=True)`
to register them.

A governed deployment (`build_governed_deployment`) requires three things and
refuses to start without them: a signed Applicability Profile, an independent
trust root at the path named by `GAP_TRUST_ROOT`, and a `ledger_dir` for durable
replay ledgers. Each has one explicitly named prototype escape hatch.

## Project Structure

```
General-Autonomy-Protocol/
├── gap_kernel/                  # Core protocol implementation
│   ├── api/app.py               # Optional FastAPI surface; mutating routes off by default
│   ├── client/                  # SubprocessGovernanceClient — the agent-side handle on an out-of-process kernel
│   ├── crypto/signing.py        # Ed25519 sign/verify + public-key registry
│   ├── execution/               # Execution Fabric (dispatch, signature verification, OOB gate) + SubAgentExecutor
│   ├── governance/              # Kernel, Applicability Profile, GIM, SIR, corrigibility, multi-agent, deployment factory
│   ├── learning/                # Learning engine — operational heuristics; policy changes are proposals only
│   ├── lineage/                 # Decision Lineage store — hash chain + Ed25519 signatures over SQLite
│   ├── models/                  # Pydantic data models — Decision Records, Uncertainty, Provenance, SIR
│   ├── reconciler/              # Reconciler loop — drift detection, escalation queue, circuit breaker
│   ├── service/kernel_server.py # The out-of-process kernel: read-only RPC over stdio, trust-root resolution
│   ├── strategy/cga_loop.py     # Strategy Layer — CGA loop + rule-based strategy generator
│   ├── verification/            # Execution and OOB replay ledgers — single-use decisions
│   ├── world_model/             # World Model store + signed evidence attestation
│   ├── _time.py                 # Timezone-aware UTC helpers
│   ├── errors.py
│   └── __init__.py
├── docs/                        # Specification, Conformance Statement, remediation plan, design notes, audits
├── action-types/                # Action type definitions in Markdown
├── tests/                       # 639 tests incl. property-based fuzzing; CI fails under 90% coverage
├── .github/workflows/ci.yml     # Matrix 3.11–3.13 + Windows, coverage floor, README quickstart, ruff
├── pyproject.toml               # Project metadata and dependencies
├── LICENSE                      # Apache 2.0
├── README.md                    # ← you are here
├── CONTRIBUTING.md              # Contribution guidelines
├── CODE_OF_CONDUCT.md           # Community standards
└── SECURITY.md                  # Security policy and vulnerability reporting
```

## Regulatory Alignment

GAP is **designed against** the following frameworks. It has not been assessed
against any of them, and no conformity claim is made here.

- **EU AI Act** — Decision Records, graduated authorization (L0–L4) and
  Structured Uncertainty are built to serve the transparency and human-oversight
  obligations. Whether they satisfy them is a legal determination this project
  has not sought.
- **NIST AI RMF** — GAP's four continuous functions were structured to map onto
  Govern / Map / Measure / Manage.
- **ISO 42001** — GAP is intended as the technical enforcement layer beneath an
  AI management system, not as the management system.
- **SOC 2** — Decision Lineage produces audit-evidence artifacts. See the
  Decision Records row above for the honest limits on their integrity: no
  external witness.
- **OWASP Agentic AI Top 10** — several entries have a corresponding structural
  gate in the kernel. No mapping exercise has been published, so no count is
  claimed.

The regulatory constraint gates check that a declared element is present. They
do not adjudicate the underlying legal question.

## RGAP: For Existing Systems

The **Retro General Autonomy Protocol (RGAP)** is the specification's answer for
agentic flows already built on LangChain, CrewAI or AutoGen: intercept at the
action execution point and create a governance negotiation loop inside the
existing framework. RGAP is **specified, not implemented** — there is no RGAP
code and no framework adapter in this repository. See
[docs/PROTOCOL_SPECIFICATION.md](docs/PROTOCOL_SPECIFICATION.md).

## Contributing

GAP is an open protocol. We welcome contributions from the community — whether
you're building governed autonomous systems, researching AI safety, or working
on compliance infrastructure. Adversarial review is the most useful thing you
can bring: the safety claims above are only as good as the attempts made to
break them.

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
  <strong>General Autonomy is the work of making them trustworthy.</strong>
</p>

<p align="center">
  <em>The GAP is real. We close it.</em>
</p>

<p align="center">
  <a href="https://nexeom.ca">Nexeom</a> · Bancroft, Ontario, Canada
</p>
