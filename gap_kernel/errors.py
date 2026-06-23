"""Shared exceptions for GAP."""


class GovernanceConfigError(Exception):
    """Raised when a *governed* deployment is missing required configuration.

    Governed mode fails closed: it refuses to run without the industry-specific
    regulatory floor (an Applicability Profile) and the universal safety
    primitives wired in (signature verification, strict action typing, the SIR
    intent-transfer gate).
    """
