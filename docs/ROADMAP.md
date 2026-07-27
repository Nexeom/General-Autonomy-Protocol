# GAP Roadmap, Business Model & Positioning

> **Non-normative.** Nothing in this document is a protocol requirement. It
> states intent and commercial context for the project, not conformance
> obligations. The normative standard is
> [`PROTOCOL_SPECIFICATION.md`](PROTOCOL_SPECIFICATION.md); what the reference
> implementation actually enforces is
> [`CONFORMANCE.md`](CONFORMANCE.md).
>
> These three sections were previously embedded in the protocol specification
> (as §8 Strategic Roadmap, §9 Business Model, §10 Competitive Position). They
> are reproduced here unchanged. They were moved because a document that
> organizations are asked to implement against should not also carry the
> author's commercial plan — and because §9 designates an asset "Proprietary
> Data" inside a document the project calls an open standard.

## Status of this roadmap

None of the phases below has shipped. As of this revision:

- There is **no tagged release**. The package version is `0.1.0-alpha`.
- There are **no known adopters** and no published implementation reports. The
  "early adopters" in Phase 1 are a plan, not a population.
- **No RGAP adapter exists** — for LangGraph or any other framework. RGAP is
  specified (see the specification's RGAP section) and unimplemented.
- The repository has **one author** and has had **no external security review**.
  The audits in `docs/` are self-run; see their metadata blocks.
- The **Constraint Library** named in the Business Model table below does not
  exist in this repository. Everything published here — the protocol
  specification and the reference kernel, including its constraint evaluators —
  is Apache-2.0.

Read the roadmap as a statement of direction. It is not evidence of traction.

---

## Strategic Roadmap

**Phase 1: Protocol Establishment (Now)**
- Ship GAP as an open-source protocol standard
- Publish the General Autonomy manifesto to establish the category
- Position Nexeom as the first GAP-native Decision Intelligence platform
- Begin capturing Decision Lineage data from early adopters

**Phase 2: Commercial Bridge (6–12 Months)**
- Launch RGAP as a commercial managed service for existing agentic frameworks
- Open-source one reference RGAP adapter (LangGraph) as proof of concept
- Build production-grade RGAP adapters for LangChain, CrewAI, AutoGen
- Build Decision Forecasting on top of accumulated lineage data

**Phase 3: Ecosystem Expansion (12–24 Months)**
- Federated GAP: cross-organization autonomous systems negotiating through shared protocol
- GAP certification standard for enterprise procurement ("Is your system GAP-compliant?")
- Integration as runtime governance layer for NIST, ISO, EU AI Act compliance
- Decision Accountability Score — the defining metric for the General Autonomy category

---

## Business Model

| Asset | Model | Revenue Mechanism |
|---|---|---|
| **GAP Core** | Open-Source | Builds adoption moat and protocol standard. Free forever. |
| **RGAP Adapters** | Commercial SaaS | Managed integration service. Bridge revenue while market migrates to GAP-native. |
| **Nexeom Platform** | Enterprise SaaS | Full GAP-native Decision Intelligence platform. Executive dashboards, audit infrastructure. |
| **Certification** | Consulting + Tooling | GAP compliance certification, audit tooling, enterprise consulting. |
| **Constraint Library** | Proprietary Data | Denial-and-constraint prompt corpus compounds with scale. Proprietary intelligence. |

**Scope note.** Only the first row describes something that exists. The
"Constraint Library" row describes a hypothetical future asset and has no
bearing on the license of anything in this repository: the protocol
specification, the reference kernel, and its constraint evaluators are
Apache-2.0 and carry no proprietary reservation.

---

## Competitive Position

GAP does not compete with LLM providers (OpenAI, Anthropic, Google) on intelligence, nor with agent frameworks (LangChain, CrewAI, AutoGen) on agency. GAP defines the third layer — the governance infrastructure that makes everything they built deployable in institutional contexts. The differentiator is structural: competitors have chat logs. GAP systems have Decision Records.

The positioning against existing governance and compliance tools: NIST, ISO, and EU AI Act tell organizations *what* to govern. GAP tells them *how* to govern it in real-time, autonomously.

---

*General Autonomy Protocol · Nexeom · 2026*
