from pathlib import Path

import pytest

from vrc_live_caption.config import ConfigError
from vrc_live_caption.local_stt.funasr.config import FunasrLocalServiceConfig


def test_local_funasr_config_returns_defaults_when_optional_file_is_missing(
    tmp_path: Path,
) -> None:
    config = FunasrLocalServiceConfig.from_toml_file(
        tmp_path / "missing-local-stt.toml",
        required=False,
    )

    assert config.mode == "2pass"
    assert config.device == "cpu"
    assert config.chunk_size == (0, 10, 5)
    assert config.packet_duration_ms == 60


def test_local_funasr_config_parses_example_file() -> None:
    config = FunasrLocalServiceConfig.from_toml_file(
        Path("local-stt-funasr.toml.example")
    )

    assert config.mode == "2pass"
    assert config.offline_asr_model == "paraformer-zh"
    assert config.online_asr_model == "paraformer-zh-streaming"


def test_local_funasr_config_rejects_invalid_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "bad-local-stt.toml"
    config_path.write_text('mode = "offline"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="mode must be 2pass"):
        FunasrLocalServiceConfig.from_toml_file(config_path)
