"""
Dynamic Risk Escalation — Runtime authorization tier adjustment.

The Governance Kernel can escalate authorization requirements based on
runtime behavioral signals. Escalation is unidirectional (up only).
De-escalation requires human policy review.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class EscalationTriggerType(str, Enum):
    """Categories of escalation triggers."""
    VOLUME_ANOMALY = "volume_anomaly"
    SCOPE_EXPANSION = "scope_expansion"
    CASCADING_ACTIONS = "cascading_actions"
    EXTERNAL_SIGNAL = "external_signal"


class EscalationTrigger(BaseModel):
    """A detected condition that warrants authorization escalation."""
    trigger_type: EscalationTriggerType
    description: str
    evidence: dict = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    original_level: str  # e.g., "L0", "L1"
    escalated_level: str  # e.g., "L2", "L3"
    confidence: float = Field(ge=0.0, le=1.0)


class EscalationConfig(BaseModel):
    """
    Configuration for escalation thresholds.
    Set by human policy-setter. Agent cannot access.
    """
    volume_threshold_multiplier: float = 10.0  # 10x normal = trigger
    scope_expansion_sensitivity: str = "medium"  # low | medium | high
    cascade_window_seconds: int = 300  # 5-minute window for cascade detection
    cascade_action_threshold: int = 5  # N actions in window = evaluate
    external_signal_sources: List[str] = Field(default_factory=list)
    enabled: bool = True


class DynamicRiskEngine:
    """
    Evaluates whether runtime behavioral signals warrant
    escalation of an action's authorization tier.

    This runs WITHIN the Governance Kernel — the governed agent
    has no access to this engine, its configuration, or its state.
    """

    def __init__(self, config: EscalationConfig):
        self.config = config
        self._behavioral_baseline: dict = {}
        self._action_history: List[dict] = []

    def evaluate(
        self,
        action_type: str,
        action_context: dict,
        current_auth_level: str,
    ) -> Optional[EscalationTrigger]:
        """
        Evaluate whether the proposed action warrants escalation.
        Returns EscalationTrigger if escalation needed, None otherwise.
        """
        if not self.config.enabled:
            return None

        triggers = []
        triggers.append(self._check_volume_anomaly(action_type, action_context))
        triggers.append(self._check_scope_expansion(action_type, action_context))
        triggers.append(self._check_cascading_actions(action_type, action_context))

        # Return highest-severity trigger
        active_triggers = [t for t in triggers if t is not None]
        if not active_triggers:
            return None

        return max(active_triggers, key=lambda t: t.escalated_level)

    def record_action(self, action_type: str, action_context: dict):
        """Record an action for behavioral baseline tracking."""
        self._action_history.append({
            "action_type": action_type,
            "context": action_context,
            "timestamp": datetime.utcnow(),
        })

    def _check_volume_anomaly(
        self, action_type: str, context: dict
    ) -> Optional[EscalationTrigger]:
        """Detect if action volume significantly exceeds baseline."""
        baseline_count = self._behavioral_baseline.get(
            f"{action_type}_count", 0
        )
        if baseline_count == 0:
            return None

        recent_count = sum(
            1 for a in self._action_history
            if a["action_type"] == action_type
            and (datetime.utcnow() - a["timestamp"]).total_seconds()
            < self.config.cascade_window_seconds
        )

        if recent_count >= baseline_count * self.config.volume_threshold_multiplier:
            current_level = context.get("current_auth_level", "L0")
            escalated = self._escalate_level(current_level)
            if escalated != current_level:
                return EscalationTrigger(
                    trigger_type=EscalationTriggerType.VOLUME_ANOMALY,
                    description=(
                        f"Action type '{action_type}' volume ({recent_count}) "
                        f"exceeds {self.config.volume_threshold_multiplier}x "
                        f"baseline ({baseline_count})"
                    ),
                    evidence={
                        "action_type": action_type,
                        "recent_count": recent_count,
                        "baseline_count": baseline_count,
                        "multiplier": self.config.volume_threshold_multiplier,
                    },
                    original_level=current_level,
                    escalated_level=escalated,
                    confidence=0.8,
                )
        return None

    def _check_scope_expansion(
        self, action_type: str, context: dict
    ) -> Optional[EscalationTrigger]:
        """Detect if action accesses resources outside historical baseline."""
        baseline_targets = self._behavioral_baseline.get(
            f"{action_type}_targets", set()
        )
        current_target = context.get("target", "")

        if baseline_targets and current_target not in baseline_targets:
            current_level = context.get("current_auth_level", "L0")
            escalated = self._escalate_level(current_level)
            if escalated != current_level:
                return EscalationTrigger(
                    trigger_type=EscalationTriggerType.SCOPE_EXPANSION,
                    description=(
                        f"Action type '{action_type}' targeting '{current_target}' "
                        f"outside historical baseline"
                    ),
                    evidence={
                        "action_type": action_type,
                        "target": current_target,
                        "known_targets": list(baseline_targets),
                    },
                    original_level=current_level,
                    escalated_level=escalated,
                    confidence=0.7,
                )
        return None

    def _check_cascading_actions(
        self, action_type: str, context: dict
    ) -> Optional[EscalationTrigger]:
        """Detect if sequence of low-risk actions constitutes high-risk operation."""
        now = datetime.utcnow()
        recent_actions = [
            a for a in self._action_history
            if (now - a["timestamp"]).total_seconds()
            < self.config.cascade_window_seconds
        ]

        if len(recent_actions) >= self.config.cascade_action_threshold:
            current_level = context.get("current_auth_level", "L0")
            escalated = self._escalate_level(current_level)
            if escalated != current_level:
                return EscalationTrigger(
                    trigger_type=EscalationTriggerType.CASCADING_ACTIONS,
                    description=(
                        f"{len(recent_actions)} actions in "
                        f"{self.config.cascade_window_seconds}s window "
                        f"exceeds cascade threshold "
                        f"({self.config.cascade_action_threshold})"
                    ),
                    evidence={
                        "action_count": len(recent_actions),
                        "window_seconds": self.config.cascade_window_seconds,
                        "threshold": self.config.cascade_action_threshold,
                        "action_types": [
                            a["action_type"] for a in recent_actions
                        ],
                    },
                    original_level=current_level,
                    escalated_level=escalated,
                    confidence=0.75,
                )
        return None

    def receive_external_signal(
        self, source: str, signal: dict
    ) -> Optional[EscalationTrigger]:
        """Process an external risk signal (SOC alert, compliance feed, etc.)."""
        if source not in self.config.external_signal_sources:
            return None

        severity = signal.get("severity", "low")
        current_level = signal.get("current_auth_level", "L0")

        if severity in ("high", "critical"):
            escalated = self._escalate_level(current_level, steps=2)
        elif severity == "medium":
            escalated = self._escalate_level(current_level, steps=1)
        else:
            return None

        if escalated != current_level:
            return EscalationTrigger(
                trigger_type=EscalationTriggerType.EXTERNAL_SIGNAL,
                description=(
                    f"External signal from '{source}' "
                    f"with severity '{severity}'"
                ),
                evidence={
                    "source": source,
                    "signal": signal,
                    "severity": severity,
                },
                original_level=current_level,
                escalated_level=escalated,
                confidence=0.9,
            )
        return None

    def set_baseline(self, action_type: str, **kwargs):
        """Set behavioral baseline for an action type."""
        for key, value in kwargs.items():
            self._behavioral_baseline[f"{action_type}_{key}"] = value

    @staticmethod
    def _escalate_level(current: str, steps: int = 1) -> str:
        """Escalate authorization level by N steps. Unidirectional (up only)."""
        levels = ["L0", "L1", "L2", "L3", "L4"]
        try:
            idx = levels.index(current)
        except ValueError:
            return current
        new_idx = min(idx + steps, len(levels) - 1)
        return levels[new_idx]
