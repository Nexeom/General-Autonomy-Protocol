"""Independent authorization review — the GIM independence axis (G-3).

The spec's GIM-1/GIM-2 call for an INDEPENDENT evaluator ("different model,
different prompt, no access to the system's classification logic") that
re-classifies operations by required authorization level, with divergence from
the system's own classification tracked as a drift score. The base GIM detectors
watch the system's *own* decision stream; this module adds the missing axis — a
second, independent classifier whose judgments are *compared* to the kernel's.

  * ``IndependentClassifier`` — the pluggable evaluator interface. A model-backed
    classifier (a genuinely different model re-rating the same inputs) is the
    intended adapter point and remains Normative / Planned.
  * ``RuleBasedIndependentClassifier`` — a reference implementation that rates an
    operation purely from its risk signals (risk score + irreversible / sensitive
    keywords), deliberately sharing NONE of the kernel's Action Type Registry
    logic, so it cannot inherit the kernel's blind spots.
  * ``IndependentAuthorizationMonitor`` — compares the system-assigned level to the
    independent classifier's level per operation and flags (a) any severe single
    operation where the system under-classified by ``severe_gap`` or more levels
    (e.g. system L0 vs independent L3/L4 — the spec's hard alert), and (b) a
    sustained under-classification drift beyond ``drift_threshold``.

Honest scoping: the reference classifier provides *logic* independence (different
algorithm); true *model* independence is the adapter. Running the monitor
out-of-process with its own key and a human-only reporting channel is a deployment
topology (the same isolation concern as the kernel boundary), not done here.
"""

from __future__ import annotations

from typing import List, Optional, Protocol

from gap_kernel.governance.integrity_monitor import IntegritySignal, _RANK, _cap
from gap_kernel.models.governance import AuthorizationLevel

_LEVELS = [
    AuthorizationLevel.L0, AuthorizationLevel.L1, AuthorizationLevel.L2,
    AuthorizationLevel.L3, AuthorizationLevel.L4,
]
_MAX_GAP = 4  # L0..L4 — normalizes the drift score to [0, 1]

_HIGH_RISK = (
    "delete", "destroy", "wipe", "purge", "erase", "terminate", "decommission",
    "shutdown", "wire", "transfer", "payment", "liquidate", "launch", "deploy",
    "grant", "escalate", "privilege",
)
_SENSITIVE = (
    "medical", "health", "patient", "clinical", "legal", "lending", "loan",
    "credit", "insurance", "biometric", "minor", "child", "financial", "phi", "pii",
)


class IndependentClassifier(Protocol):
    """Re-classifies an operation's required authorization level, independently of
    the kernel. ``classify`` sees the same inputs the kernel saw, not the kernel's
    reasoning."""

    def classify(
        self, action_type: str, parameters: dict, risk_score: int
    ) -> AuthorizationLevel:  # pragma: no cover - interface
        ...


class RuleBasedIndependentClassifier:
    """A reference independent classifier driven by risk signals, NOT the kernel's
    Action Type Registry — so it cannot share the kernel's classification blind
    spots."""

    def classify(
        self, action_type: str, parameters: dict, risk_score: int
    ) -> AuthorizationLevel:
        # Build the keyword-scan text defensively: a non-dict `parameters` or a
        # value whose __str__ raises must not crash a fail-safe oversight check.
        params = parameters if isinstance(parameters, dict) else {}
        parts = []
        for v in params.values():
            try:
                parts.append(str(v))
            except Exception:
                continue  # an unstringable value simply contributes no keywords
        text = (str(action_type) + " " + " ".join(parts)).lower()
        try:
            rs = int(risk_score)
        except (TypeError, ValueError):
            return AuthorizationLevel.L4  # cannot assess risk -> maximum oversight
        score = 0
        if rs >= 8:
            score += 3
        elif rs >= 5:
            score += 2
        elif rs >= 3:
            score += 1
        if any(k in text for k in _HIGH_RISK):
            score += 2
        if any(k in text for k in _SENSITIVE):
            score += 1
        return _LEVELS[min(score, 4)]


class IndependentAuthorizationMonitor:
    """GIM independence (G-3): flag where the system's authorization level diverges
    from an independent classifier's."""

    def __init__(
        self,
        classifier: Optional[IndependentClassifier] = None,
        *,
        drift_threshold: float = 0.15,
        severe_gap: int = 3,
        min_samples: int = 5,
        max_history: int = 1024,
    ):
        self._classifier = classifier or RuleBasedIndependentClassifier()
        self.drift_threshold = drift_threshold
        self.severe_gap = severe_gap
        self.min_samples = min_samples
        self._max_history = max(1, max_history)
        self._divergences: List[dict] = []

    def observe(
        self,
        action_type: str,
        parameters: dict,
        risk_score: int,
        system_level: AuthorizationLevel,
    ) -> dict:
        """Re-classify the operation independently and record the divergence
        (independent - system; positive => the system under-classified)."""
        independent = self._classifier.classify(action_type, parameters, risk_score)
        s, i = _RANK[system_level.value], _RANK[independent.value]
        record = {"system": s, "independent": i, "gap": i - s,
                  "action_type": str(action_type)}
        self._divergences.append(record)
        _cap(self._divergences, self._max_history)
        return record

    def observe_decision(self, decision, proposal) -> Optional[dict]:
        """Convenience: re-classify the proposal's first action and compare to the
        decision's authorization level. Skips a decision with no level."""
        if decision.authorization_level is None or not proposal.actions:
            return None
        action = proposal.actions[0]
        return self.observe(
            action.action_type, action.parameters, action.risk_score,
            decision.authorization_level,
        )

    def check_divergence(self) -> Optional[IntegritySignal]:
        """Flag a severe single-operation under-classification (system under-rated
        by >= severe_gap levels — the spec's hard alert), or sustained
        under-classification drift beyond the threshold."""
        # (a) Severe single operation — the spec's "system L0 / independent L3-L4".
        for d in self._divergences:
            if d["gap"] >= self.severe_gap:
                return IntegritySignal(
                    signal_type="GIM-INDEP",
                    subject=d["action_type"],
                    severity="critical",
                    detail=(
                        f"System assigned {_LEVELS[d['system']].value} but the "
                        f"independent evaluator requires {_LEVELS[d['independent']].value} "
                        f"for '{d['action_type']}' — a {d['gap']}-level under-classification."
                    ),
                    evidence={"system_rank": d["system"], "independent_rank": d["independent"],
                              "gap": d["gap"]},
                )
        # (b) Sustained under-classification drift.
        if len(self._divergences) < self.min_samples:
            return None
        drift = sum(max(0, d["gap"]) for d in self._divergences) / len(self._divergences) / _MAX_GAP
        if drift > self.drift_threshold:
            return IntegritySignal(
                signal_type="GIM-INDEP",
                subject="authorization_classification",
                severity="warning",
                detail=(
                    f"System authorization classification drifts {drift:.2f} below an "
                    f"independent evaluator across {len(self._divergences)} operations "
                    f"(threshold {self.drift_threshold}) — systematic under-classification."
                ),
                evidence={"drift": drift, "samples": len(self._divergences)},
            )
        return None

    def scan(self) -> List[IntegritySignal]:
        signal = self.check_divergence()
        return [signal] if signal else []


__all__ = [
    "IndependentClassifier",
    "RuleBasedIndependentClassifier",
    "IndependentAuthorizationMonitor",
]
