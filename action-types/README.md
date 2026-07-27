# GAP Action Type Specifications

This directory contains Action Type specifications for domain-specific governance configurations under the General Autonomy Protocol.

Action Types extend GAP's governance to specific domains without modifying the core protocol. Each specification defines the governance behavior, risk profiles, authorization requirements, and Decision Record extensions for a category of autonomous action.

## Specifications

| Document ID | Action Type | Spec status | Implementation status | Description |
|---|---|---|---|---|
| [GAP-AT-FIN-001](financial_transaction.md) | `financial_transaction` | Draft | **Not implemented** | Governance for autonomous financial operations |

**What "Not implemented" means here.** These are published specifications. An
Action Type specification describes governance a conformant deployment must
provide; it does not imply that the GAP reference kernel provides it.
`GAP-AT-FIN-001` currently has **no reference implementation at all** — searching
`gap_kernel/` for `financial_transaction`, `SpendGate`, or `GAP-AT-FIN` returns
nothing. The baseline Action Type Registry ships five types
(`task_execution`, `skill_modification`, `drift_reconciliation`, `escalation`,
`policy_proposal`); `financial_transaction` is not among them, and a deployment
that wants it must register it through a signed Applicability Profile and build
the SpendGate enforcement itself.

## Contributing

To propose a new Action Type specification, see [CONTRIBUTING.md](../CONTRIBUTING.md).
