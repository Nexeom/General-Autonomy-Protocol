"""General Autonomy Protocol — Kernel Implementation."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    # pyproject.toml is the single source of the version. Reading it back from
    # the installed distribution keeps the package, the REST surface and the
    # docs from drifting apart, which they did.
    __version__ = _version("gap-kernel")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
