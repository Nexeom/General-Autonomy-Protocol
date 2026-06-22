# GAP Action Type Specification: financial_transaction

**Document ID:** GAP-AT-FIN-001
**Version:** 1.0
**Date:** 2026-02-23
**Status:** Draft — Open for Community Review

---

## 1. Context

AI agents are acquiring the ability to hold funds, execute payments, and trade assets autonomously. Machine-to-machine payment protocols, agentic wallet infrastructure, and programmable financial tooling have moved from concept to production in early 2026. This creates a new class of autonomous action — financial execution — that existing governance frameworks do not address.

The infrastructure for agents to spend is shipping. The infrastructure for agents to spend safely, accountably, and within governed authority boundaries does not yet exist.

GAP's Action Type Registry provides the mechanism to define governance configurations for distinct categories of autonomous action. This specification defines the financial_transaction Action Type — the governance primitive for autonomous financial operations under the General Autonomy Protocol.

---

## 2. Action Type Definition

### 2.1 Registry Entry

| Property | Value |
|---|---|
| action_type | financial_transaction |
| Category | External · Financial · Irreversible |
| Default Authorization Level | L2 (Approve Before Execution) |
| Minimum Authorization Level | L1 (Notify) — only for pre-approved micro-transactions |
| Maximum Authorization Level | L4 (Human Only) — configurable per organization |
| Iron Rule Impact | The governed agent cannot modify financial governance thresholds. Structural enforcement applies. |
| Reversibility | Non-reversible. On-chain and wire transactions are final. |
| Decision Record | Mandatory. Extended with financial provenance fields. |
| Multi-Phase | Yes. Intent gate (authorize plan) → Outcome gate (confirm execution). |

### 2.2 Subtypes

The financial_transaction type contains six registered subtypes. Each carries its own default governance configuration. Implementations may define additional subtypes by registering them with the Governance Kernel.

| Subtype | Default Auth | Risk Profile | Description |
|---|---|---|---|
| payment_send | L2 | High — funds leave custody | Outbound payment to external address or endpoint |
| payment_receive | L1 | Low — inbound only | Accept incoming payment |
| trade_execute | L2 | High — asset conversion | Swap one asset for another |
| yield_manage | L2 | Medium — position management | Deposit, withdraw, or rebalance yield positions |
| budget_allocate | L1 | Low — internal reallocation | Move budget between internal accounts or agent capabilities |
| api_micropayment | L0* | Routine — pre-approved ceiling | Per-call API fee or compute payment |

*L0 eligibility for api_micropayment requires all of: active SpendGate session, individual transaction below the configured micro-threshold, cumulative session spend below session ceiling, and the target endpoint on the approved vendor list. Violation of any condition escalates to L2.

---

## 3. SpendGate: Financial Governance Mechanism

The SpendGate is the governance mechanism specific to financial_transaction actions. It operates within the standard GAP evaluation pipeline, adding financial constraint evaluation before the Constraint-Guided Autonomy (CGA) loop.

### 3.1 Constraint Stack

Every financial_transaction passes through five constraint layers in sequence. Failure at any layer triggers the CGA loop with structured rejection parameters — except where noted.

| # | Constraint | Evaluation | On Failure |
|---|---|---|---|
| 1 | Transaction Ceiling | Amount ≤ per-transaction maximum | CGA: reduce amount or split transaction |
| 2 | Session Budget | Cumulative session spend + amount ≤ session ceiling | CGA: defer, reduce, or escalate for budget increase |
| 3 | Frequency Cap | Transaction count within time window ≤ rate limit | CGA: batch transactions, delay, or escalate |
| 4 | Vendor Whitelist | Target address/endpoint on approved list | Block. No CGA. Human approval required. |
| 5 | Risk Classification | Transaction risk score ≤ configured threshold | Escalate to authorization level matching risk tier |

The Vendor Whitelist constraint is the only hard block in the stack. An agent cannot use the CGA loop to find a path to an unapproved counterparty. This is a structural enforcement boundary — the agent lacks the authority to approve new vendors, and no amount of compliant replanning can change that. Only a human policy-setter (L4) can modify the approved vendor list.

### 3.2 Configuration Schema

SpendGate configuration is set exclusively by the human policy-setter. Modification requires L4 (Human Only) authorization. The governed agent cannot read, access, or reason about SpendGate thresholds — consistent with the Iron Rule and structural enforcement.

```yaml
SpendGateConfig:
    # Per-transaction limits
    max_transaction_amount: Decimal        # Hard ceiling per transaction
    micro_threshold: Decimal               # Below this = L0 eligible
    elevated_threshold: Decimal            # Above this = L3 required
    critical_threshold: Decimal            # Above this = L4 (human only)

    # Session limits
    session_budget: Decimal                # Max cumulative per session
    session_duration_hours: int            # Session auto-expires
    daily_budget: Decimal                  # Rolling 24-hour ceiling
    monthly_budget: Decimal                # Rolling 30-day ceiling

    # Rate limits
    max_transactions_per_hour: int
    max_transactions_per_day: int

    # Vendor governance
    approved_vendors: List[VendorEntry]    # Whitelist (address + label)
    blocked_vendors: List[str]             # Blacklist (takes precedence)
    allow_unknown_vendors: bool = False    # If False, unlisted = Block

    # Risk parameters
    risk_model: str = "conservative"       # conservative | balanced | permissive
    require_adversarial_review: bool       # Critic evaluates before execution
```

---

## 4. CGA Loop Behavior for Financial Actions

When the SpendGate rejects a financial_transaction, the standard Constraint-Guided Autonomy loop activates. The Proposer receives structured rejection parameters and must replan within the narrowed solution space. The Critic evaluates every revised proposal.

### 4.1 Rejection-Replan Patterns

Financial CGA has specific replan strategies that differ from general action replanning:

| Rejection Reason | Replan Strategy | Constraint Injection |
|---|---|---|
| Amount exceeds ceiling | Split into multiple transactions below ceiling, or reduce scope | max_allowed_amount, remaining_budget |
| Session budget exhausted | Defer to next session, request budget increase (escalates to human), or find lower-cost alternative | remaining_session_budget, session_expiry |
| Rate limit exceeded | Batch pending transactions, schedule for next available window | next_available_slot, batch_eligible_transactions |
| Unknown vendor | No CGA. Escalate to human for vendor approval. | vendor_address, vendor_context, approval_required |
| Risk score too high | Decompose into lower-risk components, or escalate with full risk analysis | risk_score, risk_factors, threshold, mitigation_options |

### 4.2 Loop Bounds

Financial CGA loops are bounded more tightly than standard CGA to prevent runaway replan cycles that could result in unintended spend patterns:

- Maximum **3 replan attempts** per financial action (recommended default; standard actions typically allow 5).
- **Mandatory Critic review** on every replan iteration, not just the final proposal. Financial replanning must be adversarially validated at each step.
- **Escalation on loop exhaustion:** If 3 replans fail to produce a governed path, the action escalates to human with full negotiation lineage attached.
- **No silent degradation:** The agent cannot silently substitute a cheaper alternative without governance approval. Every replan is a new proposal evaluated against the full constraint stack.

---

## 5. Decision Record Extension

Financial transactions extend the standard Decision Record with provenance fields specific to monetary operations.

### 5.1 Financial Provenance

```yaml
FinancialProvenance:
    # Transaction identity
    transaction_hash: Optional[str]       # Settlement reference (post-execution)
    protocol: str                         # Payment protocol identifier
    network: str                          # Settlement network identifier

    # Financial details
    amount: Decimal
    currency: str                         # Asset identifier
    amount_usd_equivalent: Decimal        # At time of decision
    direction: str                        # "outbound" | "inbound" | "swap"

    # Counterparty
    vendor_address: str
    vendor_label: Optional[str]           # Human-readable if on whitelist
    vendor_risk_score: Optional[float]

    # SpendGate evaluation snapshot
    session_spend_before: Decimal         # Cumulative before this transaction
    session_spend_after: Decimal          # Cumulative after this transaction
    daily_spend_before: Decimal
    budget_remaining_after: Decimal
    spendgate_constraints_evaluated: dict # Full constraint stack results

    # CGA negotiation (if rejection occurred)
    cga_iterations: int = 0
    original_proposal: Optional[dict]     # What was first proposed
    final_proposal: Optional[dict]        # What was authorized
    rejection_reasons: List[str] = []
```

### 5.2 Dual Audit Trail

For transactions settled on an immutable ledger, the financial_transaction Action Type creates a dual audit trail:

- **The Decision Record** captures the governance reasoning: why this transaction was authorized, what constraints were evaluated, what the agent proposed, what was rejected, what was finally approved, and what was uncertain at the time.
- **The settlement ledger** captures the execution: that the transaction occurred, when, for how much, between which parties.

Neither alone is sufficient for institutional accountability. The settlement ledger proves execution but cannot explain authorization. The Decision Record proves authorization but cannot prove execution. Together, they provide complete decision-to-outcome traceability for autonomous financial action.

**Optional enhancement:** Decision Record hashes can be anchored alongside transaction execution on the settlement ledger, creating cryptographic proof that governance evaluation preceded and authorized every financial action.

---

## 6. Multi-Phase Authorization

Financial transactions use multi-phase authorization. This is non-optional for all financial_transaction subtypes except api_micropayment at L0.

### 6.1 Phase Sequence

| Phase | What Happens | GAP Evaluation |
|---|---|---|
| 1. Intent Gate | Agent proposes financial action with full context: amount, vendor, purpose, budget impact. | SpendGate constraint stack + adversarial review. Authorization = permission to attempt. |
| 2. Execution | Agent executes via payment skill. Transaction submitted to settlement network. | No governance gate. Execution proceeds on intent authorization. |
| 3. Outcome Gate | Agent reports execution result: settlement reference, actual amount, confirmation status, any discrepancies. | Reconciler validates outcome matches authorized intent. Discrepancy triggers drift event. |

### 6.2 Discrepancy Handling

If the outcome does not match the authorized intent (e.g., slippage on a trade, unexpected fee, partial execution), the Reconciler generates a drift event with financial context:

- **Minor discrepancy** (within configured tolerance): Log, update session tracking, continue.
- **Material discrepancy** (exceeds tolerance): Escalate to human. Pause further financial actions from this agent until reviewed.
- **Critical discrepancy** (unauthorized recipient, wrong asset, failed execution with partial spend): Emergency halt. All financial capabilities suspended. Human-only recovery.

Tolerance thresholds are part of the SpendGate configuration, set by the human policy-setter.

---

## 7. Risk Classification Matrix

| Subtype | Sandbox (Restricted) | Standard | Full |
|---|---|---|---|
| api_micropayment | Confirm | L0 (within SpendGate) | L0 (within SpendGate) |
| payment_receive | Proceed | L1 (Notify) | L1 (Notify) |
| budget_allocate | Confirm | L1 (Notify) | L1 (Notify) |
| payment_send | Block | L2 (Confirm) | L2 (Confirm) |
| trade_execute | Block | L2 (Confirm) | Threshold-dependent |
| yield_manage | Block | L3 (Collaborative) | L2 (Confirm) |

Sandbox tier blocks all outbound financial operations by default. Sandbox environments are for safe experimentation — agents in sandbox cannot spend real funds.

---

## 8. Integration Principles

The financial_transaction Action Type is protocol-agnostic. The SpendGate governs the authority to spend. The execution mechanism is abstracted behind a Skill (or equivalent action handler). This separation means:

- **Any payment protocol** can be governed by the same SpendGate. Machine-to-machine payment standards (x402), traditional payment APIs, and blockchain-native settlement all pass through the same governance evaluation.
- **The wallet or payment service** is an external resource the agent interacts with through governed Skills. The agent cannot directly access wallet keys, signing mechanisms, or custody infrastructure. This is structural enforcement — the same principle that prevents the agent from accessing the Governance Kernel.
- **Governance configuration is independent** of the payment provider. An organization can switch payment infrastructure without reconfiguring governance policy.

### 8.1 Compatibility Notes

For implementations integrating with emerging agentic payment and identity infrastructure:

- **x402 / HTTP-native payment protocols:** x402 per-request settlement maps to api_micropayment subtype at L0 within SpendGate bounds. Higher-value x402 transactions map to payment_send at L2. The SpendGate's session budget and frequency cap mechanisms are designed to govern high-frequency, low-value payment streams without creating latency bottlenecks.
- **ERC-8004 / Agent identity registries:** Agent identity verification through on-chain registries can serve as an input to the Vendor Whitelist evaluation — a counterparty with a verified ERC-8004 identity and established reputation score may receive different risk classification than an unverified counterparty. The SpendGate's vendor_risk_score field accommodates reputation-weighted risk assessment.
- **Agent-to-Agent (A2A) settlement:** When both parties to a transaction are governed agents, each agent's SpendGate evaluates independently. The sending agent's governance authorizes the outbound transaction. The receiving agent's governance authorizes the inbound acceptance. Decision Records from both sides can be cross-referenced for bilateral audit.

---

## 9. Relationship to GAP Principles

This Action Type specification is a direct application of existing GAP protocol principles to a new domain. No new protocol mechanisms are introduced.

| GAP Principle | Application in financial_transaction |
|---|---|
| **Iron Rule** | Agent cannot modify SpendGate thresholds. Structural enforcement prevents access to financial governance configuration. |
| **Constraint-Guided Autonomy** | SpendGate rejection triggers compliant replanning. The agent finds governed paths to its financial objectives. |
| **Decision Lineage** | Financial Provenance extends the Decision Record with monetary context. Full chain from policy → proposal → evaluation → execution → outcome. |
| **Multi-Phase Authorization** | Intent gate and outcome gate ensure authorization does not pre-approve outcomes. Each phase evaluated independently. |
| **Separation of Creation and Validation** | Critic reviews every financial proposal. The Proposer cannot self-certify financial actions. |
| **Structured Uncertainty** | Decision Records capture what was uncertain at the time of authorization — price volatility, counterparty risk, execution probability. |
| **Adversarial Reasoning** | Mandatory Proposer/Critic evaluation for financial actions. The tighter CGA loop (3 iterations) reflects the irreversible nature of financial execution. |
| **Reconciliation** | Outcome gate validates execution matches intent. Discrepancy handling uses the standard drift event mechanism with financial context. |
| **Dynamic Risk Escalation** | Financial behavioral patterns (sudden volume spike, new counterparty categories, unusual timing) can trigger runtime authorization tier increases. |
| **Out-of-Band Authority Verification** | L2+ financial authorizations require human identity verification through channels independent of the agent's environment. |

---

## 10. Implementation Notes

1. **Phase 1 is schema work.** Registering the Action Type, extending the Decision Record, and defining SpendGate configuration can be implemented before any payment protocol is integrated.
2. **SpendGate state is sensitive.** Session spend tracking, budget remaining, and rate limit counters must be treated as governance state — protected with the same isolation guarantees as the Governance Kernel itself.
3. **Vendor whitelist management is a human workflow.** The mechanism for adding, removing, and auditing approved vendors is outside the scope of this Action Type specification.
4. **Currency handling requires precision.** All financial amounts should use fixed-point decimal representation. Floating-point arithmetic in financial governance is a bug.
5. **The dual audit trail is optional but recommended.** Anchoring Decision Record hashes on a settlement ledger provides the strongest accountability guarantee, but is not required for protocol compliance.

---

## Contributing

This specification is part of the General Autonomy Protocol. Contributions, feedback, and implementation reports are welcome.

**Repository:** [github.com/Nexeom/General-Autonomy-Protocol](https://github.com/Nexeom/General-Autonomy-Protocol)
**Website:** [nexeom.ca/gap](https://nexeom.ca/gap)

---

*General Autonomy Protocol · Nexeom · 2026*
