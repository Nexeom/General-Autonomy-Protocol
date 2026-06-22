"""Out-of-process Governance Kernel service (Fix 2 structural boundary)."""

from gap_kernel.service.kernel_server import GovernanceService, serve_stdio

__all__ = ["GovernanceService", "serve_stdio"]
