from pathlib import Path
from typing import Any

from vrc_live_caption.cli import app
from vrc_live_caption.errors import TranslationError


class TestLocalTranslationServeCommand:
    def test_when_local_sidecar_starts__then_it_reads_runtime_settings_from_app_config(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_run_translategemma_local_server(
            *, config, host, port, logger
        ) -> None:
            captured["config"] = config
            captured["host"] = host
            captured["port"] = port

        monkeypatch.setattr(
            "vrc_live_caption.cli.run_translategemma_local_server",
            fake_run_translategemma_local_server,
        )
        config_path = config_file_factory(
            translategemma_local_translation_overrides={
                "host": "127.0.0.2",
                "port": 11096,
            },
            translategemma_local_sidecar_overrides={
                "model": "custom/translategemma",
                "device": "cpu",
                "dtype": "float32",
            },
        )

        result = cli_runner.invoke(
            app,
            ["local-translation", "serve", "--config", str(config_path)],
        )

        assert result.exit_code == 0
        assert captured["host"] == "127.0.0.2"
        assert captured["port"] == 11096
        assert captured["config"].model == "custom/translategemma"
        assert f"App config: {config_path}" in result.output
        assert "Local translation model: custom/translategemma" in result.output
        assert "Local translation device policy: cpu" in result.output
        assert "Local translation dtype policy: float32" in result.output
        assert (
            "Starting local TranslateGemma sidecar on ws://127.0.0.2:11096"
            in result.output
        )

    def test_when_app_config_is_invalid__then_it_exits_non_zero(
        self,
        tmp_path: Path,
        cli_runner,
    ) -> None:
        config_path = tmp_path / "bad-local-translation.toml"
        config_path.write_text(
            "\n".join(
                [
                    "[translation.providers.translategemma_local.sidecar]",
                    'dtype = "float16"',
                ]
            ),
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            app,
            ["local-translation", "serve", "--config", str(config_path)],
        )

        assert result.exit_code == 1
        assert (
            "translation.providers.translategemma_local.sidecar.dtype must be one of: auto, bfloat16, float32"
            in result.output
        )

    def test_when_runtime_raises_vrc_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        async def fake_run_translategemma_local_server(**kwargs) -> None:
            raise TranslationError("sidecar failed")

        monkeypatch.setattr(
            "vrc_live_caption.cli.run_translategemma_local_server",
            fake_run_translategemma_local_server,
        )

        result = cli_runner.invoke(app, ["local-translation", "serve"])

        assert result.exit_code == 1
        assert "sidecar failed" in result.output

    def test_when_runtime_raises_unexpected_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        async def fake_run_translategemma_local_server(**kwargs) -> None:
            raise RuntimeError("unexpected serve failure")

        monkeypatch.setattr(
            "vrc_live_caption.cli.run_translategemma_local_server",
            fake_run_translategemma_local_server,
        )

        result = cli_runner.invoke(app, ["local-translation", "serve"])

        assert result.exit_code == 1
        assert "unexpected serve failure" in result.output

    def test_when_keyboard_interrupt_happens__then_it_returns_cleanly(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        async def fake_run_translategemma_local_server(**kwargs) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(
            "vrc_live_caption.cli.run_translategemma_local_server",
            fake_run_translategemma_local_server,
        )

        result = cli_runner.invoke(app, ["local-translation", "serve"])

        assert result.exit_code == 0
