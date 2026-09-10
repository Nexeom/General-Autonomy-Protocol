# General Autonomy Protocol (GAP)

**An experimental runtime authorization and audit layer for autonomous agents, with policy-constrained replanning.**

[Specification](docs/PROTOCOL_SPECIFICATION.md) · [Conformance](docs/CONFORMANCE.md) · [Threat model](docs/THREAT_MODEL.md) · [Review guide](docs/REVIEW_GUIDE.md) · [Website](https://nexeom.ca/gap)

[![CI](https://github.com/Nexeom/General-Autonomy-Protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/Nexeom/General-Autonomy-Protocol/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## What it does

GAP evaluates a proposed action against a configured policy and authorization
level before dispatch. It signs the decision, checks required human approval,
tracks authorization use, and records the proposal, decision and outcome. A
rejection can return structured constraints to a strategy generator for another
proposal; each new proposal must pass governance again.

For example, the reference CRM strategy proposes outreach. If the configured
consent rule rejects it, the strategy tries another permitted workflow or routes
the case to a human. This is a small, deterministic demonstration of the control
flow. Human handoff does not itself make outreach lawful, and the example does
not establish general-purpose planning ability.

The repository contains an **open protocol proposal** and a Python reference
implementation. The proposal defines requirements; the implementation meets a
documented subset. GAP is not an established industry standard or a certification.

## Maturity

- Package version: **`0.3.0a1`**, an alpha. See
  [GitHub Releases](https://github.com/Nexeom/General-Autonomy-Protocol/releases)
  for versioned tags and artifacts, including the prior `v0.2.0-alpha` release.
  There is no published adoption report or external security review. Current
  review is maintainer-led.
- CI is configured for Python 3.11–3.13 on Linux and 3.13 on Windows, with a 90%
  line-coverage floor, regression/property tests, Ruff, and an install check.
  Type checking is advisory. Test totals and coverage should be read from the
  run for the commit under review, rather than treated as security guarantees.
- [Recorded CI validation](https://github.com/Nexeom/General-Autonomy-Protocol/actions/runs/34496186894)
  passed 746 tests with 92.82% coverage on Python 3.11, 3.12 and 3.13, the ten
  demo checks, and reproducible-build checks on Linux. The Windows/Python 3.13
  job also passed. The run identifies the tested revision and environments.
- [CONFORMANCE.md](docs/CONFORMANCE.md) records enforcement, wiring and deployment
  limits. [KNOWN_GAPS.md](docs/KNOWN_GAPS.md) records unfinished work. These take
  precedence over a summary here; passing tests do not erase their limitations.
- This branch includes a two-tool gateway, an optional LangGraph adapter and a
  runnable governed demo. The local functional demo passes its ten scripted
  checks; it uses the same OS user and a scripted approval fixture. A separate
  [26-scenario evaluation](docs/EVALUATION.md) reports completion, blocking,
  escalation and latency with explicit denominators. The
  [Linux Docker boundary run](https://github.com/Nexeom/General-Autonomy-Protocol/actions/runs/34496186876)
  passed all eight checks for the documented topology, including denied direct
  access to the live sink's exact IP and unavailable private directories.
  This is project-run automated validation, not an external security review.
  The [review guide](docs/REVIEW_GUIDE.md) separates these kinds of evidence.

## Getting started

Use a Python 3.11+ virtual environment. The commands below select the versioned
alpha after cloning; if you already have its checkout, start at the install
command. Record the commit with the review results.

```bash
git clone https://github.com/Nexeom/General-Autonomy-Protocol.git
cd General-Autonomy-Protocol
git checkout v0.3.0a1
python -m pip install -e ".[dev]"
python examples/governed_demo.py
python examples/evaluate.py --output evaluation-results/local.json
python -m pytest tests/ -q --cov=gap_kernel --cov-report=term-missing --cov-fail-under=90
python -m ruff check gap_kernel/ tests/
```

The demo starts local HTTP services with temporary identities and a harmless
outbox. It exercises permitted dispatch, rejection, an approval gate, tamper and
replay rejection, a real LangGraph workflow and signed lineage. Its approval is
scripted test data, not a human decision; it needs no model API or external
account. [GATEWAY.md](docs/GATEWAY.md) gives the separate interactive operator
approval flow and explains the deployment boundary. The
[review guide](docs/REVIEW_GUIDE.md) gives focused checks and a result template.
The evaluation uses deterministic fixtures, records its source revision/hash
and environment, and reports failures as well as aggregate metrics. Its rates
are not estimates of model safety or production reliability.

The core dependencies are `pydantic`, `croniter` and `cryptography`. The REST
surface is optional (`python -m pip install -e ".[api]"`); mutating routes are
off by default. Enabling them requires a deployment-owned authenticated control
plane.

`build_governed_deployment` requires a signed Applicability Profile, an
independent trust root at `GAP_TRUST_ROOT`, and a durable `ledger_dir`. Signed
world-model evidence also requires registered issuers in that trust root. The
issuer and policy-signing private keys must remain outside the agent's trust
domain. Explicit prototype escape hatches weaken this posture; an open-mode
constructor is not equivalent to a governed deployment.

## Implemented behavior and limits

| Capability | Current scope |
|---|---|
| Policy evaluation | Signed applicability profile, registered action types, authorization floors, structured rejection, and fail-closed handling for supported constraint evaluation. Policy authors are responsible for the policy's meaning and completeness. |
| Signed decisions and approval | Ed25519 signatures bind the decision to its proposal, nonce and expiry. L2+ execution requires separately signed approval. Durable ledgers track use and completed actions; external tools still need explicit failure and idempotency semantics. |
| Signed evidence | The consent and contact-hour evaluators verify issuer signatures over entity identity, exact property values and validity window. This covers the two world-model evaluators, not every domain evaluator. A signature proves an issuer made an assertion; it does not prove the assertion true. Revocation is not implemented, so old evidence can remain acceptable within its freshness ceiling. |
| Replanning | A pluggable `StrategyGenerator` receives structured constraints and a copy of world state. The reference generator chooses among three CRM rules. There is no implemented Proposer/Critic pair or demonstrated general LLM replanning. |
| Audit evidence | Decision lineage is hash-chained and signed. Stored canonical bytes support signature verification. There is no external witness: a party controlling both the lineage store and its signing key can rewrite and re-sign history. |
| Human halt and reconciliation | A shared kill switch gates planning and dispatch; the reconciler contains per-entity failures and tracks drift/escalations. The embedded switch uses a free-text actor label and an in-memory audit log. |
| Behavioral monitoring | Authorization drift and repeated sub-threshold activity are fed by the CGA loop. Other detectors require telemetry that the default path does not provide. Heuristic alerts do not establish semantic understanding of harmful behavior. |

Additional components, including sub-agent authorization, intent resolution,
self-modification monitoring and an independent risk classifier, have narrower
integration or enforcement limits. Output-artifact provenance and independent
output validation remain planned. Consult the
[complete capability matrix](docs/CONFORMANCE.md) before relying on them.

The [reference gateway](docs/GATEWAY.md) owns its policy, evidence, tool catalog,
credentials and dispatch. Its agent API accepts only allowlisted tool requests
and signed approval artifacts. A deployment-owned halt file blocks new work and
is checked before each dispatch. This is a narrow reference integration; it does
not automatically expose every embedded-runtime feature through the gateway.

## Architecture and trust boundary

```text
Strategy generator
    │ proposed action + declared intent + evidence
    ▼
Governance kernel ── signed decision or structured rejection
    │ approved proposal + required human approval
    ▼
Execution enforcement ── tool dispatch ── outcome / decision lineage
```

The governed factory runs the kernel in a subprocess by default. Its signing
key and policy configuration are separated from ordinary agent-side data, the
kernel resolves its trust root, and the governance RPC surface cannot change
policy or register action types.

**The existing embedded execution path is not a containment boundary against
agent-side code execution.** Its `ExecutionFabric`, verification key, executor
registry, kill switch and lineage store reside in the agent process. Code in
that process can alter the verifier or call an executor directly. A separate
process under the same OS user also does not, by itself, make key files
unreadable to that user.

The gateway moves verification and dispatch out of the agent process. Its
boundary becomes meaningful when the deployment also denies agent access to
gateway/operator secrets and direct tool access. The same-user local demo
verifies functional behavior; it does not demonstrate that isolation. See the
[gateway deployment guide](docs/GATEWAY.md) for the role and network separation
and its current verification status.

Enforcement depends on the agent having no alternative route to the controlled
tool or its credentials. The [threat model](docs/THREAT_MODEL.md) identifies the
attacker capabilities and deployment controls. The design objective called the
**Iron Rule** is that learning may change strategy without changing the policy
boundary; the current guarantee is limited by those deployment assumptions.

## Where GAP fits in AI governance

GAP is a **runtime policy-enforcement and evidence component** within a larger
governance program. Organizations define responsibility, approved uses, risk
acceptance, policy, oversight and incident response. GAP can apply an encoded
subset of those decisions at an agent's action boundary and produce records for
review.

| Concern | GAP's contribution | Work outside GAP |
|---|---|---|
| Authority over tools | Evaluate configured action policies; request approval; gate dispatch on the supported path. | Identity administration, credential custody, network isolation and complete coverage of tool access. |
| Accountability | Record proposals, authorization, evidence references and outcomes. | Independent retention/witnessing, investigation, responsible owners and recourse. |
| Risk and compliance | Enforce configured checks and preserve evidence useful to a review. | Legal interpretation, impact/risk assessment, model evaluation, monitoring adequacy and assessment of the complete deployed system. |

The project is intended to support parts of NIST AI RMF, ISO/IEC 42001 and
regulatory governance programs. It has not been assessed against them and makes
no compliance or certification claim. Most bundled regulatory evaluators check
declared structure, such as whether screening was reported; they do not verify
the screening result or adjudicate legal compliance.

GAP complements agent orchestration, policy engines, identity controls and audit
systems. Its proposed combination is policy-bound replanning with signed
authorization and decision records. It does not claim to have invented those
individual mechanisms. There is no MCP integration in the current reference
implementation. A framework adapter must ensure the controlled tool path cannot
be bypassed; wrapping one function does not govern an entire agent.

## What is General Autonomy?

“General Autonomy” is this project's name for the design question: how can an
agent pursue a goal while remaining within human-defined authority? GAP
explores one answer: reject unauthorized proposals, permit bounded replanning,
escalate unresolved cases, and retain a reviewable decision record. This is a
design thesis, not evidence that governance is solved or that other approaches
are absent.

The architecture uses common decision and action models with domain-specific
profiles and evaluators. The bundled kernel includes consent and contact-hour
logic; domain independence is an extensibility goal, not a claim that all domain
knowledge sits outside the kernel.

## Project map

- [`gap_kernel/`](gap_kernel/): policy evaluation, execution, evidence, lineage,
  reconciliation and strategy interfaces.
- [`tests/`](tests/): unit, integration, adversarial and property-based checks.
- [`docs/PROTOCOL_SPECIFICATION.md`](docs/PROTOCOL_SPECIFICATION.md): proposed
  normative requirements, including capabilities not yet implemented.
- [`docs/REVIEW_GUIDE.md`](docs/REVIEW_GUIDE.md): reproducible review and evidence
  checklist for the maintainer or a future independent reviewer.
- [`docs/GATEWAY.md`](docs/GATEWAY.md): gateway trust model, operator approval and
  deployment instructions.
- [`docs/EVALUATION.md`](docs/EVALUATION.md): evaluation cases, metric definitions
  and how to interpret the result artifact.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): evidence-gated development priorities.
- [`action-types/`](action-types/): proposed domain action specifications;
  publication does not imply an implementation exists.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance and
[SECURITY.md](SECURITY.md) for security reporting. Preparing the review package
does not mean an independent review has happened or been commissioned.

The specification, reference implementation and documentation are licensed
under [Apache-2.0](LICENSE).
