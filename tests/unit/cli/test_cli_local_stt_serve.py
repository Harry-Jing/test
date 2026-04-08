from pathlib import Path
from typing import Any

from vrc_live_caption.cli import app
from vrc_live_caption.errors import AudioRuntimeError
from vrc_live_caption.local_stt.funasr.server import FunasrLocalServerReadyInfo


class TestLocalSttServeCommand:
    def test_when_local_sidecar_starts__then_it_reads_runtime_settings_from_app_config(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_run_funasr_local_server(
            *,
            config,
            host,
            port,
            logger,
            ready_callback,
        ) -> None:
            captured["config"] = config
            captured["host"] = host
            captured["port"] = port
            ready_callback(
                FunasrLocalServerReadyInfo(
                    endpoint=f"ws://{host}:{port}",
                    resolved_device="cpu",
                    device_policy=config.device,
                )
            )

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
        assert "Endpoint: ws://127.0.0.2:11095" in result.output
        assert "Device policy: cpu" in result.output
        assert "Offline ASR model: paraformer-zh" in result.output
        assert "Online ASR model: paraformer-zh-streaming" in result.output
        assert "VAD model: fsmn-vad" in result.output
        assert "Punctuation model: ct-punc" in result.output
        assert "Log file:" in result.output
        assert (
            "[info] Loading models and opening websocket listener..." in result.output
        )
        assert (
            "[ok] Local FunASR sidecar ready: ws://127.0.0.2:11095, device=cpu, policy=cpu"
            in result.output
        )
        assert "Keep this terminal open. Press Ctrl+C to stop." in result.output

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
        assert "Stop requested. Shutting down local FunASR sidecar." in result.output
