"""Corrigibility — a human-controlled kill-switch / containment primitive (SA-4).

The audit flagged that GAP had no shutdown/containment semantics beyond a UI
"Hard Stop", and that the CGA disposition ("find a path to yes") is mildly
*anti*-corrigible. This adds a structural answer:

  * A ``KillSwitch`` is engaged and disengaged by HUMAN AUTHORITY, outside the
    governed agent. The agent does not hold it and cannot disengage it (it is
    referenced by the Execution Fabric and the loop, not by the strategy layer).
  * When engaged, the Execution Fabric refuses to dispatch any action (fail
    closed) and the CGA loop refuses to plan or execute — CGA does NOT re-plan a
    path around a halt. A halt stops the system; it is not a constraint to
    negotiate.
  * Every engage/disengage is auditable.

Scope: ``"global"`` halts everything; a specific scope key (e.g. an entity or
tenant id) halts only that scope.

The exception actually raised when execution is attempted under an engaged
switch is :class:`gap_kernel.execution.fabric.KillSwitchEngaged` (a subclass of
``ExecutionError``); it lives with the Execution Fabric where enforcement
happens, to avoid an import cycle with this primitive.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Set


class KillSwitch:
    """Human-controlled containment. Engaging halts execution fail-closed."""

    GLOBAL = "global"

    def __init__(self):
        self._engaged: Set[str] = set()
        self._log: List[dict] = []

    def engage(
        self,
        scope: str = GLOBAL,
        *,
        reason: str = "",
        engaged_by: str = "human_authority",
        at: Optional[datetime] = None,
    ) -> None:
        self._engaged.add(scope)
        self._log.append({
            "event": "engage", "scope": scope, "reason": reason,
            "by": engaged_by, "at": (at or datetime.utcnow()).isoformat(),
        })

    def disengage(
        self,
        scope: str = GLOBAL,
        *,
        disengaged_by: str = "human_authority",
        at: Optional[datetime] = None,
    ) -> None:
        self._engaged.discard(scope)
        self._log.append({
            "event": "disengage", "scope": scope,
            "by": disengaged_by, "at": (at or datetime.utcnow()).isoformat(),
        })

    def is_engaged(self, scope: Optional[str] = None) -> bool:
        """True if globally engaged, or engaged for ``scope`` specifically."""
        if self.GLOBAL in self._engaged:
            return True
        return scope is not None and scope in self._engaged

    @property
    def engaged_scopes(self) -> Set[str]:
        return set(self._engaged)

    @property
    def audit_log(self) -> List[dict]:
        return list(self._log)
