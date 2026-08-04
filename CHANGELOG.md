# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file starts here. There are no tagged releases and no published packages
before it: `0.1.0-alpha` was the version carried on `main` throughout that
period. Everything below is the first set of changes recorded.

## [Unreleased]

Five waves of security remediation. The governance surface changed shape in ways
a deployment cannot absorb silently — read **Breaking** first. Signed decisions
produced by the previous version do not verify against this one.

### Added

- `gap_kernel/_time.py` — `utcnow()` and `ensure_utc()`. Every timestamp GAP
  produces is timezone-aware UTC.
- `ExecutionLedger` (`gap_kernel/verification/execution_ledger.py`) — the replay
  authority for a decision's nonce at every authorization level, with an explicit
  state machine: a failed execution stays resumable, a completed one is spent.
- Trust-root provisioning in `gap_kernel/service/kernel_server.py` —
  `provision_trust_root()`, `load_trust_root()`, `TrustRoot`, and a kernel
  identity that persists across restarts so an auditor has a stable key to pin.
  `SubprocessGovernanceClient` pins the child to it.
- Attested provenance in the world-model store — `EvidenceChannel`,
  `attested_properties()`, `evidence_is_attested()`, and a readable log of every
  governance-relevant mutation (`GET /world/evidence-mutations`).
- `.github/workflows/ci.yml` — tests on Python 3.11–3.13 plus Windows, a 90%
  coverage floor, `ruff`, and a job that runs the README's exact quickstart
  commands so the documented install path cannot rot.
- `docs/THREAT_MODEL.md` (names the adversary and what GAP does *not* defend
  against), `docs/KNOWN_GAPS.md` (what is broken right now), `docs/ROADMAP.md`.
- `SECURITY.md` rewritten as a real policy: reporting process, scope, response
  targets, safe harbour, and an honest status header.
- An `[api]` optional extra, so the kernel core installs with three dependencies.
- Test suite 389 → 550 tests, coverage 93%.

### Changed

- The Action Type Registry is governance configuration and now rides inside the
  signed `ApplicabilityProfile` (`action_types`), inheriting its verification.
- `gap_kernel.api.app.app` is materialized lazily via PEP 562 `__getattr__`, so
  importing `create_app` no longer constructs an ungoverned application as a
  side effect. Materializing it logs a warning naming each unenforced guarantee.
- The Strategy Layer receives a deep copy of the world model rather than the
  live one.
- Governance integrity monitors observe every action in a proposal instead of
  only the first.
- Out-of-band approvals are reserved *before* dispatch rather than consumed
  after, and per-action completion is tracked, so a partial-failure retry
  resumes instead of re-running side effects under one human approval.
- `docs/CONFORMANCE.md` restructured around five statuses — "built but unfed" is
  a real state the previous four could not express, and it is the honest status
  of three GIM detectors, the dynamic risk engine and the SIR gate.

### Fixed

- The reconciler heartbeat stopped permanently and silently when
  `reconcile_once` met one malformed entity: failures are now contained per
  entity and recorded. The circuit breaker is observable and resettable,
  `awaiting_approval` no longer resets the failure counter, and integrity holds
  are no longer swallowed by escalation dedupe.
- `DriftWatcher._check_sla_drift` raised `TypeError` on the timezone-aware
  ISO-8601 timestamp any real CRM emits — the parse was guarded, the subtraction
  on the next line was not — and that exception stopped the heartbeat.
- `LineageStore.append()` was an unsynchronized read-modify-write on a
  `check_same_thread=False` connection reached from a threadpool, so concurrent
  appends dropped audit records. Now serialized under a lock with
  `BEGIN IMMEDIATE`.
- Lineage signatures verify against stored canonical bytes plus a
  `schema_version` rather than a re-serialization, so adding a model field no
  longer invalidates history. The chain anchor is signed, and `query_by_entity`
  no longer treats `%` and `_` in an entity id as SQL wildcards.
- `SubprocessGovernanceClient._call` had no lock and no request correlation,
  while every route runs in a threadpool against one shared client: two
  concurrent evaluations could each receive the other's signed decision.
  Requests now carry a monotonic id under a lock, and a mismatch fails closed
  and kills the child rather than continuing against an offset stream.
- Constraint `threshold` and `tier` were silently dropped on the HTTP round
  trip, so a constraint created over the API lost its numeric floor.
- `Set` was undefined in `multi_agent.constraints_for` — found by the new lint
  gate on its first run.
- `pip install -e .` omits pytest, so the documented test command could not be
  run from the documented install path. Both README and CONTRIBUTING install
  `[dev]`.
- Deprecation warnings emitted by the suite: 3,901 → 2.

### Security

- **The world model is evidence, not telemetry.** `POST /world/ingest` accepted
  an arbitrary properties dict for any entity with no authentication and no
  schema, and the regulatory evaluators read exactly that world model.
  Reproduced end to end: ingest an EU lead with `gdpr_consent` false → a
  `send_email` proposal under a `gdpr_consent_required` HARD constraint is
  rejected; one more ingest with `gdpr_consent` true → the identical proposal
  returns APPROVED carrying a valid kernel signature. Governance-relevant
  properties now require provenance stamped by the store, and a governed kernel
  treats an unattested value as unevaluable — a violation.
- **The Action Type Registry could be overwritten.** `register_action_type`
  claimed autonomous systems could not register types and performed no check: a
  bare dict write that could silently replace a baseline type, so re-registering
  `skill_modification` at L0 stripped its L2 approval gate.
- **Signed decisions were replayable.** `ExecutionFabric.execute` kept no record
  of what it had executed — every guard was stateless and the out-of-band ledger
  only covered L2+, leaving the routine autonomous path with no record at all.
  One signed risk-3 decision executed 4 times out of 4.
- **The kernel honoured the agent's clock.** `evaluate_proposal` accepted a
  `current_time` that the RPC filled from the agent's request; naming a time
  outside a cron window dropped the constraint from the active set, so a $10
  spend against a $5 ceiling came back APPROVED and kernel-signed, timestamped
  a year in the past.
- **The agent chose whether the regulatory gates had anything to evaluate.** The
  GDPR and contact-hours gates branched on `if entity:` with no `else`, and the
  Strategy Layer authors the target — so a `send_email` to an entity absent from
  the world model was approved with `gdpr_consent_required` active.
- **Which key signed a decision was unauthenticated metadata.**
  `kernel_public_key_id` was in the signature exclusion set and therefore
  rewritable.
- **The isolation boundary carried a registry writer.** The RPC surface exposed
  `register_action_type`, so the agent-side process could mutate the kernel's
  registry across the boundary meant to contain it — registering a type turned a
  REJECT into an APPROVE. The boundary also relayed the agent's clock.
- **The RPC read was unbounded** (the agent could OOM the governance authority),
  errors returned exception text carrying filesystem paths and pydantic field
  values, and the child's stdout shared the protocol channel.
- **Four unused runtime dependencies reached the signing key.** `langgraph`,
  `langchain-core`, `langchain-anthropic` and `langchain-openai` were installed
  by default, transitively pulling the anthropic and openai SDKs plus their HTTP
  stacks into the process that holds the Ed25519 private key. Nothing imported
  them.

### Breaking

1. **Decisions signed by the previous version no longer verify.** The signing
   domain tag is now `gap.governance.decision.v2`, and `kernel_public_key_id`
   moved inside the signed payload. Nothing migrates persisted decisions.
   *What to do:* drain in-flight decisions and let them execute before
   upgrading; re-evaluate anything still pending afterwards. Archived v1
   decisions become historical records this version cannot verify — keep the
   prior kernel available if you must re-verify them.

2. **`GovernanceKernel.register_action_type()` raises on a governed kernel.**
   Registered types are carried in the signed profile; a runtime write would be
   an unsigned change to the governance configuration. An open/prototype kernel
   keeps the call as a monotonic ratchet: a type may be added, never replaced,
   and never below the floor its own risk profile implies.
   *What to do:* put the type in `ApplicabilityProfile.action_types` and have
   the authority re-sign the profile with `sign_profile()`.

3. **The `register_action_type` RPC method and `POST /governance/action-types`
   are deleted.** Both are gone from the service, both clients, and every HTTP
   posture; the route now returns 405.
   *What to do:* as above — the signed profile is the only way in.
   `GET /governance/action-types` still reads the registry.

4. **Mutating HTTP routes are not registered by default.** `POST /intents`,
   `PUT`/`DELETE /intents/{id}`, `POST /world/ingest`, `POST /reconciler/trigger`,
   `PUT /reconciler/config`, `POST /learning/proposals/{id}/approve|reject` and
   `POST /escalations/{id}/resolve` are absent unless asked for.
   *What to do:* `create_app(enable_mutating_routes=True)` — and only where the
   app is reachable solely behind your own authenticated proxy. GAP carries no
   authentication and deliberately never will.

5. **`build_governed_deployment()` raises `GovernanceConfigError` without an
   independent trust root.** Verifying the profile against a registry supplied
   by the same process that supplied the profile is circular, so it is refused
   rather than warned about.
   *What to do:* run `provision_trust_root(<deployer-owned dir>, {key_id:
   public_key_hex})` once and set `GAP_TRUST_ROOT` to that path. For prototyping
   only, pass `require_independent_trust_root=False`.

6. **`build_governed_deployment()` raises without a durable ledger directory.**
   An in-memory ledger loses every record of what has executed, so a spent
   authorization becomes replayable across a restart. Passing
   `OOBLedger(":memory:")` or `ExecutionLedger(":memory:")` in is refused too.
   *What to do:* pass `ledger_dir=...` on persistent storage. For prototyping
   only, pass `allow_ephemeral_ledgers=True`.

7. **A governed kernel ignores a caller-supplied `current_time`.** The parameter
   still exists and an open/prototype kernel still honours it; a governed kernel
   reads its own clock, and the RPC boundary no longer relays the agent's clock
   at all.
   *What to do:* tests and tools that pinned evaluation time must either run
   against an open kernel or move the process clock; passing `current_time` to a
   governed kernel now silently has no effect.

8. **In governed mode a target the world model does not track violates a
   world-model-backed HARD constraint.** Proposals under `gdpr_consent_required`
   or the contact-hours gate against an untracked entity are REJECTED where they
   were previously approved.
   *What to do:* reconcile the entity into the world model before proposing
   against it. To keep the old behaviour deliberately, construct the kernel with
   `GovernanceKernel(allow_untracked_targets=True)`.

9. **Governance-relevant world-model properties require attested provenance in
   governed mode.** `gdpr_consent`, `geo`, `jurisdiction` and `local_hour` that
   did not arrive on an attested channel are unevaluable, which is a violation —
   the same verdict as a constraint with no registered evaluator.
   *What to do:* write them through the store with a declared channel —
   `store.upsert_entity(entity, channel=EvidenceChannel(channel_id="consent_of_record",
   attested=True))` — where the `attested` flag is the deployment asserting that
   channel authenticates what it reports. To keep the old behaviour
   deliberately, construct the kernel with
   `GovernanceKernel(require_attested_evidence=False)`.

10. **Runtime dependencies pruned and the REST surface moved to an extra.**
    `langgraph`, `langchain-core`, `langchain-anthropic` and `langchain-openai`
    are removed. `fastapi` and `uvicorn` are no longer installed by
    `pip install gap-kernel`.
    *What to do:* `pip install "gap-kernel[api]"` if you import
    `gap_kernel.api`, and install the langchain packages directly if your agent
    used them.

[Unreleased]: https://github.com/Nexeom/General-Autonomy-Protocol/commits/main
