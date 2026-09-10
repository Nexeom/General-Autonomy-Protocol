# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Early entries recorded the initial untagged implementation. The repository
subsequently published `v0.2.0-alpha`; the next candidate is `0.3.0a1`.

## [0.3.0a1] — 2026-09-10

### Governed tool boundary

- Incorporated the existing signed-evidence work: kernel-side issuer verification
  binds entity identity, exact values and freshness; agent-written stamps do not
  confer authority.
- Fixed concurrent claims on failed retries and conservative activation of
  unsupported runtime conditions; blocked reserved audit-provenance merges.
- Added an authenticated two-tool gateway owning policy, world model, signing
  keys, approval/execution ledgers and dispatch, plus a durable idempotent local
  outbox and operator-owned halt marker.
- Added a real optional LangGraph integration, runnable HTTP demo, separately
  reviewed human approval CLI, and explicit Docker filesystem/network isolation.
- Added measured functional scenarios for forbidden/permitted actions, approval
  gates, concurrency, restart and partial failure. Scope and denominators are
  published in `docs/EVALUATION.md`; no LLM-quality or independent-audit claim.
- Added hashed dependency locks, pinned container base, reproducible alpha build
  tooling and a review guide. Corrected stale "no tags" prose: `v0.2.0-alpha`
  already exists.

The gateway protects only its allowlisted tools when separately deployed. It
does not make embedded execution immune to co-resident code, validate legal
compliance, or provide an externally witnessed audit chain.

### Earlier untagged change notes

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
- **Signed Evidence Attestation** (`gap_kernel/world_model/attestation.py`) —
  `EvidenceAttestation`, `sign_attestation()`, `verify_attestation()`,
  `EvidenceVerifier` and `InProcessEvidenceSigner`. An Ed25519 signature by a
  registered issuer binds one entity's governance-relevant values, its window,
  and the issuing key id; the kernel verifies it against issuers resolved from
  the trust root. Values are compared as canonical JSON, never with `==` —
  `True == 1` in Python, and that difference is the boolean the GDPR gate turns
  on.
- `evidence_issuers` in the trust root (`provision_trust_root`, `load_trust_root`,
  `TrustRoot.evidence_issuer_registry()`), and `GovernanceKernel(evidence_issuers=…)`
  for the in-process posture. A trust root, where present, overrides anything the
  agent-side config blob carries.
- `EvidenceChannel` in the world-model store, plus an optional in-process signer
  for the prototype path, and a readable log of every governance-relevant
  mutation (`GET /world/evidence-mutations`).
- `.github/workflows/ci.yml` — tests on Python 3.11–3.13 plus Windows, a 90%
  coverage floor, `ruff`, and a job that runs the README's exact quickstart
  commands so the documented install path cannot rot.
- `docs/THREAT_MODEL.md` (names the adversary and what GAP does *not* defend
  against), `docs/KNOWN_GAPS.md` (what is broken right now), `docs/ROADMAP.md`.
- `SECURITY.md` rewritten as a real policy: reporting process, scope, response
  targets, safe harbour, and an honest status header.
- An `[api]` optional extra, so the kernel core installs with three dependencies.
- Test suite 389 → 639 tests, coverage 93%.

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
  properties now require a Signed Evidence Attestation the kernel verifies
  itself, and a governed kernel treats an unverifiable value as unevaluable — a
  violation.
- **The evidence attestation stamp was an unsigned dict.** The first fix stamped
  provenance in `WorldModelStore` and had the kernel read a boolean off the
  entity. That guarded the ingest path and nothing else: a caller that assembles
  the `WorldModel` itself never passes through the store, and in the isolated
  posture the agent legitimately authors the whole `world_state` field of the
  `evaluate` RPC request. Reproduced against a governed kernel, a hand-written
  `{"attested": true, "governance_properties": ["gdpr_consent", "geo"]}` turned
  `rejected ['gdpr_consent_required']` into `approved []` — **no code execution
  required.** Evidence is now signed by an issuer key the agent side does not
  hold and verified kernel-side against a registry resolved from the trust root.
  What that buys is a property of the DEPLOYMENT: if the issuer private key sits
  on the agent's host under the agent's OS user, the agent reads it and mints
  whatever it likes. It hardens two of nine evaluators; it does not prove a
  signed fact is true, does not close replay or staleness (the kernel's max-age
  ceiling bounds them), and provides no revocation.
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

9. **Governance-relevant world-model properties require a SIGNED attestation in
   governed mode.** `gdpr_consent`, `geo`, `jurisdiction` and `local_hour` that
   are not covered by a valid Signed Evidence Attestation are unevaluable, which
   is a violation — the same verdict as a constraint with no registered
   evaluator.

   The previous release accepted a plain `_evidence_provenance` dict on the
   entity as proof of attestation, and the kernel read a boolean off it. That
   was forgeable by anyone who could supply a `WorldModel` — which, in the
   isolated posture, is the agent, because it authors the whole `world_state`
   field of an `evaluate` request. Measured: `rejected ['gdpr_consent_required']`
   became `approved []` with a hand-written stamp, no code execution required.
   *What to do:* have the source of record sign an `EvidenceAttestation` over the
   entity's governance-relevant values and attach it under
   `_evidence_attestation`; register the issuer's PUBLIC key in the trust root's
   `evidence_issuers` map (`provision_trust_root(dir, profile_keys,
   evidence_issuers={...})`) or pass `GovernanceKernel(evidence_issuers=...)` in
   process. To keep the old behaviour deliberately, construct the kernel with
   `GovernanceKernel(require_attested_evidence=False)`.

   This hardens **two of the kernel's nine evaluators** — `gdpr_consent_required`
   and `no_contact_outside_hours`, the only two that read the world model. The
   other seven rule on agent-authored `action.parameters` / `estimated_cost` and
   are unchanged.

10. **A governed kernel with no evidence issuers rejects every world-model-backed
    proposal.** `GovernanceKernel(governed=True, applicability_profile=...)` with
    no `evidence_issuers` and no trust-root issuer map requires attestation and
    has nothing that can verify one. This is correct fail-closed behaviour and it
    will surprise prototype deployments; the kernel logs it loudly at
    construction ("governed kernel has no evidence issuers; every
    world-model-backed constraint will be unevaluable").
    *What to do:* supply issuers, or pass `require_attested_evidence=False` for
    prototyping.

11. **`evidence_is_attested()` and `attested_properties()` are deleted from
    `gap_kernel.world_model.store`.** Both read an unsigned claim off the entity,
    which is the defect in functional form: a pure function over agent-supplied
    data can only ever return what the agent sent. Verification now lives on
    `EvidenceVerifier`, which the kernel constructs from issuer keys it resolved
    itself.
    *What to do:* build an `EvidenceVerifier(issuer_registry)` and call
    `.attests(entity, key)`. The kernel does this internally; callers rarely need
    to.

12. **`WorldModelStore.upsert_entity()` no longer discards a caller-supplied
    attestation — it carries it.** The old behaviour existed only while the stamp
    itself was the trust decision. In production the blob is minted outside the
    process by the source of record and the store has no issuer registry to check
    it with, so it is shape-checked and carried verbatim. Carrying it is safe
    because verification is entirely kernel-side.
    *What to do:* nothing, unless you asserted on the discard. The store's own
    `_evidence_provenance` breadcrumb survives as an audit record with no bearing
    on any verdict, and its `attested` boolean is gone.

13. **`EntityProperties` no longer edits the provenance stamp on a direct write.**
    Value binding subsumes it: a write that CHANGES a governance value already
    fails the verifier's value check, and a write setting the SAME value is a
    no-op that should not revoke anything. The warning log and the store's
    mutation audit trail are unchanged. (On the RPC path this code never fired
    anyway — `EntityState._guard_properties` rebuilds properties via a plain
    dict initialization that bypasses `__setitem__`.)

14. **Runtime dependencies pruned and the REST surface moved to an extra.**
    `langgraph`, `langchain-core`, `langchain-anthropic` and `langchain-openai`
    are removed. `fastapi` and `uvicorn` are no longer installed by
    `pip install gap-kernel`.
    *What to do:* `pip install "gap-kernel[api]"` if you import
    `gap_kernel.api`, and install the langchain packages directly if your agent
    used them.

[Unreleased]: https://github.com/Nexeom/General-Autonomy-Protocol/commits/main
