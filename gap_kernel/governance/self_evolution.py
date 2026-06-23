"""Self-Evolution Capability-Gain Monitoring (SA-4).

The audit flagged that GAP governs each individual self-modification (a
``skill_modification`` is an L2 action requiring approval) but has NO dynamic
monitor for the *trajectory*: an agent can accrue capability gradually, each
step individually approved, while the cumulative pattern is exactly the
capability gain the Iron Rule exists to bound. This is the self-evolution analog
of GIM's threshold-avoidance decomposition.

``SelfEvolutionMonitor`` is an independent, rule-based detector over the stream of
self-modification decisions, with three signals:

  * SE-VELOCITY — a burst of self-modifications in a short window (runaway
    self-rewriting), measured recency-relative so it self-clears.
  * SE-PRIVILEGE — the authorization level of the agent's self-modifications
    trending UPWARD over time (the agent progressively granting itself
    higher-privilege skills). The mirror of GIM-1's downward authorization drift.
  * SE-SURFACE — the breadth of distinct capabilities (skills) the agent has
    modified within a window crossing a budget (capability-surface expansion).

Honest scoping: like the other GIM detectors this is DETERMINISTIC math over
self-modification metadata the deployment supplies — it does not measure semantic
"capability" (that needs a model-level capability evaluation, Normative/Planned).
What is built is the velocity / privilege-drift / surface math, the thresholds,
and the alerting. The CGA loop feeds it a self-modification only once that
modification has actually **executed** (a realized capability change) — an
approved-but-awaiting-OOB or escalated self-modification gains no capability and
is not counted — so the velocity / surface / privilege counts measure realized
modifications, not proposals. Wiring it consequentially is done in the CGA loop:
an agent already in a flagged capability-gain pattern (from prior realized
modifications) has its next self-modification HELD before it executes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Set

from pydantic import BaseModel

from gap_kernel.governance.integrity_monitor import IntegritySignal, _RANK, _cap

_DEFAULT_SELF_MODIFYING = frozenset({"skill_modification"})


class _Modification(BaseModel):
    action_type: str
    rank: int
    target: Optional[str] = None
    at: datetime


class SelfEvolutionMonitor:
    """Rule-based detectors over the stream of self-modification decisions."""

    def __init__(
        self,
        self_modifying_action_types: Set[str] = _DEFAULT_SELF_MODIFYING,
        # SE-VELOCITY
        velocity_window_seconds: int = 3600,
        velocity_threshold: int = 5,
        # SE-PRIVILEGE (upward authorization drift)
        baseline_window: int = 5,
        recent_window: int = 3,
        privilege_drift_threshold: float = 1.0,
        # SE-SURFACE (distinct modified capabilities in a window)
        surface_window_seconds: int = 86400,
        surface_threshold: int = 5,
        max_history: int = 1024,
    ):
        self._self_modifying = set(self_modifying_action_types)
        self._mods: List[_Modification] = []
        self.velocity_window = timedelta(seconds=velocity_window_seconds)
        self.velocity_threshold = velocity_threshold
        self.baseline_window = baseline_window
        self.recent_window = recent_window
        self.privilege_drift_threshold = privilege_drift_threshold
        self.surface_window = timedelta(seconds=surface_window_seconds)
        self.surface_threshold = surface_threshold
        self._max_history = max(1, max_history)

    # --- ingestion ---------------------------------------------------------

    def is_self_modifying(self, action_type: Optional[str]) -> bool:
        return action_type in self._self_modifying

    def observe(
        self,
        action_type: str,
        authorization_level: str,
        target: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> Optional[_Modification]:
        """Record a self-modification. Non-self-modifying action types are ignored."""
        if action_type not in self._self_modifying:
            return None
        mod = _Modification(
            action_type=action_type,
            rank=_RANK[authorization_level],
            target=target,
            at=timestamp or datetime.utcnow(),
        )
        self._mods.append(mod)
        _cap(self._mods, self._max_history)
        return mod

    def observe_decision(self, decision, target: Optional[str] = None) -> Optional[_Modification]:
        """Convenience: ingest a GovernanceDecision if it is a self-modification."""
        action_type = getattr(decision, "action_type_id", None)
        if action_type not in self._self_modifying or decision.authorization_level is None:
            return None
        return self.observe(
            action_type=action_type,
            authorization_level=decision.authorization_level.value,
            target=target,
            timestamp=decision.evaluated_at,
        )

    # --- detectors ---------------------------------------------------------

    def check_velocity(self) -> Optional[IntegritySignal]:
        """SE-VELOCITY: too many self-modifications within the window (recency-
        relative, so a past burst self-clears)."""
        if not self._mods:
            return None
        newest = max(m.at for m in self._mods)
        band = [m for m in self._mods if newest - m.at <= self.velocity_window]
        if len(band) >= self.velocity_threshold:
            window_seconds = int(self.velocity_window.total_seconds())
            return IntegritySignal(
                signal_type="SE-VELOCITY",
                subject="self_modification",
                severity="warning",
                detail=(
                    f"{len(band)} self-modifications within {window_seconds}s — "
                    f"possible runaway self-evolution."
                ),
                evidence={"count": len(band), "window_seconds": window_seconds},
            )
        return None

    def check_privilege_accrual(self) -> Optional[IntegritySignal]:
        """SE-PRIVILEGE: the agent's self-modifications trend toward HIGHER
        authorization over time — progressively granting itself more privilege."""
        if len(self._mods) < self.baseline_window + self.recent_window:
            return None
        ranks = [m.rank for m in self._mods]
        baseline = sum(ranks[: self.baseline_window]) / self.baseline_window
        recent = sum(ranks[-self.recent_window :]) / self.recent_window
        drift = recent - baseline  # positive => privilege rising
        if drift >= self.privilege_drift_threshold:
            return IntegritySignal(
                signal_type="SE-PRIVILEGE",
                subject="self_modification",
                severity="critical",
                detail=(
                    f"Self-modification authorization drifted UP {drift:.2f} levels "
                    f"(baseline {baseline:.2f} -> recent {recent:.2f}) — the agent is "
                    f"granting itself progressively higher-privilege capabilities."
                ),
                evidence={"baseline_mean": baseline, "recent_mean": recent, "drift": drift},
            )
        return None

    def check_surface_expansion(self) -> Optional[IntegritySignal]:
        """SE-SURFACE: the breadth of distinct capabilities modified within the
        window crosses a budget (capability-surface expansion)."""
        if not self._mods:
            return None
        newest = max(m.at for m in self._mods)
        band = [m for m in self._mods if newest - m.at <= self.surface_window]
        distinct = {m.target for m in band if m.target}
        if len(distinct) >= self.surface_threshold:
            window_seconds = int(self.surface_window.total_seconds())
            return IntegritySignal(
                signal_type="SE-SURFACE",
                subject="self_modification",
                severity="warning",
                detail=(
                    f"{len(distinct)} distinct capabilities modified within "
                    f"{window_seconds}s — capability-surface expansion."
                ),
                evidence={"distinct_targets": sorted(distinct), "window_seconds": window_seconds},
            )
        return None

    def scan(self) -> List[IntegritySignal]:
        """Run all self-evolution detectors over the current modification stream."""
        signals: List[IntegritySignal] = []
        for detector in (
            self.check_velocity,
            self.check_privilege_accrual,
            self.check_surface_expansion,
        ):
            signal = detector()
            if signal:
                signals.append(signal)
        return signals

    def is_flagged(self) -> bool:
        """True if any self-evolution signal is currently active — used by the CGA
        loop to HOLD a self-modification when the agent is in a capability-gain
        pattern."""
        return bool(self.scan())


__all__ = ["SelfEvolutionMonitor"]
