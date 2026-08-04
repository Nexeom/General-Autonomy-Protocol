# Security Policy

## Project status — read this first

GAP is a **pre-release reference implementation**. `pyproject.toml` declares
version `0.2.0-alpha`. There are no tagged releases, no published packages, and
no external security review. The repository has one author.

The governance kernel enforces real controls — kernel-signed decisions,
single-use authorizations, a signed regulatory floor, a fail-closed evaluator —
and those controls are adversarially tested. It also has a documented boundary
it does not cross: **an adversary with code execution inside the agent process
bypasses enforcement without forging anything.** That is not a bug report; it is
the current architecture, described in
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

Do not deploy GAP as the sole control on a system that can cause harm. Read
[docs/CONFORMANCE.md](docs/CONFORMANCE.md) for what is built and verified versus
specified, and the threat model for what each deployment posture actually buys.

## Reporting a vulnerability

Email **security@nexeom.ca**. Do not open a public GitHub issue, discussion, or
pull request for an unfixed vulnerability.

There is no published PGP key. If you need an encrypted channel, say so in a
first message containing no vulnerability detail and one will be arranged.

### What to include

A report is actionable when it contains:

- **The commit SHA or branch** you tested against.
- **Which deployment posture.** Open/prototype, governed in-process
  (`isolated=False`), or governed + isolated subprocess (`isolated=True`, the
  default). This decides whether the behavior is a finding at all — the open
  posture is permissive by design.
- **A reproduction.** A minimal script or, best, a failing pytest case. The
  existing adversarial suites (`tests/test_adversarial.py`,
  `tests/test_replay_protection.py`, `tests/test_decision_integrity.py`,
  `tests/test_boundary_hardening.py`) are the house style.
- **What the attacker gains** — an executed action that governance rejected, a
  forged or replayed authorization, a constraint evaluated as satisfied when it
  is not, an audit record altered undetected, a leak of kernel internals across
  the RPC boundary.
- **What the attacker must already hold** to run it. A report that assumes code
  execution in the agent process is describing a documented limitation, not a
  new finding — see Scope below.
- Your assessment of severity, and a suggested fix if you have one.

### What not to do

- Do not disclose publicly — issue, discussion, PR, gist, blog, or social post —
  before a fix is on `main` or the coordination window below has elapsed. A pull
  request that fixes an unreported vulnerability *is* the disclosure; report
  first.
- Do not test against systems you do not own or have written permission to test.
- Do not access, alter, or exfiltrate data that is not yours. If you encounter
  third-party data during research, stop and say so in your report.
- Do not run denial-of-service, spam, or social-engineering tests against any
  person or host.

## Supported versions

There are no tagged releases and nothing is published to PyPI, so there is
exactly one supported thing: the `main` branch.

| Version | Status | Receives security fixes |
|---|---|---|
| `main` (currently `0.2.0-alpha`) | Active development; the only supported target | Yes — fixes land here |
| Any fork, vendored copy, or checkout of an older commit | Unsupported | No — rebase onto `main` |

Supported runtime: **Python 3.11+**, per `requires-python` in `pyproject.toml`.
CI runs the suite on 3.11, 3.12, and 3.13 on Linux and on 3.13 on Windows.
Behavior outside that matrix is untested.

Because there is no release channel, a security fix reaches you only when you
pull `main`. If you are running GAP anywhere that matters, watch the repository.

## Response targets

| Stage | Target |
|---|---|
| Acknowledge that your report arrived | 3 days |
| Initial assessment — in scope, reproduced, severity | 10 days |
| Fix or documented mitigation on `main` for high/critical findings | 30 days |
| Fix, or a documented decision not to fix, for everything else | 90 days |

**These are targets, not contractual SLAs.** This is a single-maintainer project
with no on-call rotation. If you have heard nothing after the acknowledgement
window, send a follow-up to the same address; assume the first message was lost
rather than ignored.

## Scope

### In scope

A working demonstration of any of the following, against the governed postures:

- Producing a `GovernanceDecision` the `ExecutionFabric` accepts without holding
  the kernel's private signing key — forgery, signature-verification bypass,
  canonicalization ambiguity, or domain-tag confusion.
- Executing a decision twice: defeating the `ExecutionLedger` nonce claim, the
  `expires_at` window, the `OOBLedger` approval reservation, or the per-action
  completion tracking that makes a retry safe.
- Executing a proposal other than the one a decision authorizes — defeating the
  `proposal_id` or `proposal_digest` binding.
- Causing a governed kernel to verify an Applicability Profile against a key the
  trust root does not name, or to accept a tampered profile.
- Causing a HARD constraint or a Tier-1 regulatory-floor constraint to evaluate
  as satisfied when it is not, using only data an integrator or a caller can
  supply — an unattested world-model value treated as evidence, a constraint
  silently dropped, a threshold lost in transit.
- Registering or altering an action type at runtime against a governed kernel,
  or reaching the Action Type Registry through the RPC or HTTP surface.
- Input across the subprocess RPC boundary that crashes or hangs the kernel,
  exhausts its memory, leaks kernel internals (paths, field names, exception
  types) into an error response, or lets one caller receive another caller's
  signed decision.
- Altering, deleting, or reordering a lineage record without
  `verify_chain_integrity()` returning false.
- Dispatching or planning while the kill switch is engaged for the relevant
  scope, including by retargeting around a per-entity halt.
- Amplifying authority through sub-agent delegation past the parent's ceiling,
  or escaping a halt that should propagate down the delegation subtree.
- Reaching a state-changing operation over HTTP when
  `enable_mutating_routes=False`.
- A vulnerability in a runtime dependency (`pydantic`, `croniter`,
  `cryptography`) reachable from the kernel path.

### Out of scope

These are documented properties of the current implementation, not findings.
Each is described in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) with the
deployment control that addresses it.

- **Anything requiring code execution inside the agent process.** The
  `ExecutionFabric` that verifies kernel signatures, the executor registry, the
  kill switch, the world model, and the lineage store all live there. Overwriting
  an attribute to disable verification is expected to work; that is the boundary,
  and moving it is architecture work, not a patch.
- **The open/prototype posture.** `allow_unsigned_decisions=True`,
  `require_independent_trust_root=False`, `allow_ephemeral_ledgers=True`,
  `isolated=False`, `GovernanceKernel()` with no profile, and the module-level
  `gap_kernel.api.app:app` are named escape hatches. Each one warns or is
  documented as non-enforcing.
- **The REST API has no authentication and never will.** Mutating routes are off
  by default; enabling them asserts that an authenticated proxy sits in front.
- **Paraphrase evasion of `RuleBasedIndependentClassifier`.** It is a substring
  keyword scan; renaming an action defeats it. It is a reference implementation
  of a pluggable interface, not a control.
- **No external witness for the lineage chain.** The chain is signed by the
  process that owns the database and the anchor row lives in the same SQLite
  file.
- **The mock executors in `ExecutionFabric`.** They are prototype stand-ins.
- **The free-text `engaged_by` / `disengaged_by` on `KillSwitch`**, and its
  in-memory audit log.
- Automated scanner output with no working proof of concept.
- Cryptographic preference arguments with no concrete break (e.g. "use a
  different curve").

**If you think one of the out-of-scope items is exploitable in a way the threat
model does not cover — in particular, a bypass of the isolated posture that does
*not* require code execution in the agent process — that is in scope. Send it.**

## Safe harbour

If you research in good faith and follow this policy, Nexeom will not pursue
legal action against you, will not report you to your hosting or network
provider, and will treat your testing as authorized.

This covers this repository and code you run on infrastructure you control. It
cannot grant you authorization over anyone else's systems or data, and it does
not apply if you access third-party data, disrupt a live service, or disclose
outside the coordination window.

## Disclosure and credit

- Please hold public detail until a fix is on `main`, or until 90 days after
  acknowledgement, whichever comes first.
- You will be credited by name or handle in the fixing commit and in the notes
  for whatever release first carries the fix, unless you ask to stay anonymous.
- **There is no bug bounty.** There is no budget for one, and saying otherwise
  would be the kind of overclaim this project exists to avoid.

## Related documents

- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — the adversary, the trust
  boundaries, what each posture buys, and what is delegated to deployment.
- [docs/CONFORMANCE.md](docs/CONFORMANCE.md) — per-capability status: built and
  verified, partial, or normative/planned.
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and code standards.
