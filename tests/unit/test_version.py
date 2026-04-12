import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import vrc_live_caption
from vrc_live_caption.cli import app
from vrc_live_caption.version import get_version


def _project_version() -> str:
    with (Path(__file__).resolve().parents[2] / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_when_package_is_imported__then_it_does_not_expose_version_constant() -> None:
    assert hasattr(vrc_live_caption, "__version__") is False


def test_when_version_is_resolved__then_it_matches_project_version() -> None:
    assert get_version() == _project_version()


def test_when_version_is_resolved_without_package_metadata__then_it_falls_back_to_pyproject(
    monkeypatch,
) -> None:
    def raise_package_not_found(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(
        "vrc_live_caption.version.package_version",
        raise_package_not_found,
    )

    assert get_version() == _project_version()


def test_when_version_flag_is_used_without_package_metadata__then_it_falls_back_to_pyproject(
    cli_runner,
    monkeypatch,
) -> None:
    def raise_package_not_found(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(
        "vrc_live_caption.version.package_version",
        raise_package_not_found,
    )

    result = cli_runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == _project_version()
