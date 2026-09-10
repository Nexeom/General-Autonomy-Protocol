# GAP review guide

This package helps the maintainer review the alpha implementation and makes a
future independent review reproducible. **The current review is maintainer-led.
No external review, endorsement or adoption is claimed, and no outside review
has been commissioned by preparing this document.**

GAP is an open protocol proposal. A reviewer should assess a specific code
revision and deployment against specific claims. A passing test suite is useful
evidence for those cases; it is not proof of complete conformance or security.

## 1. Establish the scope

Read these in order:

1. [README](../README.md): intended behavior and onboarding.
2. [CONFORMANCE.md](CONFORMANCE.md): enforced, partial, unwired and planned claims.
3. [THREAT_MODEL.md](THREAT_MODEL.md): attacker capabilities and trust boundaries.
4. [KNOWN_GAPS.md](KNOWN_GAPS.md): defects and unmet requirements.
5. [PROTOCOL_SPECIFICATION.md](PROTOCOL_SPECIFICATION.md): proposed normative
   requirements. A requirement in this document is not an implementation result.

Record the commit, any uncommitted diff, Python version, OS, dependency versions
and deployment posture. Explicitly identify open/prototype mode, governed
embedded execution, or the reference gateway. Record enabled
prototype flags. Do not generalize a result from one posture to another.

```bash
git rev-parse HEAD
git status --short
git diff --stat
python --version
```

For an uncommitted review, retain the actual diff with the report. A commit hash
alone does not identify modified code.

## 2. Reproduce the baseline

In a Python 3.11+ virtual environment, run from the repository root:

```bash
python -m pip install -e ".[dev]"
python -m pip freeze
python examples/governed_demo.py
python examples/evaluate.py --output evaluation-results/local.json
python -m pytest tests/ -q --cov=gap_kernel --cov-report=term-missing --cov-fail-under=90
python -m ruff check gap_kernel/ tests/
```

Keep the full output and exit codes. Report observed test totals and coverage
for this revision. CI's matrix configuration is not evidence that the local
review exercised every supported OS/Python combination. Mypy is advisory in
the current CI configuration; do not report type checking as a required gate.

Local development validation used Windows/Python 3.12.14. Record the final
candidate's observed totals from a fresh run; earlier branch counts are not a
result for a changed test suite or a different environment. The evaluation
artifact separately identifies its git revision, working-tree source hash and
dependency versions.

For the recorded hosted validation, the
[CI run](https://github.com/Nexeom/General-Autonomy-Protocol/actions/runs/34496186894)
passed 746 tests with 92.82% line coverage on Python 3.11, 3.12 and 3.13, ten
demo checks, and reproducible-build checks on Linux. Its Windows/Python 3.13
job also passed. The separate
[Docker boundary run](https://github.com/Nexeom/General-Autonomy-Protocol/actions/runs/34496186876)
passed all eight checks against the documented topology after confirming
gateway access to the sink's exact IP. These are project-run automated results
for the recorded revision and environments; they do not claim an independent
review.

The following focused commands are useful for tracing a claim or reproducing a
failure after the baseline run. They need not all be rerun when the complete
suite already covered them and no relevant code has changed.

```bash
python -m pytest tests/test_evidence_attestation.py tests/test_world_model_trust.py tests/test_strategy_isolation.py -q
python -m pytest tests/test_decision_integrity.py tests/test_replay_protection.py tests/test_oob_verification.py tests/test_bypass_regressions.py -q
python -m pytest tests/test_fail_closed.py tests/test_enforcement_properties.py tests/test_registry_integrity.py tests/test_boundary_hardening.py -q
python -m pytest tests/test_corrigibility.py tests/test_reconciler_resilience.py tests/test_lineage_identity.py tests/test_lineage.py -q
```

## 3. Check the authorization and evidence protocol

Follow the entire path from untrusted proposal to the observable tool side
effect. A test that only checks the returned verdict can miss an unauthorized
dispatch. Count tool invocations or inspect a separate tool-side receipt as
well as the decision record.

| Invariant to review | Negative case and expected result | Existing evidence entry point |
|---|---|---|
| Policy authority is fixed by the trusted profile signer. | Modify profile contents, substitute its key, or attempt runtime action-type replacement. A governed kernel must reject or refuse startup; no tool call follows. | `test_registry_integrity.py`, `test_deployment_defaults.py`, `test_boundary_hardening.py` |
| The authorization covers this proposal. | Change an action, target or parameter after evaluation, change the decision, or use a different kernel key. Signature/proposal binding must fail before dispatch. | `test_decision_integrity.py` |
| Temporal validity uses the authority's clock. | Supply a convenient evaluation time or malformed schedule. The caller must not disable a hard policy or extend authorization validity. | `test_boundary_hardening.py`, `test_time_boundary.py`, `test_fail_closed.py` |
| Signed evidence binds issuer, entity, exact values and validity window. | Substitute values, move evidence to another entity, use an unknown issuer, or alter `1` into `true`. The world-model gate must reject. | `test_evidence_attestation.py` |
| Agent data cannot replace the evidence trust root. | Supply issuer keys in agent configuration while an independent trust root is active. The kernel must continue to trust only the deployment's issuers. | `test_evidence_attestation.py` |
| Evidence freshness is enforced and its residual risk is explicit. | Expired/forward-dated evidence must be rejected. Previously valid evidence for withdrawn consent can still pass within the freshness ceiling: record this as a limitation, not a passed revocation guarantee. | `test_evidence_attestation.py`, especially the withdrawn-consent replay case |
| One authorization cannot produce concurrent duplicate dispatch. | Present the same decision sequentially, concurrently, and after restart. Durable nonce and approval ledgers must preserve the claimed scope; inspect side effects, not only exceptions. | `test_replay_protection.py`, `test_bypass_regressions.py` |
| Higher authorization levels require the correct human approval. | Omit, forge, substitute or reuse an approval. L2+ must not execute automatically; approval must bind the decision and be reserved before dispatch. | `test_oob_verification.py`, `test_approval_gating.py` |
| A resumed operation skips already completed actions. | Fail after an earlier action completed and then retry. Earlier recorded successes must not dispatch twice. Separately inspect the tool-side crash window; local completion tracking does not alone guarantee exactly-once external effects. | `test_replay_protection.py` |
| Replanning remains within policy and halt scope. | Mutate generator inputs, keep proposing a forbidden action, or retarget after halt. Shared governance state must remain unchanged and forbidden/halted work must not dispatch. | `test_strategy_isolation.py`, `test_adversarial.py`, `test_corrigibility.py` |
| Audit evidence detects the tampering it claims to detect. | Edit, remove or reorder stored records without the lineage key, and append concurrently. Verification must reject corruption without silently dropping legitimate records. A store-plus-key attacker remains outside the local chain's guarantee. | `test_lineage.py`, `test_lineage_identity.py` |
| A bad entity does not silently terminate reconciliation. | Feed malformed/timezone-sensitive input alongside a valid entity. The loop must surface the failure and continue handling other entities. | `test_reconciler_resilience.py`, `test_time_boundary.py` |

Test paths in this table are relative to [`tests/`](../tests/). Read the actual
assertions and fixtures before interpreting the title as evidence. Where a
required case lacks a test, record the gap instead of marking it covered.

For signature checks, inspect canonical serialization, domain separation,
signed-field coverage and verification-key selection. For replay checks,
inspect transaction boundaries, concurrent reservations, expiry, restart and
failure recovery. Identify who can read or modify every key, database and
configuration path.

## 4. Integration checks

The gateway and LangGraph adapter have an executable local demonstration.
The maintainer's branch run passed the demo's ten scripted checks. Reproduce
the commands below and retain the output for your own revision. The demo's
same-user processes and scripted approver are functional fixtures; a human
approval and a container-isolation check are separate exercises.

| Artifact | Status of this review package | Evidence required |
|---|---|---|
| Governed demo | `python examples/governed_demo.py`; ten local functional checks passed in the branch run. | Inspect individual checks and outbox behavior. Scripted approval must not be reported as human oversight. |
| Reference tool gateway | `gap_kernel/gateway/`; unit/integration entry point `tests/test_gateway.py`. [GATEWAY.md](GATEWAY.md) documents the HTTP and operator paths. | Verification, approval, nonce reservation and tool credentials live outside the agent process; malformed or unauthorized requests cannot invoke the tool. Isolation also requires the deployed boundary. |
| Docker network example | [`deploy/compose.yaml`](../deploy/compose.yaml), [`examples/container_check.py`](../examples/container_check.py); commands in [GATEWAY.md](GATEWAY.md). [Recorded Linux run: 8/8 checks passed](https://github.com/Nexeom/General-Autonomy-Protocol/actions/runs/34496186876). | From the agent container, direct access to the live protected tool fails and tool credentials are unavailable. The permitted lookup succeeds through the gateway. Recheck mounts, network membership and privilege settings for the deployment under review. |
| LangGraph adapter | `.[langgraph]` extra; `examples/langgraph_agent.py`; `tests/test_langgraph_integration.py`. The local demo uses the actual framework. | The workflow routes its tool calls through the governed path, propagates structured rejection, bounds retries and reaches an approval/escalation state without treating every denial as an exception to bypass. |
| Evaluation harness | `python examples/evaluate.py --output evaluation-results/local.json`; the local branch run passed 26/26 deterministic scenarios with exit code 0. See [EVALUATION.md](EVALUATION.md) for the recorded result and metric definitions. | Check both the exit code and top-level `passed`. The JSON includes scenario observations, fixed denominators, category latency, source identity, dependencies and infrastructure errors. |

```bash
python -m pytest tests/test_gateway.py tests/test_langgraph_integration.py -q
python examples/governed_demo.py
```

With the gateway already running and `GAP_GATEWAY_URL` and the client-only
`GAP_GATEWAY_TOKEN` configured as shown in [GATEWAY.md](GATEWAY.md):

```bash
python examples/langgraph_agent.py --fixture lookup
python examples/langgraph_agent.py --fixture replan
python examples/langgraph_agent.py --fixture approval
```

The first two fixtures should complete. The approval fixture intentionally
stops with `awaiting_approval` and exit code 3. Use the interactive operator
flow to approve the exact stored request separately; the agent must not hold
the approval private key. These fixtures do not invoke or evaluate an LLM.

For the gateway deployment, assess these threat scenarios explicitly:

- The agent supplies arbitrary protocol messages, including forged approvals,
  an unrecognized tool and a payload different from the approved proposal.
- The agent has code execution in its own process/container and attempts to
  call the protected tool directly, read credentials, alter the trusted profile
  or substitute a verification key.
- The agent retries the same authorization from concurrent clients and after a
  gateway restart. Tool-side observations must agree with the ledger's claim.
- The gateway or tool fails before dispatch, during a side effect, or before
  the result is recorded. Distinguish safe retry, ambiguous outcome and manual
  reconciliation; do not infer exactly-once behavior from a success response.
- A human halts the relevant scope while work is queued. Verify the actual
  dispatch-time gate and document whether an already executing action can stop.
- Signed evidence is authentic but stale or factually wrong. Document what the
  issuer, freshness ceiling and domain validation can establish.

A local subprocess integration test does not demonstrate container/network
isolation. If Docker is unavailable, report container execution as **not run**;
do not turn a static Compose inspection into a deployment validation.

## 5. Evaluate usefulness separately from enforcement

Report at least these measurements, with scenario counts:

| Measurement | Definition |
|---|---|
| Allowed-action completion | Expected-permitted cases that produce the intended tool effect divided by expected-permitted cases. |
| Forbidden-action blocking | Expected-forbidden cases that produce no forbidden effect divided by expected-forbidden cases. |
| False blocks | Expected-permitted cases incorrectly blocked or unnecessarily escalated, with reasons. |
| Replanning outcome | Cases that reach a permitted alternative within the retry budget, versus correctly escalated or unresolved cases. |
| Latency | Time spent in evaluation/dispatch and end-to-end workflow, stating workload, environment and which phases are timed. |

Avoid treating a single aggregate “pass rate” as all of these measures. A system
that rejects every action can block forbidden actions perfectly while being
unusable. Fixed rule fixtures test known control flows; claims about model
behavior require model-backed cases with model/version, settings, prompts,
repeated runs and failure examples recorded.

## 6. Record the review

Use a report containing:

```text
Reviewer and relationship to the project:
Date:
Commit and uncommitted-diff identifier:
OS, Python, dependency versions:
Deployment posture and enabled prototype flags:
Claims tested:
Commands, exit codes and retained outputs:
Tool-side observations:
Passed cases / failed cases / not-run cases:
Findings (reproduction, preconditions, impact, expected vs actual):
Known limitations and claims not assessed:
Disposition (fixed, open, or outside this stated scope, with reason):
```

Do not record an automated assistant, maintainer-run test or prepared checklist
as an independent review. A later external report should identify its own
reviewer, scope and evidence. Conformance status should change only when the
corresponding implementation and observed results support the change.
