from pathlib import Path

from vrc_live_caption.cli import app
from vrc_live_caption.config import ConfigError
from vrc_live_caption.errors import TranslationError


class TestLocalTranslationServeCommand:
    def test_when_local_sidecar_starts__then_it_uses_the_requested_host_and_port(
        self,
        monkeypatch,
        tmp_cwd: Path,
        cli_runner,
    ) -> None:
        captured: dict[str, object] = {}

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

        result = cli_runner.invoke(
            app,
            ["local-translation", "serve", "--host", "127.0.0.1", "--port", "10096"],
        )

        assert result.exit_code == 0
        assert captured["host"] == "127.0.0.1"
        assert captured["port"] == 10096
        assert "Local translation model: google/translategemma-4b-it" in result.output
        assert "Local translation device policy: auto" in result.output
        assert "Local translation dtype policy: auto" in result.output
        assert (
            "Starting local TranslateGemma sidecar on ws://127.0.0.1:10096"
            in result.output
        )

    def test_when_local_config_is_invalid__then_it_exits_non_zero(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        monkeypatch.setattr(
            "vrc_live_caption.cli._load_optional_local_translation_config",
            lambda config: (_ for _ in ()).throw(
                ConfigError("bad local translation config")
            ),
        )

        result = cli_runner.invoke(app, ["local-translation", "serve"])

        assert result.exit_code == 1
        assert "bad local translation config" in result.output

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
