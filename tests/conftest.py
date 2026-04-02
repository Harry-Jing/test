from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.support.config_helpers import write_test_config


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def tmp_cwd(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def config_file_factory(tmp_path: Path) -> Callable[..., Path]:
    def factory(
        path: Path | None = None,
        *,
        capture_overrides: dict[str, Any] | None = None,
        audio_overrides: dict[str, Any] | None = None,
        pipeline_overrides: dict[str, Any] | None = None,
        logging_overrides: dict[str, Any] | None = None,
        debug_overrides: dict[str, Any] | None = None,
        osc_overrides: dict[str, Any] | None = None,
        stt_overrides: dict[str, Any] | None = None,
        stt_retry_overrides: dict[str, Any] | None = None,
        iflytek_rtasr_overrides: dict[str, Any] | None = None,
        openai_realtime_overrides: dict[str, Any] | None = None,
    ) -> Path:
        target = path or tmp_path / "app.toml"
        write_test_config(
            target,
            capture_overrides=capture_overrides,
            audio_overrides=audio_overrides,
            pipeline_overrides=pipeline_overrides,
            logging_overrides=logging_overrides,
            debug_overrides=debug_overrides,
            osc_overrides=osc_overrides,
            stt_overrides=stt_overrides,
            stt_retry_overrides=stt_retry_overrides,
            iflytek_rtasr_overrides=iflytek_rtasr_overrides,
            openai_realtime_overrides=openai_realtime_overrides,
        )
        return target

    return factory
