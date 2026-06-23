"""Operational → governance action-type classification.

The strategy layer emits *operational* action types (``send_email``, ``query_crm``)
on each `PlannedAction`; the Action Type Registry is keyed on *governance*
categories (``task_execution``, ``drift_reconciliation``, ``skill_modification``,
``policy_proposal``…) that carry the authorization defaults. A governed
(strict-typing) kernel needs every proposal to declare a registered governance
action type — so something must bridge the two vocabularies.

`ActionTypeClassifier` does this by the caller's governance *context*: it starts
from a base category (e.g. the reconciler's autonomous actions are
``drift_reconciliation``) and **escalates to a more-restrictive category** when a
proposal contains a sensitive operation (e.g. a skill or policy change). The
most-restrictive applicable category wins — a conservative, fail-safe default.
"""

from __future__ import annotations

from typing import Dict, Optional

from gap_kernel.models.strategy import StrategyProposal

# Restrictiveness rank of the baseline governance categories (by their default
# authorization level). Higher = more restrictive; the classifier picks the max.
_DEFAULT_RANK: Dict[str, int] = {
    "task_execution": 0,
    "escalation": 0,
    "drift_reconciliation": 1,
    "skill_modification": 2,
    "policy_proposal": 4,
}

# Operational action types that imply a MORE-restrictive governance category than
# routine work. Extend per deployment; an operational type absent here does not
# escalate (it stays at the caller's base category).
_DEFAULT_OVERRIDES: Dict[str, str] = {
    "modify_skill": "skill_modification",
    "update_skill": "skill_modification",
    "tune_skill": "skill_modification",
    "propose_policy": "policy_proposal",
    "modify_policy": "policy_proposal",
}


class ActionTypeClassifier:
    """Classify a proposal into a registered governance ``action_type_id``."""

    def __init__(
        self,
        base_action_type: str = "drift_reconciliation",
        overrides: Optional[Dict[str, str]] = None,
        rank: Optional[Dict[str, int]] = None,
    ):
        self._base = base_action_type
        self._overrides = dict(overrides if overrides is not None else _DEFAULT_OVERRIDES)
        self._rank = dict(rank if rank is not None else _DEFAULT_RANK)

    def _restrictiveness(self, action_type_id: str) -> int:
        return self._rank.get(action_type_id, 0)

    def classify(self, proposal: StrategyProposal) -> str:
        """Return the governance action type for ``proposal`` — the most-restrictive
        of the caller's base category and any escalation its actions imply."""
        candidates = [self._base]
        for action in proposal.actions:
            override = self._overrides.get(action.action_type)
            if override:
                candidates.append(override)
        return max(candidates, key=self._restrictiveness)
