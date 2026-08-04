# GAP Threat Model

This document names the adversary GAP is built against, states what the
implementation defends and what it does not, and identifies the deployment
control for each thing it cannot close itself.

It is written against the code in this repository. Every claim points at a file
and a symbol so you can check it. It is not a proof, and no external review has
been performed.

Companion documents: [CONFORMANCE.md](CONFORMANCE.md) gives per-capability
status (built and verified / partial / planned); [SECURITY.md](../SECURITY.md)
gives the reporting process and the in-scope list.

---

## 1. What is being protected

| Asset | Where it lives | Why an adversary wants it |
|---|---|---|
| Governance kernel private signing key | Kernel process; on disk at `kernel_identity.json` under the trust root | Sign an `APPROVED` decision for anything |
| Applicability Profile — Tier-1 regulatory floor plus the Action Type Registry | Signed blob, verified against the trust root's `profile_keys` | Weaken the floor, or register an action type whose default authorization is low enough to let a forbidden action through |
| The dispatch path — `ExecutionFabric._executors` and whatever tool credentials the deployment registered into it | **Agent process** | Act without any decision at all |
| Kill switch | **Agent process**, shared object (`app.state.kill_switch`) | Prevent or undo a halt |
| Governance-relevant world-model properties (`gdpr_consent`, `geo`, `jurisdiction`, `local_hour`) | **Agent process**, `WorldModelStore` | Flip the facts a HARD constraint is evaluated against, turning a rejection into an approval |
| Decision lineage chain and its signing key | **Agent process**, `LineageStore` (SQLite) | Erase or rewrite the record of what happened |
| Replay ledgers and approver key registry | **Agent process**, SQLite + `PublicKeyRegistry` | Replay a spent decision or a spent human approval |

Note how much of that list says "agent process." That is the subject of §4.

---

## 2. Adversaries

**A. The governed agent.** The primary adversary and the one GAP is designed
around. A model-driven planner that pursues its objective through whatever path
remains open: it writes proposals, reads structured rejections, and replans. It
is assumed to be persistent, creative, and to have read this repository. It is
**not** assumed to be running attacker-authored native code inside its own
process.

**B. A compromised agent process.** An adversary with arbitrary code execution
in the process the agent runs in — prompt injection escalated into a
code-executing tool, a malicious or compromised dependency, a hostile executor
implementation. **GAP does not defend against this.** See §4.

**C. A network caller** reaching the REST surface (`gap_kernel/api/app.py`).

**D. A malicious or careless integrator.** Supplies governance *data* — an
Applicability Profile, a world-model write, an RPC payload, an HTTP request —
but does not control the deployment or its environment.

**E. An insider with filesystem access** to the SQLite ledgers, the lineage
database, or the trust root directory on the host.

**Explicitly not modeled:** an adversary with OS-kernel or hypervisor access;
timing and other side channels; a supply-chain attack on CPython or
`cryptography` itself; compromise of a human approver's signing hardware; and
physical access.

---

## 3. The three postures

The code has exactly three deployment postures. They are not degrees of the same
thing — they buy different properties.

### Posture 1 — Open / prototype

Raw constructors: `GovernanceKernel()`, `ExecutionFabric(world_model, ...)`,
`CGALoop(...)`, and the module-level `gap_kernel.api.app:app` (which logs a
warning enumerating everything it does not enforce).

Enforced: fail-closed constraint evaluation, the rank-based authorization
comparator, and CGA's inability to negotiate around a hard constraint.

Not enforced: no signed regulatory floor; strict action typing off;
`register_action_type` available at runtime (as a monotonic ratchet — a type
cannot be replaced, and cannot register below the floor its own risk profile
implies, `governance/kernel.py`); a caller-supplied `current_time` is honoured;
`allow_unsigned_decisions=True` lets an unsigned decision execute; ledgers are
in-memory.

**This is not a security posture.** It exists for embedding and tests.

### Posture 2 — Governed, in-process

`build_governed_deployment(..., isolated=False)` or
`create_app(applicability_profile=..., isolated=False)`.

Adds over posture 1: a signed Applicability Profile is required and verified;
strict action typing is on; the kernel signs every decision and the fabric
verifies it fail-closed; the governed kernel ignores a caller-supplied
`current_time` and reads its own (`GovernanceKernel._resolve_time`); runtime
`register_action_type` is refused outright; the SIR gate is mandatory on the CGA
loop; a kill switch is always present.

Does not add: the kernel object and its private signing key are Python
attributes in the same address space as the agent. Adversary B forges freely
here. `InProcessGovernanceClient` documents itself as a convenience, not a
boundary.

### Posture 3 — Governed + isolated subprocess (the default)

`build_governed_deployment(...)` with `isolated=True` (the default) and
`require_independent_trust_root=True` (the default), a durable `ledger_dir`, and
`GAP_TRUST_ROOT` set in the environment the deployer controls.

This is the only posture with a real trust boundary, and §5 states exactly what
that boundary buys.

| | Posture 1 (open) | Posture 2 (governed, in-process) | Posture 3 (governed + isolated) |
|---|---|---|---|
| Signed regulatory floor required | No | Yes | Yes |
| Decisions kernel-signed and verified | Optional | Yes | Yes |
| Signing key outside the agent's address space | No | No | **Yes** |
| Profile verified against a key the agent side did not choose | No | Only if `GAP_TRUST_ROOT` is set | **Yes — required by default** |
| Replay protection survives a restart | No | Only with `ledger_dir` | **Yes — required by default** |
| Runtime action-type registration | Ratcheted | Refused | Refused |
| Resists an agent that can run arbitrary code in its own process | No | No | **No — see §4** |

---

## 4. The central limitation: enforcement runs in the agent's process

The subprocess boundary moves **one** thing out of the agent process: the
`GovernanceKernel` and its private signing key. Everything downstream of the
decision stays with the agent:

- `ExecutionFabric` (`gap_kernel/execution/fabric.py`) — **the code that
  verifies the kernel's signature**;
- `ExecutionFabric._executors` — the executor registry, holding whatever tool
  credentials the deployment registered;
- `KillSwitch` (`gap_kernel/governance/corrigibility.py`), shared by reference
  with the loop and exposed as `app.state.kill_switch`;
- `WorldModelStore` and the entity property dicts the constraint evaluators rule
  on;
- `LineageStore` **and its signing key** (`gap_kernel/lineage/store.py`);
- `ExecutionLedger`, `OOBLedger`, and the approver `PublicKeyRegistry`.

**So adversary B does not need to forge anything.** Each of the following is an
ordinary attribute write or method call available to any code running in that
process:

1. **Call `fabric._dispatch_action(action)` directly.** This skips the kill
   switch, the signature check, the verdict check, expiry, the ledger claim, and
   OOB reservation — every guard in `ExecutionFabric.execute`.
2. **Disable verification in place.** Set `fabric._kernel_public_key_hex = None`,
   `fabric._kernel_key_registry = None`, `fabric._allow_unsigned_decisions =
   True`, then present a hand-built `GovernanceDecision`.
   `_verify_decision_signature` and `_verify_single_use_fields` both return early
   on exactly that combination.
3. **Read or replace the executor registry.** `fabric._executors` and
   `register_executor` reach whatever credentials the deployment wired in — the
   real damage surface, since the executors are what actually touch the world.
4. **Undo a halt.** `kill_switch.disengage()` takes a free-text `disengaged_by`
   and authenticates nothing. Or set `fabric._kill_switch = None`.
5. **Rewrite the evidence.** Write `entity.properties["gdpr_consent"]` and the
   reserved `_evidence_provenance` stamp directly. The store is the sole
   provenance writer *for writes that go through the store*; the stamp is a plain
   dict field. `world_model/store.py` states this in its own module docstring.
6. **Rewrite history.** `LineageStore._signing_key_hex` is in the same process,
   so a record can be altered, re-signed, and the chain anchor recomputed — and
   `verify_chain_integrity()` will pass.

**What would change this:** an execution boundary. The fabric, the executors, the
kill switch and the ledgers would have to sit behind the same kind of process
boundary the kernel already has, with the agent side holding only a request
channel. **That is not built.** There is no socket mode, no attach mode, and no
partial version of it in the repository.

**Therefore: GAP's enforcement is meaningful against an agent that plans,
proposes, and negotiates. It is not meaningful against an agent that can run
arbitrary code in its own process.** Treat "the agent obtained code execution" as
a total compromise of GAP as currently implemented, and size the surrounding
controls accordingly.

---

## 5. What the isolated posture does buy

Precisely, and no more than this.

- **The kernel's private signing key is never in the agent's address space.**
  `SubprocessGovernanceClient` (`gap_kernel/client/governance_client.py`) holds a
  public key and a stdio channel. An adversary at posture 3 with code execution
  can execute *without* verification (§4), but **cannot produce a decision that
  an independent third party verifying against the pinned kernel public key would
  accept.** That matters for after-the-fact attribution and for any downstream
  consumer that verifies on its own terms.
- **Governance configuration is read-only across the boundary.** The
  `GovernanceService` dispatch table (`gap_kernel/service/kernel_server.py`)
  exposes `evaluate`, `get_public_key`, `list_action_types`, and
  `get_action_type` — nothing that writes. The Action Type Registry rides inside
  the signed profile, a governed kernel refuses `register_action_type` outright,
  and there is no `POST /governance/action-types` route.
- **The trust root is not chosen by the agent side.** The kernel process resolves
  `GAP_TRUST_ROOT` from *its own* environment
  (`kernel_server._kernel_for_service`); the path is never placed in argv or in
  the config blob. With a trust root in force, `build_governed_deployment`
  deliberately hands the child an **empty** key registry, so a child that somehow
  failed to resolve its own trust root has nothing to verify against and fails
  closed instead of trusting what the parent sent.
- **The child's identity is pinned.** The client compares the child's public key
  to `kernel_public_key_hex` from the trust root file and refuses the channel on
  mismatch. The pin is read from the trust root, never accepted as a caller
  argument.
- **Profile tamper fails closed** at the child, before any decision is served.
- **No clock crosses the boundary.** The evaluation request carries no time; the
  kernel reads its own. A caller-named evaluation time is a caller-chosen set of
  active constraints.
- **The channel itself is hardened.** Requests are serialized under a lock and
  correlated by request id (without both, two concurrent evaluations could each
  receive the other's validly-signed decision); a request line longer than
  `MAX_REQUEST_CHARS` (4,194,304 characters) is refused without being buffered,
  and its remainder is discarded in bounded chunks so the *next* request still
  frames correctly; errors cross as stable codes (`invalid_request`,
  `request_too_large`, `unknown_method`, `internal_error`) that leak no paths,
  field names, or exception types; and the child claims the real stdout for
  protocol framing so a stray `print` cannot inject a response line.

**Caveat that limits all of the above:** by default the kernel subprocess runs as
the *same OS user* as the agent. `provision_trust_root` writes
`kernel_identity.json` with mode 0600, which is hygiene, not a boundary, when
both processes share a user — the agent can simply read the file. Running the
kernel as a separate OS user is what makes the private key genuinely unreachable.
See §7.

---

## 6. What is enforced against the planning agent (adversary A)

| Attack | Control | Where | Status |
|---|---|---|---|
| Forge an approval | Ed25519 signature over a canonical payload carrying domain tag `gap.governance.decision.v2`; the fabric refuses unsigned, forged, or unverifiable decisions | `models/governance.py:canonical_decision_payload`, `execution/fabric.py:_verify_decision_signature` | Enforced |
| Rewrite which key verifies a decision | `kernel_public_key_id` sits **inside** the signed payload; an unknown key id fails closed | `execution/fabric.py:_resolve_kernel_public_key` | Enforced |
| Replay a decision at any level | `nonce` + `expires_at` are inside the signed payload; `ExecutionLedger` claims the nonce once, at every authorization level; a failed attempt resumes and skips actions that already succeeded | `verification/execution_ledger.py`, `execution/fabric.py:execute` | Enforced (durable ledgers required by default) |
| Spend one human approval twice | The OOB reservation is taken **before** dispatch, atomically via the ledger's primary key | `execution/fabric.py:_reserve_oob_authority` | Enforced |
| Swap the payload under an approval | `proposal_id` plus a `proposal_digest` content binding | `execution/fabric.py:execute` | Enforced |
| Approve above an approver's authority | Per-approver ceiling; an approver absent from the map cannot authorize at all | `execution/fabric.py:_verify_oob_authority` | Enforced when `approver_max_levels` is configured |
| Register a permissive action type at runtime | Governed kernel refuses; registry rides in the signed profile; no RPC method, no HTTP route | `governance/kernel.py:register_action_type` | Enforced |
| Choose the evaluation clock | Governed kernel ignores caller time; no clock crosses the RPC | `governance/kernel.py:_resolve_time` | Enforced |
| Flip consent or geography to change a verdict | Governance-relevant properties carry store-stamped provenance; unattested values are unevaluable, which is a violation; stamping is downgrade-only | `world_model/store.py` | ❌ **Not enforced. The stamp is an unsigned dict and is forgeable.** In the isolated posture the agent authors the `world_state` field of the `evaluate` request, so a hand-written `{"attested": true, ...}` reads as attested to the kernel. Reproduced: the same proposal is `rejected ['gdpr_consent_required']` unattested and `approved []` with a forged stamp. This needs **no code execution** — it is reachable through the published RPC interface by any party that supplies data, which is a different and larger exposure than the in-process caveat elsewhere in this document. |
| Negotiate around a hard constraint via CGA | Replan within bounds or escalate; never bypass | `strategy/cga_loop.py` | Enforced, adversarially tested |
| Route around a halt by retargeting | Kill switch checked first, per-entity scope; the loop refuses to plan and does not retarget | `execution/fabric.py:execute`, `strategy/cga_loop.py` | Enforced |
| Amplify authority through delegation | Rank-bounded sub-agent ceiling, constraints accumulate downward, halt propagates to the subtree | `governance/multi_agent.py`, `execution/sub_agent_executor.py` | Enforced for sub-agents routed through `SubAgentExecutor` |
| Launder state through the strategy generator | The generator receives a deep copy of the world model, intent, and drift event | `strategy/cga_loop.py` | Enforced |
| Hide actions from the monitors | Monitors observe every action in a proposal, not just the first | `strategy/cga_loop.py:_proposal_targets` | Enforced |
| Drop audit records by racing appends | Append is lock-serialized inside a `BEGIN IMMEDIATE` transaction; signatures verify against stored canonical bytes with a schema version | `lineage/store.py` | Enforced |
| Kill governance by feeding one malformed entity | Per-entity exception containment; an observable, resettable circuit breaker; integrity holds are not swallowed by escalation dedupe | `reconciler/loop.py` | Enforced |
| Silence drift detection with a junk `last_contacted` | Contact evidence must be an evaluable timestamp; an unparseable value fails closed to "not contacted" rather than suppressing drift | `reconciler/loop.py:_contacted_at` | Enforced — a *well-formed* forged timestamp still suppresses drift, so this depends on who can write the property (§4) |

---

## 7. Out of scope for GAP — and the deployment control for each

GAP is a governance kernel. These are real risks it deliberately does not
address, each with the control that does.

| Out of scope | Why the kernel cannot close it | Named deployment control |
|---|---|---|
| Code execution inside the agent process | The fabric, executors, kill switch, world model, and lineage store all live there (§4) | **OS user separation** — run the agent as its own unprivileged user with no read access to the trust root directory, and give its executors least-privilege credentials with per-tool network egress limits. This bounds the blast radius; it does not restore governance enforcement. |
| The kernel's private key readable by the agent | Same-user 0600 is hygiene, not isolation | **Run the kernel process as a separate OS user** (or in its own container/VM) that owns `GAP_TRUST_ROOT` and `kernel_identity.json`. The agent user must not be able to read that directory. |
| Lineage rewritten by whoever owns the database | `LineageStore` signs with a key in its own process, and the chain anchor row lives in the same SQLite file as the records it anchors | **External WORM anchoring** — periodically publish `(record_count, genesis_signature, tip_signature)` to append-only storage the agent host cannot write, and hold the lineage signing key in an HSM/KMS so the process that appends cannot re-seal history. |
| Unauthenticated mutation over HTTP | `create_app` carries no authentication and deliberately never will — an identity framework grown inside a governance kernel becomes the thing deployments trust for authorization while being nobody's product | **An authenticated reverse proxy in front of the app.** Keep `enable_mutating_routes=False` unless that proxy exists and identifies callers; the split between the read/evaluate surface and the mutating surface *is* the security contract. |
| Anyone in the process can engage or disengage the kill switch | `engaged_by` / `disengaged_by` are free-text; the audit log is in-memory | **Operator identity at the control plane** — expose engage/disengage only through an authenticated operator channel that binds a real identity, and persist the switch's audit log append-only outside the process. |
| Ledger and database files tampered with or deleted | They are SQLite files on the agent host | **Store the ledgers where the agent user cannot write them**, and back them up; a deleted `ExecutionLedger` restores replayability for every unexpired decision. |
| A dishonest Strategy Layer populating regulatory metadata | The Category evaluators verify that the required structural element is *present and declared* — that a fairness evaluation ran, that AML screening happened — not that the underlying result was truthful | **Source regulatory evidence from attested channels** and validate it independently of the layer that proposes the action. Treat the Strategy Layer as untrusted input. |
| Compromise of a human approver | GAP verifies a signature; it cannot verify a person | **Hardware-backed approver keys**, per-approver ceilings (`approver_max_levels`), and an out-of-band approval channel the agent has no route to. |
| Denial of service | The kernel refuses oversized requests and times out a hung child, but nothing rate-limits an integrator | **Rate limiting and quotas at the proxy**, plus process supervision that restarts a killed kernel channel. |

---

## 8. Known gaps in the current implementation

Facts about what is and is not wired, as of this revision. None of these are
vulnerabilities under [SECURITY.md](../SECURITY.md); all of them change what a
deployer should expect.

- **Two of the five GIM detectors have no shipped feed.** GIM-1 (authorization
  drift) and GIM-3 (threshold-avoidance decomposition) run unassisted on the CGA
  path and, in governed mode, hold and escalate a flagged action. GIM-4
  (escalation framing bias) *is* fed on the shipped path, but only when the
  deployment declares the framing it used (`reconciler.attach_escalation_framing`
  plus a `chosen_option_id` at resolution). **GIM-2** requires the deployment to
  pass `audited=` into `observe_decision`; the shipped CGA path does not, so no
  audit-behavior samples accumulate. **GIM-5** (`observe_challenge`) has no
  caller anywhere outside the test suite. "Five independent detectors" means five
  *implemented* detectors; two of them see nothing unless a deployment feeds
  them.
- **The independent authorization classifier is not wired into any shipped
  path.** `IndependentAuthorizationMonitor` has no reference outside its own
  module and its tests — not in the CGA loop, the reconciler, the deployment
  factory, or the API. Its reference classifier is a substring keyword scan over
  the action type and stringified parameters
  (`governance/independent_review.py`), so it is paraphrase-evadable by
  construction: renaming `delete_account` to `retire_profile` defeats it.
- **SIR's `declared_boundaries` is captured and never enforced.** The field is
  recorded on the Intent Declaration and read by no enforcement path.
  `verify_seal` has no callers outside tests. The shipped `ReconcilerLoop` and
  REST API run the CGA loop **without** the SIR gate — the gate is mandatory only
  under `build_governed_deployment`.
- **`DynamicRiskEngine`'s feed methods are never called on any shipped path.**
  `GovernanceKernel` does call `evaluate()`, but `set_baseline`, `record_action`,
  and `receive_external_signal` have no non-test callers, so
  `_behavioral_baseline` and `_action_history` are always empty and none of the
  volume, scope-expansion, or cascade detectors can fire.
- **Decisions signed under the v1 domain tag no longer verify.** The canonical
  payload is now tagged `gap.governance.decision.v2`. Nothing migrates historical
  decisions, and nothing is expected to — but a stored v1 decision will not
  verify.
- **`create_app` is weaker than `build_governed_deployment`.** The factory
  requires an independent trust root and durable ledgers by default; the HTTP app
  factory does neither. A governed `create_app` with `GAP_TRUST_ROOT` unset
  verifies the profile against the registry its own caller supplied — circular —
  and its `ExecutionFabric` gets in-memory ledgers, so replay protection ends at
  the next restart. Set `GAP_TRUST_ROOT`, or assemble the deployment through
  `build_governed_deployment`.
- **The lineage chain has no external witness.** It is signed by the process that
  owns the database, and the anchor lives in the same SQLite file. It is
  tamper-evident to a third party holding the public key; it is not tamper-evident
  to that process itself.

---

## 9. Verifying this yourself

```bash
pip install -e ".[dev]"
pytest tests/ -q --cov=gap_kernel --cov-fail-under=90
ruff check gap_kernel/ tests/
```

The suite is 550 tests; CI runs it on Python 3.11–3.13 on Linux and 3.13 on
Windows, and enforces the 90% coverage floor.

The adversarial suites are where the claims in §6 are tested:
`tests/test_adversarial.py`, `tests/test_decision_integrity.py`,
`tests/test_replay_protection.py`, `tests/test_boundary_hardening.py`,
`tests/test_registry_integrity.py`, `tests/test_world_model_trust.py`,
`tests/test_fail_closed.py`, `tests/test_corrigibility.py`,
`tests/test_kernel_service.py` (a real subprocess boundary),
`tests/test_deployment_defaults.py`, `tests/test_reconciler_resilience.py`,
`tests/test_strategy_isolation.py`.

There is no test that demonstrates §4, because §4 is not a defect the suite could
close — it is the shape of the current architecture.
