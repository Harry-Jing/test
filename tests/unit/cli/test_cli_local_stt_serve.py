from pathlib import Path
from typing import Any

from vrc_live_caption.cli import app
from vrc_live_caption.errors import AudioRuntimeError


class TestLocalSttServeCommand:
    def test_when_local_sidecar_starts__then_it_reads_runtime_settings_from_app_config(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_run_funasr_local_server(*, config, host, port, logger) -> None:
            captured["config"] = config
            captured["host"] = host
            captured["port"] = port

        monkeypatch.setattr(
            "vrc_live_caption.cli.run_funasr_local_server",
            fake_run_funasr_local_server,
        )
        config_path = config_file_factory(
            funasr_local_overrides={"host": "127.0.0.2", "port": 11095},
            funasr_local_sidecar_overrides={"device": "cpu"},
        )

        result = cli_runner.invoke(
            app,
            ["local-stt", "serve", "--config", str(config_path)],
        )

        assert result.exit_code == 0
        assert captured["host"] == "127.0.0.2"
        assert captured["port"] == 11095
        assert captured["config"].device == "cpu"
        assert f"App config: {config_path}" in result.output
        assert "Local STT device policy: cpu" in result.output
        assert "Starting local FunASR sidecar on ws://127.0.0.2:11095" in result.output

    def test_when_app_config_is_invalid__then_it_exits_non_zero(
        self,
        tmp_path: Path,
        cli_runner,
    ) -> None:
        config_path = tmp_path / "bad-local-stt.toml"
        config_path.write_text(
            "\n".join(
                [
                    "[stt.providers.funasr_local.sidecar]",
                    'device = "metal"',
                ]
            ),
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            app,
            ["local-stt", "serve", "--config", str(config_path)],
        )

        assert result.exit_code == 1
        assert (
            "stt.providers.funasr_local.sidecar.device must be one of: auto, cpu, cuda"
            in result.output
        )

    def test_when_runtime_raises_vrc_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        async def fake_run_funasr_local_server(**kwargs) -> None:
            raise AudioRuntimeError("sidecar failed")

        monkeypatch.setattr(
            "vrc_live_caption.cli.run_funasr_local_server",
            fake_run_funasr_local_server,
        )

        result = cli_runner.invoke(app, ["local-stt", "serve"])

        assert result.exit_code == 1
        assert "sidecar failed" in result.output

    def test_when_runtime_raises_unexpected_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        async def fake_run_funasr_local_server(**kwargs) -> None:
            raise RuntimeError("unexpected serve failure")

        monkeypatch.setattr(
            "vrc_live_caption.cli.run_funasr_local_server",
            fake_run_funasr_local_server,
        )

        result = cli_runner.invoke(app, ["local-stt", "serve"])

        assert result.exit_code == 1
        assert "unexpected serve failure" in result.output

    def test_when_keyboard_interrupt_happens__then_it_returns_cleanly(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        async def fake_run_funasr_local_server(**kwargs) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(
            "vrc_live_caption.cli.run_funasr_local_server",
            fake_run_funasr_local_server,
        )

        result = cli_runner.invoke(app, ["local-stt", "serve"])

        assert result.exit_code == 0
