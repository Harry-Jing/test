from pathlib import Path

import pytest

from vrc_live_caption.config import AppConfig
from vrc_live_caption.local_translation.translategemma.config import (
    TranslateGemmaLocalServiceConfig,
)


def test_local_translategemma_config_returns_model_defaults() -> None:
    config = TranslateGemmaLocalServiceConfig()

    assert config.model == "google/translategemma-4b-it"
    assert config.device == "auto"
    assert config.dtype == "auto"
    assert config.max_new_tokens == 256


def test_local_translategemma_config_is_loaded_from_the_main_app_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "app.toml"
    config_path.write_text(
        "\n".join(
            [
                "[translation.providers.translategemma_local.sidecar]",
                'model = "custom/translategemma"',
                'device = "cpu"',
                'dtype = "float32"',
            ]
        ),
        encoding="utf-8",
    )
    config = AppConfig.from_toml_file(
        config_path
    ).translation.providers.translategemma_local.sidecar

    assert config.model == "custom/translategemma"
    assert config.device == "cpu"
    assert config.dtype == "float32"


def test_local_translategemma_config_rejects_invalid_device(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="translation.providers.translategemma_local.sidecar.device must be one of: auto, cpu, cuda",
    ):
        TranslateGemmaLocalServiceConfig(device="metal")


def test_local_translategemma_config_rejects_invalid_dtype(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="translation.providers.translategemma_local.sidecar.dtype must be one of: auto, bfloat16, float32",
    ):
        TranslateGemmaLocalServiceConfig(dtype="float16")


def test_local_translategemma_config_rejects_blank_model(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="translation.providers.translategemma_local.sidecar.model must not be empty",
    ):
        TranslateGemmaLocalServiceConfig(model="   ")
