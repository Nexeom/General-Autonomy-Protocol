# Reference tool gateway

The gateway moves policy evaluation, decision verification, approval checks,
durable ledgers and tool credentials into a trusted service. The agent submits
tool intent to a bounded HTTP API. It cannot submit a replacement policy, world
model, risk score, signed decision, executor or dispatch URL through that API.

This is an alpha reference integration for two tools and one demonstration
target. It is not a general-purpose proxy for arbitrary tools. Docker/WSL is
unavailable in the local review environment, so container deployment checks
remain unrun there; see the [Review Guide](REVIEW_GUIDE.md).

## Supported workflow

| Request | Behavior |
|---|---|
| `lookup` for target `demo`, with empty arguments | Read the harmless demonstration record. The trusted catalog assigns its risk and cost. |
| `notify` for target `demo`, with a `message` | Append a note to the protected local outbox after an L2 approval. No email, SMS or other external communication is sent. |
| Unknown tool/target or unsupported arguments | Reject without dispatch. |

The service constructs its own proposal from its trusted catalog and loads
policy, intent and signed world evidence from deployment-owned files. It stores
the proposal and signed decision under a request ID. Execution refers to that
stored request; an agent cannot replace its actions during execution.

An agent token permits proposal submission, request retrieval and execution
requests. It does not grant authority to sign L2 approvals. The separate tool
token is held by the gateway and protected tool; the agent must never receive
it.

## Trust and deployment assumptions

```text
Operator                                  Agent
  profile/evidence signing keys             client token only
  approval private key                      arbitrary code / proposals
  pinned kernel public key                         │
          │ signed profile/evidence/approval        │ allowed API
          ▼                                         ▼
               Trusted gateway
               policy + world + tool catalog
               decision/approval verification
               private kernel key + durable state
               protected tool credential
                         │ fixed endpoint
                         ▼
               Protected local tool
               credential check + durable outbox
               tool-side idempotency
```

The operator, gateway, tool, host administrator and container administrator are
trusted. Isolation must prevent agent code from reading their private files,
joining the protected tool network, changing their configuration or bypassing
their authorization path. The gateway is the enforcement boundary; a separate
agent process running under the same OS user is only a functional test of this
arrangement.

The reference container design uses separate frontend/backend networks,
role-specific read-only configuration mounts, non-root processes, dropped
capabilities and no tool port exposed to the host. The agent belongs only to
the frontend network. The operator key directory is not mounted into the agent
or gateway. These properties must be checked in the actual deployed Compose
configuration; they do not follow merely from installing this package.
Network separation assumes Docker's normal bridge isolation and host firewall
rules. Verify the live topology if daemon or firewall settings differ.

No sandbox claim is made against host/root access, the Docker control socket,
a compromised gateway or a compromised evidence issuer. Do not give the agent
privileged mode, host networking, the Docker socket or shared secret/state
directories. A deployment outside the private demonstration network must also
provide appropriate transport security, service identity, operator identity,
credential rotation and operational controls.

## Local provisioning and service commands

The following commands exercise the service on one machine. They do **not**
isolate it from another process running as the same user. Use a fresh empty
directory: provisioning refuses to replace existing identities.

```bash
python -m pip install -e ".[gateway,langgraph]"
python -m gap_kernel.gateway.provision .gap-demo --tool-url http://127.0.0.1:8091
```

Provisioning creates separate `operator/`, `gateway/`, `agent/` and `sink/`
directories. Keep `operator/` out of the agent environment. For this demonstration
the signed consent evidence expires **five minutes after provisioning**. Start
the services and run the workflow promptly. To repeat after expiry, stop the
services and provision a new directory; do not weaken the freshness check.

In one operator-controlled terminal:

```bash
python -m gap_kernel.gateway.sink --token-file .gap-demo/sink/tool-token --db .gap-demo/sink/outbox.sqlite
```

In another operator-controlled terminal:

```bash
python -m gap_kernel.gateway.app --config .gap-demo/gateway/config.json --state-dir .gap-demo/gateway/state
```

Both services bind loopback by default, at ports 8091 and 8090 respectively.
The service paths above are confirmed by the CLI source. Observed end-to-end
run results must be recorded separately in the review report.

## Manual human approval

These Bash commands illustrate the real operator flow against either a local
gateway or a container gateway published on host loopback. Run them from an
operator-controlled environment, within the evidence validity window. The
private approval key belongs only in that environment.

Use fresh request and output filenames on each run. The example request ID
`manual-demo-1` is single-use; change it for a new attempt after expiry or
completion.

```bash
export GAP_GATEWAY_URL=http://127.0.0.1:8090
export GAP_DEMO_DIR="${GAP_DEMO_DIR:-$(pwd)/.gap-demo}"
export GAP_GATEWAY_TOKEN="$(cat "$GAP_DEMO_DIR/agent/agent-token")"

cat > proposal.json <<'JSON'
{"request_id":"manual-demo-1","actions":[{"tool":"notify","target":"demo","arguments":{"message":"Operator-reviewed demonstration note"}}]}
JSON

curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $GAP_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  --data @proposal.json "$GAP_GATEWAY_URL/v1/proposals" > request.json

python -m gap_kernel.gateway.approval request.json \
  --identity "$GAP_DEMO_DIR/operator/approver.json" --output approval.json
```

The approval CLI verifies the kernel signature and proposal binding using the
operator's pinned kernel public key, displays the proposal and asks for the
literal confirmation `APPROVE`. Review the exact actions before confirming.
Only after confirmation does it write the signed approval file. The agent
receives that signed artifact, never the signing key.

```bash
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $GAP_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  --data @approval.json "$GAP_GATEWAY_URL/v1/requests/manual-demo-1/execute"

curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $GAP_GATEWAY_TOKEN" "$GAP_GATEWAY_URL/v1/audit"
```

Expected behavior is `awaiting_approval` before the human step, one completed
outbox write afterward, and a valid local lineage chain. Executing before
approval must fail; presenting a completed authorization again must fail. An
authentic but expired decision or approval must also fail. Capture actual
responses and tool-side state in the review; the expected behavior here is not
a claim that this manual workflow has been independently tested.

Decisions expire after four minutes in this reference gateway. An approval
expires at the earlier of its decision expiry and two minutes after signing.
Dispatch also reevaluates policy and evidence, so a still-signed decision cannot
override expired evidence. The service does not refresh evidence automatically.

## LangGraph fixture

With the gateway running and only the client token configured, run:

```bash
python examples/langgraph_agent.py --fixture lookup
python examples/langgraph_agent.py --fixture replan
python examples/langgraph_agent.py --fixture approval
```

`lookup` should complete. `replan` first proposes an unapproved target and then
the permitted target. `approval` stops with `awaiting_approval` and exit code
3; this is an expected boundary, not a successful tool execution. Retrieve its
stored request via `GET /v1/requests/{request_id}` and use the operator flow
above to review and approve it if appropriate.

The fixtures are deterministic planners exercising LangGraph state transitions
and the HTTP boundary. A custom `--planner module:callable` is an integration
point for a real model; this repository does not claim model-backed evaluation
from the fixture results.

## Container deployment — not run in the local review

[`deploy/compose.yaml`](../deploy/compose.yaml) defines the network and mount
separation above. Its Dockerfile installs from hashed dependency files, then
installs the package without resolving extra dependencies. **The live container
boundary has not been validated locally because Docker/WSL is unavailable.**
Static configuration inspection and the same-user demo do not replace that
check.

Run the following from a non-root Linux or WSL shell with Docker Compose
available, at the repository root. Use your ordinary host UID/GID so the
non-root services can read their role's restricted files and write their state
directories. Do not set either identity to root. If local services already use
port 8090, stop them before starting this deployment.

```bash
set -euo pipefail
export GAP_DEMO_DIR="$(pwd)/.gap-demo-container"
export GAP_UID="$(id -u)"
export GAP_GID="$(id -g)"

docker compose -f deploy/compose.yaml build
python -m gap_kernel.gateway.provision "$GAP_DEMO_DIR" --tool-url http://sink:8091
docker compose -f deploy/compose.yaml up -d --wait sink gateway

SINK_ID="$(docker compose -f deploy/compose.yaml ps -q sink)"
GAP_TEST_SINK_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$SINK_ID")"
test -n "$GAP_TEST_SINK_IP"
export GAP_TEST_SINK_IP
docker compose -f deploy/compose.yaml exec -T -e GAP_TEST_SINK_IP="$GAP_TEST_SINK_IP" gateway python -c "import os,urllib.request; urllib.request.urlopen('http://' + os.environ['GAP_TEST_SINK_IP'] + ':8091/health', timeout=3)"
docker compose -f deploy/compose.yaml run --rm agent
```

Provisioning creates the writable state directories as well as identities.
Building before provisioning preserves the five-minute evidence window. The
Dockerfile copies an explicit set of metadata, requirements, package source and
examples; deployment material in the documented location is not copied into
the shared image. `.dockerignore` additionally excludes `.gap-demo*` and
`.gap-runs` from the build context. Keep custom secret files outside the copied
source directories.
For another run after expiry, stop this deployment and use a fresh
`.gap-demo-*` directory. The provisioning command refuses to overwrite keys.

The gateway is reachable on host loopback port 8090 for the manual approval
workflow above. The sink has no host port. Preserve the current
`GAP_DEMO_DIR` when following that workflow; do not replace it with the local
demo's directory. Export the agent token from this deployment's `agent/`
directory.

An operator can engage the persistent gateway halt from the host:

```bash
touch "$GAP_DEMO_DIR/state/gateway/halted"
```

New work must be rejected, including execution of a previously prepared
request. Remove that specific marker when the operator decides to resume:

```bash
rm -- "$GAP_DEMO_DIR/state/gateway/halted"
```

To stop the example services while retaining their state:

```bash
docker compose -f deploy/compose.yaml down
```

Record the exact Compose revision, commands and per-check results. A missing
Docker engine must be reported as “not run,” rather than counted as a successful
isolation test.

The boundary check in `examples/container_check.py` inspects unavailable secret
paths, attempts direct tool connectivity, and exercises denied/allowed requests
through the gateway. Supply the tool's exact backend IP as `GAP_TEST_SINK_IP`
for the required network test: a missing or malformed IP fails the check.
The preceding gateway-side health request establishes that this exact address
is live before the agent's denied connection is interpreted as isolation.
Failed DNS alone is insufficient evidence that the tool cannot be reached.
Inspect each returned check as well as the command's exit code.

## Persistence and remaining limits

- Requests, execution claims, approvals and lineage use durable SQLite state.
  Changing policy/configuration/evidence requires reproposal; stored authority
  is bound to its original deployment configuration.
- The demonstration outbox commits its idempotency record and note together.
  This supports safe retry when a response is lost. A different external tool
  needs its own idempotency or reconciliation design; GAP cannot infer that a
  timed-out external side effect never happened.
- The gateway holds both the local lineage store and its signing key. Audit
  verification detects some tampering, but there is no independent witness or
  external append-only retention.
- Evidence authenticity does not establish truth. There is no immediate
  revocation channel; already signed evidence can remain usable until expiry.
- The gateway uses a deployment-owned `halted` file in its state directory.
  An operator with service-host/container filesystem authority can create it
  to reject new proposals and executions; it is checked before each tool
  dispatch. Remove it to permit new work again. The agent API has no toggle.
  This is not an authenticated HTTP operator endpoint or a durable operator
  identity/audit system. An already executing tool call cannot be undone by
  creating the file.
- This API uses one deployment-level agent token, not a multi-tenant identity
  or authorization system. It has bounded request schemas and body size, but
  the reference service does not constitute a production rate-limiting,
  availability or resource-exhaustion defense.

See [CONFORMANCE.md](CONFORMANCE.md) and [KNOWN_GAPS.md](KNOWN_GAPS.md) for the
broader implementation status.
