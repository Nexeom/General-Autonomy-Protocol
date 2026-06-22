"""Structured Intent Resolution — governing the intent-transfer moment (SIR).

SIR operates BEFORE the CGA loop: CGA governs *how* the system achieves an
intent; SIR governs *whether* the intent is correctly understood. This module
implements a skeletal resolver:

  * SIR-1 — produce a five-component Intent Declaration.
  * SIR-3 — proportional resolution: depth scales with authorization level.
  * SIR-4 — cryptographically seal a confirmed declaration and link it to the
            resulting Decision Record (lineage origin node).
  * SIR-5 — governed Standing Declarations for L0 (human-authored, expiring).

Meta-intent inference (SIR-2) is intentionally pluggable — the specification
defines what must be captured/confirmed, not how to infer it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Callable, List, Optional
from uuid import uuid4

from gap_kernel.crypto.signing import sign, verify
from gap_kernel.models.governance import AuthorizationLevel
from gap_kernel.models.sir import (
    ConfirmationState,
    CorrectionRecord,
    IntentDeclaration,
    MetaIntent,
    StandingIntentDeclaration,
)

_AUTH_RANK = {
    AuthorizationLevel.L0: 0,
    AuthorizationLevel.L1: 1,
    AuthorizationLevel.L2: 2,
    AuthorizationLevel.L3: 3,
    AuthorizationLevel.L4: 4,
}

# SIR-3 — resolution depth proportional to authorization level.
_RESOLUTION_MODES = {
    AuthorizationLevel.L0: "standing_declaration",
    AuthorizationLevel.L1: "structured_confirmation",
    AuthorizationLevel.L2: "collaborative_specification",
    AuthorizationLevel.L3: "full_negotiation",
    AuthorizationLevel.L4: "full_negotiation",
}

# Confirmation states that represent a human-aligned declaration ready for CGA.
_READY_STATES = {ConfirmationState.CONFIRMED, ConfirmationState.CORRECTED}


class StandingIntentError(Exception):
    """Raised when a Standing Intent Declaration is invalid or ungoverned."""


def _default_meta_intent(stated_intent: str) -> MetaIntent:
    """A placeholder meta-intent inference (SIR-2 is pluggable)."""
    return MetaIntent(
        primary_objective=f"Fulfil the stated intent: {stated_intent}",
        value_hierarchy=["safety", "regulatory_compliance", "user_benefit"],
        risk_tolerance="moderate",
        stakeholder_impact=["requesting_user"],
    )


class StructuredIntentResolver:
    """Produces and governs Intent Declarations at the intent-transfer boundary."""

    def __init__(
        self,
        system_identity: str = "governance_system",
        meta_intent_inferencer: Optional[Callable[[str], MetaIntent]] = None,
    ):
        # The autonomous system's own identity — it may never author a Standing
        # Declaration (SIR-5) nor confirm its own intent.
        self._system_identity = system_identity
        self._infer_meta_intent = meta_intent_inferencer or _default_meta_intent

    # --- SIR-3 ---

    @staticmethod
    def resolution_mode(authorization_level: AuthorizationLevel) -> str:
        return _RESOLUTION_MODES[authorization_level]

    @staticmethod
    def requires_confirmation(authorization_level: AuthorizationLevel) -> bool:
        """L0 uses a standing declaration; L1+ require per-action confirmation."""
        return _AUTH_RANK[authorization_level] >= _AUTH_RANK[AuthorizationLevel.L1]

    # --- SIR-1 ---

    def resolve(
        self,
        stated_intent: str,
        authorization_level: AuthorizationLevel,
        *,
        interpreted_intent: Optional[str] = None,
        meta_intent: Optional[MetaIntent] = None,
        declared_boundaries: Optional[List[str]] = None,
        declaration_id: Optional[str] = None,
        intent_class: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> IntentDeclaration:
        """Produce an Intent Declaration (PENDING) for the stated intent."""
        return IntentDeclaration(
            declaration_id=declaration_id or f"intent_{uuid4().hex[:12]}",
            intent_class=intent_class,
            stated_intent=stated_intent,
            interpreted_intent=interpreted_intent or stated_intent,
            meta_intent=meta_intent or self._infer_meta_intent(stated_intent),
            declared_boundaries=declared_boundaries or [],
            confirmation_state=ConfirmationState.PENDING,
            authorization_level=authorization_level,
            resolution_mode=self.resolution_mode(authorization_level),
            created_at=created_at or datetime.utcnow(),
        )

    def confirm(self, declaration: IntentDeclaration) -> IntentDeclaration:
        """Mark a declaration confirmed by human authority."""
        return declaration.model_copy(
            update={"confirmation_state": ConfirmationState.CONFIRMED}
        )

    def correct(
        self,
        declaration: IntentDeclaration,
        *,
        field: str,
        corrected_value: str,
        original_value: str = "",
        at: Optional[datetime] = None,
    ) -> IntentDeclaration:
        """Record a human correction to the system's interpretation."""
        record = CorrectionRecord(
            field=field,
            original=original_value,
            corrected=corrected_value,
            corrected_at=at or datetime.utcnow(),
        )
        return declaration.model_copy(
            update={
                "confirmation_state": ConfirmationState.CORRECTED,
                "correction_records": declaration.correction_records + [record],
            }
        )

    # --- readiness gate (SIR-3 + mutual-confirmation principle) ---

    def is_ready_for_cga(
        self,
        declaration: IntentDeclaration,
        *,
        standing: Optional[StandingIntentDeclaration] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """Whether the CGA loop may engage on this declaration.

        L0: requires a valid, human-authored, unexpired Standing Declaration that
            actually covers THIS declaration's intent class (the standing's
            pre-approval must be scoped to the intent it authorizes).
        L1+: requires the declaration to be CONFIRMED or CORRECTED (not PENDING).
        """
        if declaration.authorization_level == AuthorizationLevel.L0:
            if standing is None:
                return False
            try:
                self.validate_standing(standing, now=now)
            except StandingIntentError:
                return False
            # Bind the standing to this declaration: a standing for one intent
            # class must not bless an unrelated L0 intent. Both must carry the
            # same non-empty intent class.
            if not declaration.intent_class:
                return False
            return declaration.intent_class == standing.intent_class
        return declaration.confirmation_state in _READY_STATES

    # --- SIR-5 standing-declaration governance ---

    def validate_standing(
        self,
        standing: StandingIntentDeclaration,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        """Validate a Standing Declaration (SIR-5); raise StandingIntentError if invalid."""
        if standing.authored_by == self._system_identity or not standing.authored_by:
            raise StandingIntentError(
                f"Standing declaration '{standing.standing_id}' must be authored by a "
                f"human authority, not the governed system."
            )
        when = now or datetime.utcnow()
        if when >= standing.expires_at:
            raise StandingIntentError(
                f"Standing declaration '{standing.standing_id}' expired at "
                f"{standing.expires_at.isoformat()}; human re-confirmation required."
            )
        # The pre-resolved intent must itself be an L0, human-confirmed intent —
        # a standing cannot pre-approve an unconfirmed or higher-tier declaration.
        if standing.declaration.authorization_level != AuthorizationLevel.L0:
            raise StandingIntentError(
                f"Standing declaration '{standing.standing_id}' must carry an L0 intent."
            )
        if standing.declaration.confirmation_state not in _READY_STATES:
            raise StandingIntentError(
                f"Standing declaration '{standing.standing_id}' intent is not confirmed."
            )

    # --- SIR-4 cryptographic seal + lineage link ---

    @staticmethod
    def _seal_payload(declaration: IntentDeclaration) -> str:
        data = declaration.model_dump(
            mode="json", exclude={"integrity_signature", "signing_key_id"}
        )
        return json.dumps(data, sort_keys=True, default=str)

    def seal(
        self,
        declaration: IntentDeclaration,
        private_key_hex: str,
        signing_key_id: str,
        *,
        decision_id: Optional[str] = None,
    ) -> IntentDeclaration:
        """Seal a confirmed declaration and link it to its Decision Record (SIR-4).

        Only a CONFIRMED or CORRECTED declaration may be sealed — sealing a
        PENDING (unconfirmed) intent would bless an unaligned directive.
        """
        if declaration.confirmation_state not in _READY_STATES:
            raise StandingIntentError(
                f"Cannot seal declaration '{declaration.declaration_id}': it is "
                f"{declaration.confirmation_state.value}, not confirmed."
            )
        linked = declaration.model_copy(
            update={"linked_decision_id": decision_id, "signing_key_id": signing_key_id}
        )
        signature = sign(private_key_hex, self._seal_payload(linked))
        return linked.model_copy(update={"integrity_signature": signature})

    def verify_seal(self, declaration: IntentDeclaration, public_key_hex: str) -> bool:
        """Verify a sealed declaration's integrity signature."""
        if not declaration.integrity_signature:
            return False
        return verify(
            public_key_hex,
            self._seal_payload(declaration),
            declaration.integrity_signature,
        )
