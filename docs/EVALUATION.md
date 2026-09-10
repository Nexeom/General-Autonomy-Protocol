# Reference gateway evaluation

`examples/evaluate.py` runs a repeatable functional evaluation against actual
gateway and sink HTTP processes, using real compiled LangGraph workflows. Its
planners and approver are deterministic fixtures. It does not measure model
intelligence, alignment, human decision quality, or production readiness.

## Run it

From the repository root, with Python 3.11 or later:

```sh
python -m pip install -e ".[dev]"
python examples/evaluate.py --output evaluation-results/current-platform.json
```

No provider account or API key is needed. The harness creates temporary demo
identities, a signed policy profile, short-lived attested facts, and separate
gateway/sink state. `notify` records a harmless note in a local SQLite outbox;
it does not send email. Temporary processes and state are cleaned up afterwards.

Exit status is zero only when all 26 scenarios pass and setup/cleanup succeed.
Exceptions are recorded as failures. Missing scenarios retain the expected
metric denominators, so an interrupted run cannot become a perfect smaller run.
An infrastructure failure makes the overall result fail even if completed
scenarios passed.

## What is measured

The versioned JSON artifact contains every scenario, expected outcome, observed
status, elapsed time, and supporting counts. These are scenario counts, not a
claim about independent statistical samples or the number of HTTP calls.

| Scenario family | Cases | Success condition |
|---|---:|---|
| Permitted lookup batches | 10 | Real LangGraph submits batches of 1–8 allowed reads; all expected records return successfully. Batch sizes 1 and 2 are repeated. |
| Replanning | 2 | A disallowed target is rejected, then a new proposal for `demo` completes on the second attempt. |
| Prohibited requests | 8 | Unknown tool, disallowed target, unexpected lookup argument, missing notify message, caller risk override, caller policy override, excessive batch cost, and invalid authentication are rejected at the appropriate gate; the outbox is unchanged. |
| Approval contract | 2 | LangGraph stops awaiting approval; unsigned and tampered approvals are refused; a scripted authorized signature permits exactly one note. |
| Concurrent duplicates | 1 | Eight simultaneous copies of one proposal return the same canonical record; eight simultaneous executions produce one completion, seven conflicts, and one note. |
| Restart and replay | 1 | After a real gateway process restart using the same durable state, a completed request remains recorded and replay is refused; its note appears once. |
| Partial failure and resume | 1 | An explicitly in-process executor fault fails the second action before its effect; reopening the durable execution ledger and retrying skips the first action and completes the second once. |
| Audit integrity | 1 | The gateway's signed lineage chain verifies after the HTTP scenarios. |

The partial-failure case uses `ExecutionFabric` with a deterministic failing
executor and a durable ledger. It is **in-process fault injection**, not an HTTP
failure or a container-boundary test. Its expected executor call counts are
`first=1, second=2`, with realized fixture effects `first=1, second=1`. The first
attempt must report failure; the retry must report success; subsequent replay
must be refused. This case does not cover a crash after an external effect
commits but before its acknowledgement reaches the gateway.

The headline rates have explicit, fixed denominators:

- **Prohibited-action prevention:** passing prohibited fixtures / 8. Schema and
  authentication rejection count as prevention, with the rejection layer and
  response status retained in each case.
- **Permitted task completion:** verified completions / 14, comprising 10 lookup
  batches, 2 replans and 2 separately approved notifications. The concurrent and
  restart fixtures, plus supporting outbox reads, are excluded from this rate.
- **Observed false blocks:** unexpected authorization refusals in those 14
  permitted cases / 14. Unobserved cases and other failures are also reported;
  zero observed false blocks alone does not establish successful completion.
- **Escalation contract correctness:** passing approval fixtures / 2. This checks
  correct gating, no effect before valid approval, and one effect afterwards.
  It does not evaluate human understanding, workload, bias, or approval quality.

## Timing and reproducibility

The JSON records nearest-rank p50 and p95 wall-clock times separately by scenario
family. Startup is excluded from scenario timers. Individual cases include all
their operations: for example, approval cases include supporting outbox reads,
and the restart case includes restarting the process. These are local functional
scenario timings, not isolated kernel latency, throughput, or production service
objectives. A family with one or two observations cannot characterize tail
latency reliably.

Every artifact records UTC timestamps, Python/OS/package versions, Git HEAD,
branch, dirty status, and a SHA-256 digest of the source working tree. The digest
covers sorted Git-visible source paths and file bytes, excluding generated
evaluation results, temporary deployment state, and caches. A dirty result must
be associated with this digest, not presented as evidence for the base commit
alone. Regenerate the artifact after source changes and on the committed release
candidate. An artifact generated on a developer machine is an observed local
result, not an external audit.

The checked-in [Windows artifact](../evaluation-results/current-windows.json)
is the concrete observed run. Its `passed`, `metrics`, and any
`infrastructure_error` fields are authoritative; the scenario inventory above
describes the evaluation contract rather than promising a passing result on
every platform.

## Observed candidate results

The checked-in Windows run evaluated clean source commit `e940f0c` with
package version `0.3.0a1`: **26/26 scenarios passed**, **8/8 prohibited cases
prevented**, **14/14 permitted tasks completed**, **0/14 observed false blocks**,
and **2/2 approval-contract cases passed**. Ten lookup scenarios had observed
p50 **71.436 ms** and p95 **92.267 ms** on that machine; these include the
scenario's HTTP operations and are not isolated kernel timings.

The [Linux/Windows CI run](https://github.com/Nexeom/General-Autonomy-Protocol/actions/runs/34496186894)
records the per-platform tests. Its Linux Python 3.12 job passed **746 tests**
with **92.82% line coverage**. The demo and two reproducible alpha artifact
builds also passed. Read the run's individual job results for other platforms.

The separate [live Docker run](https://github.com/Nexeom/General-Autonomy-Protocol/actions/runs/34496186876)
passed all eight reference-boundary checks and the 26 functional scenarios.
[The retained boundary result](../evaluation-results/container-ci.json) records
the PR head and synthetic merge commit used by CI. The agent could access an
authorized record through the gateway; its private-directory, credential-mount,
backend-hostname, and backend-IP checks passed. The gateway first reached the
same backend IP as a positive control. This is evidence for the documented
Docker topology on that hosted Linux runner, not for arbitrary deployments.

These are maintainer-run automated results. They are not independent
implementation feedback or an external security audit. Later documentation and
evidence-only commits do not change which source revision each result measures.

## Limits of this evidence

All local processes share the evaluator's OS user. This harness does **not**
establish separation from an agent with arbitrary code execution on that host.
The Docker topology and `examples/container_check.py` evaluate a different
boundary; their results must be reported separately. The harness intentionally
holds operator test material so it can provision and sign its scripted fixtures.

Passing these cases does not establish general resistance to prompt injection,
malicious models, arbitrary tools, compromised operators, host administrators,
distributed deployments, or legal/regulatory noncompliance. The small allowlist,
trusted demo evidence, and short-lived credentials are fixed test conditions.
Audit-chain verification also does not provide an independent external witness
against a party that controls both the store and its signing identity.
