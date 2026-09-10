"""Offline operator provisioning for the reference demo, never an agent API."""
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from gap_kernel._time import utcnow
from gap_kernel.crypto.signing import generate_keypair
from gap_kernel.governance.profile import ApplicabilityProfile, sign_profile
from gap_kernel.models.intent import Constraint, ConstraintType, IntentVector
from gap_kernel.models.world import EntityState, WorldModel, EVIDENCE_ATTESTATION_PROPERTY
from gap_kernel.world_model.attestation import EvidenceAttestation, sign_attestation


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Creation is exclusive: rerunning setup cannot silently replace identities.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value, indent=2))


def provision(directory: str | Path, *, tool_url="http://127.0.0.1:8091"):
    base = Path(directory)
    if base.exists() and any(base.iterdir()):
        raise ValueError("choose a new empty provisioning directory; identities are never overwritten")
    operator = base / "operator"
    gateway = base / "gateway"
    agent = base / "agent"
    sink = base / "sink"
    profile_private, profile_public = generate_keypair()
    issuer_private, issuer_public = generate_keypair()
    kernel_private, kernel_public = generate_keypair()
    approver_private, approver_public = generate_keypair()
    now = utcnow()
    floor = [Constraint(name="gdpr_consent_required", type=ConstraintType.HARD,
                        description="Require attested consent for this demonstration"),
             Constraint(name="cost_ceiling", type=ConstraintType.HARD,
                        description="Maximum $2.00 per proposed batch")]
    profile = sign_profile(ApplicabilityProfile(profile_id="demo", issued_at=now,
                                               tier1_constraints=floor),
                           profile_private, "profile_authority")
    facts = {"geo": "DE", "gdpr_consent": True}
    attestation = sign_attestation(EvidenceAttestation(
        attestation_id=str(uuid4()), entity_id="demo", properties=facts,
        issued_at=now, expires_at=now + timedelta(seconds=300), issuer_key_id="source"),
        issuer_private, "source")
    world = WorldModel(entities={"demo": EntityState(
        entity_type="lead", entity_id="demo",
        properties={**facts, EVIDENCE_ATTESTATION_PROPERTY: attestation.model_dump(mode="json")},
        source="operator_demo_fixture", last_updated=now)}, last_reconciled=now)
    intent = IntentVector(id="demo_intent", objective="Read a local record and record an approved note",
                          priority=50, hard_constraints=[], soft_constraints=[],
                          created_by="operator", created_at=now)
    agent_token, tool_token = secrets.token_hex(32), secrets.token_hex(32)
    for path, content in [
        (operator / "profile-key.json", {"private_key_hex": profile_private}),
        (operator / "evidence-key.json", {"private_key_hex": issuer_private}),
        (operator / "approver.json", {"key_id": "operator", "private_key_hex": approver_private,
                                       "kernel_public_key_hex": kernel_public}),
        (gateway / "kernel_identity.json", {"private_key_hex": kernel_private}),
        (gateway / "trust_root.json", {"profile_keys": {"profile_authority": profile_public},
                                       "evidence_issuers": {"source": issuer_public},
                                       "kernel_public_key_hex": kernel_public,
                                       "kernel_identity_path": "kernel_identity.json"}),
        (gateway / "profile.json", profile.model_dump(mode="json")),
        (gateway / "world.json", world.model_dump(mode="json")),
        (gateway / "intent.json", intent.model_dump(mode="json")),
        (gateway / "agent-token", agent_token), (gateway / "tool-token", tool_token),
        (agent / "agent-token", agent_token), (sink / "tool-token", tool_token),
        (gateway / "config.json", {"trust_root": "trust_root.json", "profile": "profile.json",
                                    "world": "world.json", "intent": "intent.json",
                                    "agent_token_file": "agent-token", "tool_token_file": "tool-token",
                                    "tool_base_url": tool_url, "approvers": {"operator": approver_public},
                                    "targets": ["demo"], "tools": {
                                        "lookup": {"action_type": "query_crm", "risk_score": 1, "cost": 0.01},
                                        "notify": {"action_type": "send_email", "risk_score": 6, "cost": 1.0}}}),
    ]:
        write_private(path, content)
    for role in ("gateway", "sink"):
        (base / "state" / role).mkdir(parents=True, mode=0o700)
    return base


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--tool-url", default="http://127.0.0.1:8091")
    args = parser.parse_args()
    provision(args.directory, tool_url=args.tool_url)
    print("Created separate operator, gateway, agent and sink material. Demo evidence expires in 5 minutes.")


if __name__ == "__main__":
    main()
