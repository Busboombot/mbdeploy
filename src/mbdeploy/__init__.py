"""mbdeploy — standalone micro:bit deploy package."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    # Single source of truth: the version declared in pyproject.toml, read
    # back from the installed package metadata (editable installs included).
    __version__ = _pkg_version("mbdeploy")
except PackageNotFoundError:  # pragma: no cover - only when not installed
    __version__ = "0.0.0+unknown"
