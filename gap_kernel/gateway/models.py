"""The agent supplies tool intent, never policies, risk levels, or evidence."""
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolAction(StrictModel):
    tool: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=128)
    arguments: dict = Field(default_factory=dict)


class ProposalRequest(StrictModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=r"^[A-Za-z0-9_-]{1,64}$")
    actions: list[ToolAction] = Field(min_length=1, max_length=8)


class LookupArguments(StrictModel):
    pass


class NotifyArguments(StrictModel):
    message: str = Field(min_length=1, max_length=2000)


class HumanApproval(StrictModel):
    human_approval_signature: str = Field(pattern=r"^[0-9a-f]{128}$")
    human_approver_public_key_id: str = Field(min_length=1, max_length=128)
    human_approval_timestamp: datetime
    human_approval_valid_until: datetime

    @field_validator("human_approval_timestamp", "human_approval_valid_until")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approval timestamps require a timezone")
        return value


class ExecuteRequest(StrictModel):
    approval: HumanApproval | None = None


class ToolDefinition(StrictModel):
    """Deployment configuration; never accepted in an HTTP request."""
    action_type: Literal["query_crm", "send_email"]
    risk_score: int = Field(ge=1, le=10)
    cost: float = Field(ge=0, allow_inf_nan=False)


class GatewayConfig(StrictModel):
    trust_root: str
    profile: str
    world: str
    intent: str
    agent_token_file: str
    tool_token_file: str
    tool_base_url: str
    approvers: dict[str, str]
    targets: list[str] = Field(min_length=1)
    tools: dict[str, ToolDefinition]
    # HTTPX timeouts bound individual I/O waits, not total batch duration.
    # Cross-process dispatch exclusion comes from the requests DB transaction.
    tool_timeout_seconds: float = Field(default=5, gt=0, le=10)
