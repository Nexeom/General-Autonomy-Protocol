# GAP roadmap and positioning

This document states development priorities for an **open protocol proposal**.
It is non-normative. Proposed requirements live in
[PROTOCOL_SPECIFICATION.md](PROTOCOL_SPECIFICATION.md); implemented guarantees
and their limits live in [CONFORMANCE.md](CONFORMANCE.md).

## Current evidence

The project has an alpha Python reference implementation, a test suite and
maintainer-led reviews. The prior [v0.2.0-alpha release](https://github.com/Nexeom/General-Autonomy-Protocol/releases/tag/v0.2.0-alpha)
is tagged; the current `0.3.0a1` candidate is not yet tagged. There is no published
adoption report or external security review. This roadmap is not evidence of commercial traction,
certification, third-party approval or a production deployment.

A two-tool gateway, optional LangGraph adapter and runnable governed demo are
implemented for the `0.3.0a1` alpha candidate. The local demo passes ten functional checks using
the same OS user and a scripted approver; it does not demonstrate deployment
isolation. The [evaluation harness](EVALUATION.md) covers 26 deterministic
scenarios and records revision, environment and denominators. Live container
validation remains unrun locally because Docker/WSL is unavailable. Code
presence alone does not satisfy the milestones below. The current reviewer is
the maintainer. No outside review request is implied by preparation of the
[review package](REVIEW_GUIDE.md).

## Development milestones

Progress is gated by evidence rather than promised calendar dates.

| Milestone | Deliverable | Evidence needed before marking complete |
|---|---|---|
| 1. Reproducible alpha | Documented install, governed demo, threat model, capability matrix and review guide. | A clean checkout runs the documented commands; observed outputs identify the exact revision and configuration; known failures remain visible. |
| 2. Execution boundary | Tool dispatch and credentials outside the agent process, with an example of deployment isolation. | Tests exercise forged/tampered/expired decisions, proposal substitution, concurrent replay, unauthorized direct tool access, halt and restart behavior. A deployed topology demonstrates that the agent cannot reach tool credentials or the protected tool directly. |
| 3. One framework integration | A small optional LangGraph adapter using the governed action path. | A runnable workflow shows rejection, bounded replanning, approval and dispatch. A second integration path cannot silently bypass the gate. Framework compatibility is specified and tested. |
| 4. Measured behavior | Reproducible evaluation cases and machine-readable results. | Report allowed-action completion, forbidden-action blocking, false blocks, replanning/escalation outcomes and latency, with scenario denominators and failure details. Separate deterministic fixture results from model-backed evaluations. |
| 5. Versioned release | Tagged alpha artifact, changelog and compatibility/migration notes. | Installation and tests from the distributed artifact, documented schema/protocol versions, and an explicit scope of supported deployments. A version tag is not a security certification. |
| 6. Independent assessment | Review and implementation reports by people other than the maintainer, if and when arranged. | Published scope, reviewed revision, methods, findings, unresolved issues and responses. Until a review happens, this remains unmet. |

## Subsequent work

Priorities after the first complete integration should follow observed failures
and deployer needs. Candidates include external audit anchoring, evidence
revocation, authenticated durable operator controls, stronger domain evidence,
additional adapters and distributed delegation. The present heuristic monitors,
intent-resolution boundaries and unpopulated provenance fields also need the
work recorded in [KNOWN_GAPS.md](KNOWN_GAPS.md).

Model-backed strategy generation and evaluation need separate evidence. A
deterministic workflow is useful for reproducibility but does not measure
robustness to model variation, paraphrasing or adversarial prompts. Expansion to
more domains should follow a demonstrated integration in one domain.

## Positioning and commercial context

GAP contributes runtime authorization, policy-constrained replanning and signed
decision evidence to an organization's AI governance program. It complements
policy engines, identity systems, agent frameworks and audit infrastructure.
The project does not claim that existing tools only keep chat logs, that the
governance category was previously absent, or that GAP alone satisfies a
regulatory framework.

Managed adapters, deployment support and a hosted platform are possible future
services. There is no GAP certification program, published procurement standard
or proprietary Constraint Library in this repository. Earlier roadmap language
described those as commercial ambitions; they are not delivered assets or
restrictions on use of the code.

The published specification, reference implementation, evaluators and
documentation remain Apache-2.0. Any future service must state its own terms
without implying that the open proposal or its current code has become
proprietary.
