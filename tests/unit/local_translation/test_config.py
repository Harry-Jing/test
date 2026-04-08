from pathlib import Path

import pytest

from vrc_live_caption.config import ConfigError
from vrc_live_caption.local_translation.translategemma.config import (
    TranslateGemmaLocalServiceConfig,
)


def test_local_translategemma_config_returns_defaults_when_optional_file_is_missing(
    tmp_path: Path,
) -> None:
    config = TranslateGemmaLocalServiceConfig.from_toml_file(
        tmp_path / "missing-local-translation.toml",
        required=False,
    )

    assert config.model == "google/translategemma-4b-it"
    assert config.device == "auto"
    assert config.dtype == "auto"
    assert config.max_new_tokens == 256


def test_local_translategemma_config_parses_example_file() -> None:
    config = TranslateGemmaLocalServiceConfig.from_toml_file(
        Path("local-translation-translategemma.toml.example")
    )

    assert config.model == "google/translategemma-4b-it"
    assert config.device == "auto"
    assert config.dtype == "auto"


def test_local_translategemma_config_rejects_invalid_device(tmp_path: Path) -> None:
    config_path = tmp_path / "bad-local-translation-device.toml"
    config_path.write_text('device = "metal"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="device must be one of: auto, cpu, cuda"):
        TranslateGemmaLocalServiceConfig.from_toml_file(config_path)


def test_local_translategemma_config_rejects_invalid_dtype(tmp_path: Path) -> None:
    config_path = tmp_path / "bad-local-translation-dtype.toml"
    config_path.write_text('dtype = "float16"\n', encoding="utf-8")

    with pytest.raises(
        ConfigError, match="dtype must be one of: auto, bfloat16, float32"
    ):
        TranslateGemmaLocalServiceConfig.from_toml_file(config_path)


def test_local_translategemma_config_rejects_blank_model(tmp_path: Path) -> None:
    config_path = tmp_path / "bad-local-translation-model.toml"
    config_path.write_text('model = "   "\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="model must not be empty"):
        TranslateGemmaLocalServiceConfig.from_toml_file(config_path)
