"""Resolve the application version from package metadata or the source tree."""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

_PACKAGE_NAME = "vrc-live-caption"


def _source_tree_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"

    try:
        with pyproject_path.open("rb") as handle:
            project = tomllib.load(handle)["project"]
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Package metadata is unavailable and pyproject.toml was not found."
        ) from exc
    except KeyError as exc:
        raise RuntimeError("Missing [project] table in pyproject.toml.") from exc

    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("Missing [project].version in pyproject.toml.")
    return version


def get_version() -> str:
    """Return the installed package version or the source-tree project version."""
    try:
        return package_version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return _source_tree_version()
