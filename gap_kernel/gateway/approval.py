"""Operator-side review and signing; the agent has only the resulting approval."""
import json
from datetime import timedelta
from pathlib import Path

from gap_kernel._time import ensure_utc, utcnow
from gap_kernel.crypto.signing import sign, verify
from gap_kernel.execution.fabric import ExecutionFabric
from gap_kernel.models.governance import GovernanceDecision, canonical_decision_payload
from gap_kernel.models.strategy import StrategyProposal, compute_proposal_digest


def validate_request(response: dict, approver_file: str | Path):
    identity = json.loads(Path(approver_file).read_text())
    decision = GovernanceDecision.model_validate(response["decision"])
    proposal = StrategyProposal.model_validate(response["proposal"])
    if (decision.verdict.value != "approved" or decision.authorization_level is None
            or decision.authorization_level.value != "L2"
            or compute_proposal_digest(proposal) != decision.proposal_digest
            or not verify(identity["kernel_public_key_hex"], canonical_decision_payload(decision),
                          decision.decision_signature or "")):
        raise ValueError("request does not contain an authentic, content-bound L2 decision")
    now = utcnow()
    if decision.expires_at is None or ensure_utc(decision.expires_at) <= now:
        raise ValueError("decision expired")
    if ensure_utc(decision.evaluated_at) > now:
        raise ValueError("decision evaluation is in the future")
    return identity, decision


def sign_approval(response: dict, approver_file: str | Path):
    identity, decision = validate_request(response, approver_file)
    now = utcnow()
    decision = decision.model_copy(update={
        "human_approver_public_key_id": identity["key_id"],
        "human_approval_timestamp": now,
        "human_approval_valid_until": min(ensure_utc(decision.expires_at), now + timedelta(seconds=120)),
    })
    signature = sign(identity["private_key_hex"], ExecutionFabric._oob_signed_message(decision))
    return {"human_approver_public_key_id": identity["key_id"],
            "human_approval_timestamp": now.isoformat(),
            "human_approval_valid_until": decision.human_approval_valid_until.isoformat(),
            "human_approval_signature": signature}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_file")
    parser.add_argument("--identity", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    response = json.loads(Path(args.request_file).read_text())
    validate_request(response, args.identity)
    print(json.dumps(response["proposal"], indent=2))
    if input("Approve exactly these actions? Type APPROVE: ") != "APPROVE":
        raise SystemExit("No approval issued")
    approval = sign_approval(response, args.identity)
    from gap_kernel.gateway.provision import write_private
    write_private(args.output, {"approval": approval})
    print("Approval written; valid only for this decision and its expiry.")


if __name__ == "__main__":
    main()
