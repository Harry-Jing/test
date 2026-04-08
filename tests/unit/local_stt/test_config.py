from pathlib import Path

import pytest

from vrc_live_caption.config import AppConfig
from vrc_live_caption.local_stt.funasr.config import FunasrLocalServiceConfig


def test_local_funasr_config_returns_model_defaults() -> None:
    config = FunasrLocalServiceConfig()

    assert config.mode == "2pass"
    assert config.device == "auto"
    assert config.chunk_size == (0, 10, 5)
    assert config.packet_duration_ms == 60


def test_local_funasr_config_is_loaded_from_the_main_app_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "app.toml"
    config_path.write_text(
        "\n".join(
            [
                "[stt.providers.funasr_local.sidecar]",
                'device = "cpu"',
                'offline_asr_model = "custom-offline"',
                'online_asr_model = "custom-online"',
            ]
        ),
        encoding="utf-8",
    )
    config = AppConfig.from_toml_file(config_path).stt.providers.funasr_local.sidecar

    assert config.mode == "2pass"
    assert config.device == "cpu"
    assert config.offline_asr_model == "custom-offline"
    assert config.online_asr_model == "custom-online"


def test_local_funasr_config_rejects_invalid_mode(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="stt.providers.funasr_local.sidecar.mode must be 2pass",
    ):
        FunasrLocalServiceConfig(mode="offline")


def test_local_funasr_config_rejects_invalid_device(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="stt.providers.funasr_local.sidecar.device must be one of: auto, cpu, cuda",
    ):
        FunasrLocalServiceConfig(device="metal")
