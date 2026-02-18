"""
Learning Engine — Operational and Normative learning.

The Iron Rule: Learning may modify strategy weights, never policy boundaries.

Operational Learning (Automatic):
- Caches known constraint patterns
- Reorders strategy generation based on past successes
- Avoids previously rejected action patterns

Normative Learning (Human-Approved Only):
- Surfaces PolicyProposals when policy boundaries should change
- Never auto-applies policy changes
"""

from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

from gap_kernel.models.governance import GovernanceDecision, GovernanceVerdict
from gap_kernel.models.learning import OperationalHeuristic, PolicyProposal
from gap_kernel.models.lineage import LineageRecord


class LearningEngine:
    """
    Manages operational heuristics and policy proposals.
    Enforces the Iron Rule.
    """

    def __init__(self):
        self._heuristics: Dict[str, OperationalHeuristic] = {}
        self._policy_proposals: Dict[str, PolicyProposal] = {}

    # --- Operational Learning (Automatic) ---

    def learn_from_lineage(self, record: LineageRecord) -> Optional[OperationalHeuristic]:
        """
        Extract operational heuristics from a completed lineage record.
        Looks for patterns in rejections that could inform future strategy generation.
        """
        if record.total_attempts <= 1:
            return None  # Nothing to learn from single-attempt success

        # Analyze rejection patterns
        rejections = [
            d for d in record.governance_decisions
            if d.verdict == GovernanceVerdict.REJECTED
        ]

        if not rejections:
            return None

        # Extract constraint violation patterns
        for rejection in rejections:
            for constraint_name in rejection.violated_constraints:
                pattern = self._extract_pattern(record, constraint_name)
                if pattern:
                    existing = self._find_matching_heuristic(pattern)
                    if existing:
                        existing.hit_count += 1
                        if record.execution_success:
                            # Update success rate with exponential moving average
                            existing.success_rate = (
                                0.8 * existing.success_rate + 0.2 * 1.0
                            )
                    else:
                        heuristic = OperationalHeuristic(
                            id=f"heur_{uuid4().hex[:12]}",
                            pattern=pattern,
                            source_lineage_ids=[record.id],
                            hit_count=1,
                            success_rate=1.0 if record.execution_success else 0.0,
                            learned_at=datetime.utcnow(),
                        )
                        self._heuristics[heuristic.id] = heuristic
                        return heuristic

        return None

    def get_heuristics_for_context(self, context: dict) -> List[OperationalHeuristic]:
        """
        Get relevant heuristics for a given strategy generation context.
        Returns heuristics sorted by relevance (hit_count * success_rate).
        """
        relevant = []
        for h in self._heuristics.values():
            if h.status != "active":
                continue
            # Simple keyword matching for prototype
            if self._heuristic_matches_context(h, context):
                relevant.append(h)

        return sorted(
            relevant,
            key=lambda h: h.hit_count * h.success_rate,
            reverse=True,
        )

    def get_all_heuristics(self) -> List[OperationalHeuristic]:
        """Get all operational heuristics."""
        return list(self._heuristics.values())

    def _extract_pattern(self, record: LineageRecord, constraint_name: str) -> Optional[str]:
        """Extract a reusable pattern from a constraint violation."""
        # Look at the world state to find context-specific patterns
        snapshot = record.world_state_snapshot
        entities = snapshot.get("entities", {})

        for entity_id, entity_data in entities.items():
            props = entity_data.get("properties", {})
            geo = props.get("geo", props.get("jurisdiction", ""))

            if constraint_name == "gdpr_consent_required" and geo:
                return f"geo:{geo} → prepend consent_verification"
            elif constraint_name == "no_contact_outside_hours":
                local_hour = props.get("local_hour")
                if local_hour is not None:
                    return f"local_hour:{local_hour} → defer_or_route_to_human"

        return f"constraint:{constraint_name} → check_before_action"

    def _find_matching_heuristic(self, pattern: str) -> Optional[OperationalHeuristic]:
        """Find an existing heuristic that matches the pattern."""
        for h in self._heuristics.values():
            if h.pattern == pattern:
                return h
        return None

    def _heuristic_matches_context(self, heuristic: OperationalHeuristic, context: dict) -> bool:
        """Check if a heuristic is relevant to the current context."""
        pattern = heuristic.pattern
        world_state = context.get("world_state", {})
        entities = world_state.get("entities", {}) if isinstance(world_state, dict) else {}

        # Check geo-based patterns
        if pattern.startswith("geo:"):
            geo_val = pattern.split(":")[1].split(" ")[0]
            for entity_data in entities.values():
                props = entity_data if isinstance(entity_data, dict) else {}
                if isinstance(props, dict):
                    props = props.get("properties", props)
                entity_geo = props.get("geo", props.get("jurisdiction", ""))
                if entity_geo.upper() == geo_val.upper():
                    return True

        return False

    # --- Normative Learning (Human-Approved Only) ---

    def propose_policy_change(
        self,
        proposed_change: str,
        rationale: str,
        supporting_lineage_ids: List[str],
        risk_assessment: str,
    ) -> PolicyProposal:
        """
        Surface a policy change proposal for human review.
        The Iron Rule: This NEVER auto-applies.
        """
        proposal = PolicyProposal(
            id=f"pprop_{uuid4().hex[:12]}",
            proposed_change=proposed_change,
            rationale=rationale,
            supporting_lineage_ids=supporting_lineage_ids,
            risk_assessment=risk_assessment,
        )
        self._policy_proposals[proposal.id] = proposal
        return proposal

    def get_pending_proposals(self) -> List[PolicyProposal]:
        """Get all pending policy proposals."""
        return [
            p for p in self._policy_proposals.values()
            if p.status == "pending_review"
        ]

    def get_all_proposals(self) -> List[PolicyProposal]:
        """Get all policy proposals."""
        return list(self._policy_proposals.values())

    def approve_proposal(self, proposal_id: str, reviewer: str) -> Optional[PolicyProposal]:
        """Human approves a policy proposal."""
        proposal = self._policy_proposals.get(proposal_id)
        if proposal and proposal.status == "pending_review":
            proposal.status = "approved"
            proposal.reviewed_by = reviewer
            proposal.reviewed_at = datetime.utcnow()
            return proposal
        return None

    def reject_proposal(self, proposal_id: str, reviewer: str) -> Optional[PolicyProposal]:
        """Human rejects a policy proposal."""
        proposal = self._policy_proposals.get(proposal_id)
        if proposal and proposal.status == "pending_review":
            proposal.status = "rejected"
            proposal.reviewed_by = reviewer
            proposal.reviewed_at = datetime.utcnow()
            return proposal
        return None

    def detect_policy_improvement_opportunity(
        self, records: List[LineageRecord]
    ) -> Optional[PolicyProposal]:
        """
        Analyze lineage records for patterns that suggest a policy
        boundary should be reviewed by a human.

        This is normative learning — the system proposes, humans decide.
        """
        # Look for high escalation rates on specific constraints
        escalation_counts: Dict[str, int] = {}
        total_counts: Dict[str, int] = {}

        for record in records:
            for decision in record.governance_decisions:
                for constraint in decision.violated_constraints:
                    total_counts[constraint] = total_counts.get(constraint, 0) + 1
                    if record.escalated_to_human:
                        escalation_counts[constraint] = escalation_counts.get(constraint, 0) + 1

        # If a constraint causes >50% escalation rate with enough data,
        # suggest review
        for constraint_name, total in total_counts.items():
            if total >= 5:  # Minimum sample size
                esc_count = escalation_counts.get(constraint_name, 0)
                if esc_count / total > 0.5:
                    return self.propose_policy_change(
                        proposed_change=(
                            f"Review constraint '{constraint_name}': "
                            f"High escalation rate ({esc_count}/{total} = "
                            f"{esc_count/total:.0%})"
                        ),
                        rationale=(
                            f"Constraint '{constraint_name}' is causing "
                            f"frequent escalations to human. The strategy "
                            f"layer cannot find compliant alternatives in "
                            f"most cases."
                        ),
                        supporting_lineage_ids=[r.id for r in records[:10]],
                        risk_assessment=(
                            "Modifying this constraint could reduce "
                            "human escalation workload but may weaken "
                            "governance guardrails."
                        ),
                    )

        return None
