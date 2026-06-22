"""Structured Intent Resolution models (SIR-1).

SIR governs the intent-transfer moment — the ungoverned origin where human
intent becomes system action. Every governed action at L1+ must first produce a
confirmed Intent Declaration with five components.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from gap_kernel.models.governance import AuthorizationLevel


class ConfirmationState(str, Enum):
    """Whether the human has reviewed and confirmed mutual understanding."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"   # human corrected the system's interpretation


class MetaIntent(BaseModel):
    """The intent behind the intent — the values motivating the request (SIR-2)."""
    primary_objective: str                       # what success looks like
    value_hierarchy: List[str] = []              # which constraints outweigh the goal
    risk_tolerance: str = "moderate"             # low | moderate | high
    stakeholder_impact: List[str] = []           # who is affected and how


class CorrectionRecord(BaseModel):
    """A human correction to the system's interpretation — the highest-value signal."""
    field: str
    original: str
    corrected: str
    corrected_at: datetime


class IntentDeclaration(BaseModel):
    """The five-component Intent Declaration (SIR-1)."""
    declaration_id: str
    stated_intent: str                           # the human directive, verbatim
    interpreted_intent: str                      # the system's operational reading
    meta_intent: MetaIntent
    declared_boundaries: List[str] = []          # what the system will NOT do
    confirmation_state: ConfirmationState = ConfirmationState.PENDING
    correction_records: List[CorrectionRecord] = []
    authorization_level: AuthorizationLevel
    resolution_mode: str
    created_at: datetime
    # SIR-4 — cryptographic lineage link to the resulting Decision Record.
    linked_decision_id: Optional[str] = None
    integrity_signature: Optional[str] = None
    signing_key_id: Optional[str] = None


class StandingIntentDeclaration(BaseModel):
    """A pre-resolved intent for routine L0 operations (SIR-5).

    Must be human-authored, must expire, and is itself a governed artifact —
    the autonomous system cannot create one.
    """
    standing_id: str
    intent_class: str
    declaration: IntentDeclaration
    authored_by: str                             # human authority (never the system)
    expires_at: datetime
