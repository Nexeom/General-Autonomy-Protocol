"""Execution Result — outcome from the Execution Fabric."""

from datetime import datetime
from typing import List

from pydantic import BaseModel


class ExecutionResult(BaseModel):
    """Outcome of executing an approved strategy."""

    proposal_id: str
    actions_completed: List[dict]
    actions_failed: List[dict]
    success: bool
    world_state_changes: List[dict]
    executed_at: datetime
    execution_duration_seconds: float
