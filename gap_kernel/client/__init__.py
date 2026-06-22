"""Governance Kernel clients — the agent-side handle to the governance boundary."""

from gap_kernel.client.governance_client import (
    GovernanceClientError,
    InProcessGovernanceClient,
    SubprocessGovernanceClient,
)

__all__ = [
    "GovernanceClientError",
    "InProcessGovernanceClient",
    "SubprocessGovernanceClient",
]
