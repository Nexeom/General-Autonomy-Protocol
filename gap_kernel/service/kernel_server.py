"""Governance Kernel service — the kernel behind a constrained request/response API.

This is the structural half of Fix 2 (G-2): the Governance Kernel — with its
private signing key and its policy/registry state — runs as a service reached
ONLY through this narrow API (evaluate a proposal, fetch the public key). A
governed agent on the other side of the boundary never holds the kernel object,
its private key, or its registry, so it cannot read, modify, or forge governance
— it can only request a decision and verify the signature.

The service is transport-agnostic (``handle`` maps a request dict to a response
dict); ``serve_stdio`` runs it as a subprocess over newline-delimited JSON, and
``GovernanceClient`` implementations (gap_kernel/client) consume it.

The kernel's TRUST ROOT — the keys that verify the signed Applicability Profile,
the keys that verify a Signed Evidence Attestation, and the identity the kernel
signs decisions with — is resolved here from a deployer-owned path
(``GAP_TRUST_ROOT``), never from the config the agent-side parent hands down.
See :class:`TrustRoot`.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from dataclasses import dataclass, field
from typing import Dict, Optional, TextIO, Tuple

from pydantic import ValidationError

from gap_kernel.crypto.signing import (
    PublicKeyRegistry,
    generate_keypair,
    sign,
    verify,
)
from gap_kernel.errors import GovernanceConfigError
from gap_kernel.governance.kernel import GovernanceKernel
from gap_kernel.governance.profile import ApplicabilityProfile
from gap_kernel.models.intent import IntentVector
from gap_kernel.models.strategy import StrategyProposal
from gap_kernel.models.world import WorldModel

logger = logging.getLogger("gap_kernel.service")

# The deployer names the trust root through the environment the kernel process
# is launched into. It is deliberately NOT a command-line argument and NOT a
# field in the config blob: both of those are authored by the agent-side parent,
# and a trust root the parent authors is not a trust root.
TRUST_ROOT_ENV = "GAP_TRUST_ROOT"

TRUST_ROOT_FILENAME = "trust_root.json"
KERNEL_IDENTITY_FILENAME = "kernel_identity.json"

# Proof-of-possession domain: signing this with the identity's private key and
# verifying against the pinned public key proves the two belong together without
# needing a key-derivation primitive outside gap_kernel.crypto.signing.
_IDENTITY_PROOF_DOMAIN = "gap.kernel_identity.v1:"

# The agent side frames one request per line. A longer line is refused without
# being buffered — otherwise the side this boundary exists to contain can drive
# the governance authority out of memory.
MAX_REQUEST_CHARS = 4 * 1024 * 1024

# Errors cross as stable codes. The detail — exception type, pydantic field
# names, filesystem paths — is logged on the kernel side and never sent to the
# agent side, which must not learn the kernel's internals from its failures.
ERROR_UNKNOWN_METHOD = "unknown_method"
ERROR_INVALID_REQUEST = "invalid_request"
ERROR_REQUEST_TOO_LARGE = "request_too_large"
ERROR_INTERNAL = "internal_error"


class TrustRootError(GovernanceConfigError):
    """Raised when the independently-deployed trust root is absent or unusable.

    A subclass of :class:`GovernanceConfigError`: an unresolvable trust root is a
    governed deployment running without its required configuration, so it fails
    closed on the same path as a missing regulatory floor.
    """


@dataclass(frozen=True)
class TrustRoot:
    """The kernel's independently-deployed trust root.

    Three things a governed kernel must NOT take from the agent-side parent:

      * ``profile_keys`` — the public keys an Applicability Profile may be signed
        by. Taking these from the same party that supplies the profile makes
        verification circular: the profile is checked against its own author's
        key, so any profile the parent chooses verifies.
      * ``evidence_issuers`` — the public keys a Signed Evidence Attestation may
        be signed by. Identical reasoning: the agent side authors the whole
        ``world_state`` field of an ``evaluate`` request, so an issuer registry
        it also supplies would verify whatever it chose to sign.
      * the kernel identity (``kernel_public_key_hex`` + the private key at
        ``kernel_identity_path``) — persisted so the key an auditor pins is the
        same key across restarts, instead of one minted per process.

    The file lives on a path the deployer owns and names through
    ``GAP_TRUST_ROOT``; the kernel process reads it directly.
    """

    path: str
    profile_keys: Dict[str, str]
    kernel_public_key_hex: str
    kernel_identity_path: str
    evidence_issuers: Dict[str, str] = field(default_factory=dict)

    def profile_key_registry(self) -> PublicKeyRegistry:
        """The ONLY registry a trust-rooted kernel verifies profiles against."""
        return PublicKeyRegistry(dict(self.profile_keys))

    def evidence_issuer_registry(self) -> PublicKeyRegistry:
        """The ONLY registry a trust-rooted kernel verifies evidence against.

        An empty map is a valid, fail-closed state: the kernel accepts no
        attestation at all, so every world-model-backed constraint is
        unevaluable and therefore violated. That is deliberately louder than
        falling back to something the agent side supplied.
        """
        return PublicKeyRegistry(dict(self.evidence_issuers))

    def load_kernel_identity(self) -> Tuple[str, str]:
        """Return ``(private_key_hex, public_key_hex)`` for the pinned identity.

        Fails closed if the private key does not correspond to the pinned public
        key: an identity that cannot satisfy its own pin is unevaluable, and the
        kernel would otherwise sign decisions under a key nobody pinned.
        """
        identity = _read_json(self.kernel_identity_path, "kernel identity")
        private_key_hex = identity.get("private_key_hex")
        if not isinstance(private_key_hex, str) or not private_key_hex:
            raise TrustRootError(
                f"Kernel identity '{self.kernel_identity_path}' has no "
                f"'private_key_hex'; the kernel cannot sign as its pinned identity."
            )
        proof = _IDENTITY_PROOF_DOMAIN + self.kernel_public_key_hex
        try:
            matches = verify(self.kernel_public_key_hex, proof, sign(private_key_hex, proof))
        except ValueError as exc:
            raise TrustRootError(
                f"Kernel identity '{self.kernel_identity_path}' is malformed: {exc}"
            ) from exc
        if not matches:
            raise TrustRootError(
                f"Kernel identity '{self.kernel_identity_path}' does not match the "
                f"public key pinned in trust root '{self.path}'."
            )
        return private_key_hex, self.kernel_public_key_hex


def _read_json(path: str, what: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise TrustRootError(f"Cannot read {what} at '{path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TrustRootError(f"The {what} at '{path}' is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TrustRootError(f"The {what} at '{path}' must be a JSON object.")
    return data


def trust_root_path() -> Optional[str]:
    """The deployer-named trust root path, or ``None`` when none is configured."""
    return os.environ.get(TRUST_ROOT_ENV) or None


def load_trust_root(path: Optional[str] = None) -> TrustRoot:
    """Load the trust root named by ``GAP_TRUST_ROOT`` (or an explicit ``path``).

    Raises :class:`TrustRootError` when none is configured or the file cannot be
    used. There is no fallback to caller-supplied keys: falling back would hand
    the trust decision straight back to the party the trust root exists to
    exclude.
    """
    resolved = path or trust_root_path()
    if not resolved:
        raise TrustRootError(
            f"No independently-deployed trust root: {TRUST_ROOT_ENV} is not set. "
            f"The governance kernel would otherwise verify the Applicability "
            f"Profile against a key supplied by the same process that supplied "
            f"the profile. Provision one with provision_trust_root(), or pass "
            f"require_independent_trust_root=False for prototyping only."
        )
    data = _read_json(resolved, "trust root")

    profile_keys = data.get("profile_keys")
    if not isinstance(profile_keys, dict) or not profile_keys:
        raise TrustRootError(
            f"Trust root '{resolved}' declares no 'profile_keys'; no Applicability "
            f"Profile could be verified against it."
        )
    if not all(isinstance(k, str) and isinstance(v, str) and v for k, v in profile_keys.items()):
        raise TrustRootError(
            f"Trust root '{resolved}' has a malformed 'profile_keys' map."
        )

    kernel_public_key_hex = data.get("kernel_public_key_hex")
    if not isinstance(kernel_public_key_hex, str) or not kernel_public_key_hex:
        raise TrustRootError(
            f"Trust root '{resolved}' pins no 'kernel_public_key_hex'; the kernel's "
            f"identity would be whatever the process minted at start-up."
        )

    # Optional: a deployment that carries no evidence issuers fails closed on
    # every world-model-backed constraint rather than refusing to boot, so the
    # absence of the key is a posture and not a configuration error.
    evidence_issuers = data.get("evidence_issuers") or {}
    if not isinstance(evidence_issuers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v
        for k, v in evidence_issuers.items()
    ):
        raise TrustRootError(
            f"Trust root '{resolved}' has a malformed 'evidence_issuers' map."
        )

    identity_path = data.get("kernel_identity_path")
    if not isinstance(identity_path, str) or not identity_path:
        raise TrustRootError(
            f"Trust root '{resolved}' names no 'kernel_identity_path'."
        )
    # Relative identity paths resolve against the trust root's own directory, so
    # a provisioned trust root is relocatable as a unit.
    if not os.path.isabs(identity_path):
        identity_path = os.path.join(os.path.dirname(os.path.abspath(resolved)), identity_path)

    return TrustRoot(
        path=os.path.abspath(resolved),
        profile_keys=dict(profile_keys),
        kernel_public_key_hex=kernel_public_key_hex,
        kernel_identity_path=identity_path,
        evidence_issuers=dict(evidence_issuers),
    )


def provision_trust_root(
    directory: str,
    profile_keys: Dict[str, str],
    evidence_issuers: Optional[Dict[str, str]] = None,
) -> TrustRoot:
    """Create (or re-read) a trust root in ``directory`` and return it.

    Idempotent in the identity: an existing ``kernel_identity.json`` is kept, so
    re-provisioning a deployment does not silently rotate the key external
    auditors have pinned. The identity file is written 0600 where the platform
    supports it — on a host where the agent process runs as the same user this is
    hygiene, not a boundary; running the kernel as a separate user is what makes
    the private key genuinely unreachable from the agent side.

    ``evidence_issuers`` is the PUBLIC half of each key allowed to sign a Signed
    Evidence Attestation. The same caveat applies with more force: the guarantee
    is a property of where the corresponding PRIVATE keys live, not of this file.
    If an issuer private key sits on this host under the OS user the agent runs
    as, the agent reads it and mints any consent it likes, and the attestation
    certifies nothing that adversary did not already control.

    The deployer then points ``GAP_TRUST_ROOT`` at the returned ``path``.
    """
    os.makedirs(directory, exist_ok=True)
    identity_path = os.path.join(directory, KERNEL_IDENTITY_FILENAME)
    if os.path.exists(identity_path):
        identity = _read_json(identity_path, "kernel identity")
        private_key_hex = identity.get("private_key_hex")
        public_key_hex = identity.get("public_key_hex")
        if not isinstance(private_key_hex, str) or not isinstance(public_key_hex, str):
            raise TrustRootError(
                f"Existing kernel identity '{identity_path}' is malformed; refusing "
                f"to overwrite it — an operator must resolve the identity first."
            )
    else:
        private_key_hex, public_key_hex = generate_keypair()
        _write_json(identity_path, {
            "private_key_hex": private_key_hex,
            "public_key_hex": public_key_hex,
        })
        try:
            os.chmod(identity_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover - platform dependent
            logger.warning("could not restrict permissions on %s", identity_path)

    root_path = os.path.join(directory, TRUST_ROOT_FILENAME)
    _write_json(root_path, {
        "profile_keys": dict(profile_keys),
        "evidence_issuers": dict(evidence_issuers or {}),
        "kernel_public_key_hex": public_key_hex,
        "kernel_identity_path": KERNEL_IDENTITY_FILENAME,
    })
    return load_trust_root(root_path)


def _write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def dump_governed_config(
    applicability_profile: ApplicabilityProfile,
    profile_key_registry: PublicKeyRegistry,
    evidence_issuers: Optional[PublicKeyRegistry] = None,
) -> dict:
    """Serialize the config a subprocess kernel needs to run GOVERNED — the signed
    Applicability Profile plus the (public-key-only) profile key and evidence
    issuer registries. No private key or secret crosses the boundary.

    Both registries here are a PROTOTYPE convenience: they are authored by the
    same process that authors the profile and the world state, so neither can
    establish trust on its own. When the kernel process resolves a trust root,
    both registries in this blob are ignored entirely — see
    :func:`kernel_from_governed_config`.
    """
    return {
        "profile": applicability_profile.model_dump(mode="json"),
        "registry": profile_key_registry.as_dict(),
        "evidence_issuers": (
            evidence_issuers.as_dict() if evidence_issuers is not None else {}
        ),
    }


def kernel_from_governed_config(
    config: dict, trust_root: Optional[TrustRoot] = None
) -> GovernanceKernel:
    """Construct a governed GovernanceKernel from a ``dump_governed_config`` dict.

    With a ``trust_root``, the profile is verified against the trust root's keys,
    evidence is verified against the trust root's issuers, and the kernel signs
    with the trust root's persisted identity; NEITHER registry inside ``config``
    is consulted at all. Without one, the config's own registries are used — the
    prototype posture, in which the caller both supplies and vouches for the
    profile and for the evidence.

    Either way the kernel raises (fail closed) on an unsigned / tampered /
    unknown-key profile, and rejects any attestation it cannot verify.
    """
    profile = ApplicabilityProfile.model_validate(config["profile"])
    if trust_root is not None:
        registry = trust_root.profile_key_registry()
        evidence_issuers = trust_root.evidence_issuer_registry()
        signing_key_hex, public_key_hex = trust_root.load_kernel_identity()
    else:
        registry = PublicKeyRegistry(config.get("registry") or {})
        evidence_issuers = PublicKeyRegistry(config.get("evidence_issuers") or {})
        signing_key_hex = public_key_hex = None
    return GovernanceKernel(
        governed=True,
        applicability_profile=profile,
        profile_key_registry=registry,
        evidence_issuers=evidence_issuers,
        signing_key_hex=signing_key_hex,
        public_key_hex=public_key_hex,
    )


class GovernanceService:
    """Wraps a GovernanceKernel and exposes evaluate, the public key, and
    READ-ONLY views of the action-type registry.

    Nothing here changes governance configuration. The registry arrives inside
    the signed Applicability Profile, so a write across this boundary would be an
    unsigned change to the policy set, made by the exact side the boundary exists
    to contain — an agent could register the type its own forbidden action needs
    and turn a REJECT into a signed APPROVE."""

    def __init__(self, kernel: Optional[GovernanceKernel] = None):
        self._kernel = kernel or GovernanceKernel()

    @property
    def public_key_hex(self) -> str:
        return self._kernel.public_key_hex

    def handle(self, request: dict) -> dict:
        """Map a request dict to a response dict. Never raises across the boundary.

        The response echoes the request's ``id`` when one is present, so a client
        sharing the channel between threads can prove a response answers its own
        request rather than someone else's."""
        if not isinstance(request, dict):
            return {"ok": False, "error": ERROR_INVALID_REQUEST}
        response = self._dispatch(request)
        request_id = request.get("id")
        if request_id is not None:
            response["id"] = request_id
        return response

    def _dispatch(self, request: dict) -> dict:
        method = request.get("method")
        try:
            if method == "get_public_key":
                return {"ok": True, "public_key_hex": self._kernel.public_key_hex}
            if method == "evaluate":
                proposal = StrategyProposal.model_validate(request["proposal"])
                intents = [IntentVector.model_validate(i) for i in request["intents"]]
                world = WorldModel.model_validate(request["world_state"])
                # No clock crosses the boundary: the kernel reads its own, since a
                # caller-named time is a caller-chosen set of active constraints.
                decision = self._kernel.evaluate_proposal(
                    proposal=proposal,
                    intents=intents,
                    world_state=world,
                    action_type_id=request.get("action_type_id"),
                )
                return {"ok": True, "decision": decision.model_dump(mode="json")}
            if method == "list_action_types":
                return {"ok": True, "action_types": {
                    k: v.model_dump(mode="json")
                    for k, v in self._kernel.get_registered_action_types().items()
                }}
            if method == "get_action_type":
                spec = self._kernel.get_action_type(request["type_id"])
                return {"ok": True, "action_type": spec.model_dump(mode="json") if spec else None}
            logger.warning("governance service refused an unknown method: %r", method)
            return {"ok": False, "error": ERROR_UNKNOWN_METHOD}
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            logger.warning("governance service rejected a malformed %r request: %s", method, exc)
            return {"ok": False, "error": ERROR_INVALID_REQUEST}
        except Exception:  # boundary: surface errors as data, never crash the channel
            logger.exception("governance service failed while handling method %r", method)
            return {"ok": False, "error": ERROR_INTERNAL}


def _claim_stdout_for_protocol() -> TextIO:
    """Take exclusive ownership of the real stdout for the protocol framing and
    point ``sys.stdout`` at stderr, so a stray ``print`` anywhere in the kernel
    cannot inject a line into the response stream."""
    protocol_stream = sys.stdout
    sys.stdout = sys.stderr
    return protocol_stream


def _read_request_line(stream_in: TextIO, max_chars: int) -> tuple[str, bool]:
    """Read one newline-terminated request, never buffering more than ``max_chars``.

    Returns ``(line, oversize)``. The remainder of an oversized line is discarded
    in bounded chunks so the following request still frames correctly."""
    line = stream_in.readline(max_chars + 1)
    if len(line) <= max_chars or line.endswith("\n"):
        return line, False
    while True:
        rest = stream_in.readline(max_chars)
        if not rest or rest.endswith("\n"):
            return "", True


def serve_stdio(
    stream_in: Optional[TextIO] = None,
    stream_out: Optional[TextIO] = None,
    kernel: Optional[GovernanceKernel] = None,
    max_request_chars: int = MAX_REQUEST_CHARS,
) -> None:
    """Run the service over newline-delimited JSON on stdin/stdout."""
    stream_in = stream_in or sys.stdin
    stream_out = stream_out or _claim_stdout_for_protocol()
    service = GovernanceService(kernel)
    while True:
        line, oversize = _read_request_line(stream_in, max_request_chars)
        if oversize:
            logger.warning("governance service refused a request over %d chars", max_request_chars)
            response = {"ok": False, "error": ERROR_REQUEST_TOO_LARGE}
        elif not line:
            return
        else:
            line = line.strip()
            if not line:
                continue
            try:
                response = service.handle(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("governance service received a non-JSON request: %s", exc)
                response = {"ok": False, "error": ERROR_INVALID_REQUEST}
        stream_out.write(json.dumps(response) + "\n")
        stream_out.flush()


def _kernel_for_service() -> Optional[GovernanceKernel]:
    """Build the kernel this process will serve.

    The trust root comes from THIS process's environment, so it is established by
    the deployer that launched the kernel rather than by the parent that asked
    for a decision. Everything argv carries — the governed config blob — is
    treated as agent-side data: it names the profile, never the key that verifies
    it. A configured-but-unusable trust root raises here, which exits the process
    before the serve loop starts: no governance runs at all, rather than running
    against a trust root nobody could evaluate.
    """
    root = load_trust_root() if trust_root_path() is not None else None
    if len(sys.argv) > 1 and sys.argv[1]:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            return kernel_from_governed_config(json.load(fh), trust_root=root)
    if root is not None:
        # Open kernel, pinned identity: even without a regulatory floor the
        # decisions this process signs must be attributable to the pinned key.
        signing_key_hex, public_key_hex = root.load_kernel_identity()
        return GovernanceKernel(
            signing_key_hex=signing_key_hex, public_key_hex=public_key_hex
        )
    return None


if __name__ == "__main__":
    # Claim the protocol stream before anything else runs, so nothing on the way
    # to the serve loop can write to it either.
    _protocol_stream = _claim_stdout_for_protocol()
    serve_stdio(stream_out=_protocol_stream, kernel=_kernel_for_service())
