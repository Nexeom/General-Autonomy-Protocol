"""Governance Integrity Monitoring (GIM) — Fix 6 skeleton.

An independent, rule-based monitor that watches the *stream of governance
decisions* for the indirect erosion that static gates miss. It deliberately
shares none of the kernel's evaluation logic — it is a separate, deterministic
component over decision metadata (model/key independence, addressing G-3).

Implemented signals:
  * GIM-1 Authorization Drift — an action type being authorized at steadily
    LOWER levels over time (governance erosion).
  * GIM-3 Threshold-Avoidance Decomposition — many low-authorization actions
    against the same target within a short window, i.e. one high-tier action
    split into sub-threshold pieces.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from pydantic import BaseModel

_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


class IntegritySignal(BaseModel):
    """A detected governance-integrity concern."""

    signal_type: str          # "GIM-1" | "GIM-3"
    subject: str              # the action type or target the signal concerns
    severity: str = "warning"
    detail: str = ""
    evidence: dict = {}


class _Observation(BaseModel):
    action_type: str
    rank: int
    target: Optional[str] = None
    at: datetime


class GovernanceIntegrityMonitor:
    """Rule-based GIM detectors over observed governance decisions."""

    def __init__(
        self,
        baseline_window: int = 5,
        recent_window: int = 3,
        drift_threshold: float = 1.0,
        decomposition_window_seconds: int = 300,
        decomposition_count_threshold: int = 3,
        decomposition_max_level: str = "L1",
    ):
        self._by_action: Dict[str, List[_Observation]] = defaultdict(list)
        self._by_target: Dict[str, List[_Observation]] = defaultdict(list)
        self.baseline_window = baseline_window
        self.recent_window = recent_window
        self.drift_threshold = drift_threshold
        self.decomposition_window = timedelta(seconds=decomposition_window_seconds)
        self.decomposition_count_threshold = decomposition_count_threshold
        self.decomposition_max_rank = _RANK[decomposition_max_level]

    # --- ingestion ---------------------------------------------------------

    def observe(
        self,
        action_type: str,
        authorization_level: str,
        target: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> _Observation:
        obs = _Observation(
            action_type=action_type,
            rank=_RANK[authorization_level],
            target=target,
            at=timestamp or datetime.utcnow(),
        )
        self._by_action[action_type].append(obs)
        if target is not None:
            self._by_target[target].append(obs)
        return obs

    def observe_decision(self, decision, target: Optional[str] = None) -> Optional[_Observation]:
        """Convenience: ingest a GovernanceDecision (skips decisions with no level)."""
        if decision.authorization_level is None:
            return None
        return self.observe(
            action_type=decision.action_type_id or "unspecified",
            authorization_level=decision.authorization_level.value,
            target=target,
            timestamp=decision.evaluated_at,
        )

    # --- detectors ---------------------------------------------------------

    def check_authorization_drift(self, action_type: str) -> Optional[IntegritySignal]:
        """GIM-1: flag a sustained DOWNWARD drift in an action type's authorization."""
        history = self._by_action.get(action_type, [])
        if len(history) < self.baseline_window + self.recent_window:
            return None
        ranks = [o.rank for o in history]
        baseline = sum(ranks[: self.baseline_window]) / self.baseline_window
        recent = sum(ranks[-self.recent_window :]) / self.recent_window
        drift = baseline - recent  # positive => authorized lower than before
        if drift >= self.drift_threshold:
            return IntegritySignal(
                signal_type="GIM-1",
                subject=action_type,
                detail=(
                    f"Authorization for '{action_type}' drifted down {drift:.2f} "
                    f"levels (baseline {baseline:.2f} -> recent {recent:.2f})."
                ),
                evidence={"baseline_mean": baseline, "recent_mean": recent, "drift": drift},
            )
        return None

    def check_decomposition(self, target: str) -> Optional[IntegritySignal]:
        """GIM-3: flag many low-authorization actions against one target in a window."""
        history = self._by_target.get(target, [])
        if not history:
            return None
        latest = max(o.at for o in history)
        window_start = latest - self.decomposition_window
        recent_low = [
            o for o in history
            if o.at >= window_start and o.rank <= self.decomposition_max_rank
        ]
        if len(recent_low) >= self.decomposition_count_threshold:
            return IntegritySignal(
                signal_type="GIM-3",
                subject=target,
                detail=(
                    f"{len(recent_low)} low-authorization actions against '{target}' "
                    f"within {self.decomposition_window.seconds}s — possible "
                    f"threshold-avoidance decomposition."
                ),
                evidence={"count": len(recent_low), "max_rank": self.decomposition_max_rank},
            )
        return None

    def scan(self) -> List[IntegritySignal]:
        """Run all detectors over the current observation set."""
        signals: List[IntegritySignal] = []
        for action_type in list(self._by_action):
            signal = self.check_authorization_drift(action_type)
            if signal:
                signals.append(signal)
        for target in list(self._by_target):
            signal = self.check_decomposition(target)
            if signal:
                signals.append(signal)
        return signals
