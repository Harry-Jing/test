import asyncio

from vrc_live_caption.audio import AudioDeviceInfo
from vrc_live_caption.cli import app
from vrc_live_caption.errors import AudioRuntimeError, SecretError


class _FakeController:
    def __init__(self) -> None:
        self.resolved_device = AudioDeviceInfo(
            index=7,
            name="Fake Mic",
            max_input_channels=1,
            default_sample_rate=16_000.0,
            is_default=True,
        )
        self.backend_description = "iflytek_rtasr (autodialect, near_field)"

    async def start(self) -> None:
        return None

    async def run_forever(self) -> None:
        raise KeyboardInterrupt

    async def stop(self) -> None:
        return None


class TestRunCommandHelp:
    def test_when_short_help_flag_is_used__then_it_matches_long_help(
        self,
        cli_runner,
    ) -> None:
        long_help = cli_runner.invoke(app, ["run", "--help"])
        short_help = cli_runner.invoke(app, ["run", "-h"])

        assert long_help.exit_code == 0
        assert short_help.exit_code == 0
        assert short_help.output == long_help.output

    def test_when_help_is_rendered__then_it_shows_grouped_panels_and_log_level_choices(
        self,
        cli_runner,
    ) -> None:
        result = cli_runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "Configuration" in result.output
        assert "Logging" in result.output


class TestRunCommand:
    def test_when_config_file_is_missing__then_it_exits_non_zero(
        self,
        tmp_path,
        cli_runner,
    ) -> None:
        missing_config = tmp_path / "missing.toml"

        result = cli_runner.invoke(app, ["run", "--config", str(missing_config)])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_when_controller_starts_successfully__then_it_prints_runtime_summary(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_live_pipeline_controller",
            lambda *, app_config, logger, emit_line: _FakeController(),
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 0
        assert f"Config: {config_path}" in result.output
        assert "Input device selector: default" in result.output
        assert "OSC target: 127.0.0.1:9000" in result.output
        assert "STT backend: iflytek_rtasr (autodialect, near_field)" in result.output
        assert "Translation: disabled" in result.output
        assert "Log file:" in result.output
        assert "[info] Starting runtime pipeline..." in result.output
        assert "[ok] Runtime ready: input_device=" in result.output
        assert "Fake Mic" in result.output

    def test_when_asyncio_cancellation_happens__then_it_stops_gracefully(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        class CancelledController(_FakeController):
            def __init__(self) -> None:
                super().__init__()
                self.stop_calls = 0

            async def run_forever(self) -> None:
                raise asyncio.CancelledError

            async def stop(self) -> None:
                self.stop_calls += 1

        controller = CancelledController()
        shutdown_failures: list[tuple[str, BaseException]] = []
        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_live_pipeline_controller",
            lambda *, app_config, logger, emit_line: controller,
        )
        monkeypatch.setattr(
            "vrc_live_caption.cli._log_shutdown_failure",
            lambda logger, message, exc: shutdown_failures.append((message, exc)),
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 0
        assert controller.stop_calls == 1
        assert shutdown_failures == []
        assert "Stopping... Press Ctrl+C again to force exit." in result.output

    def test_when_controller_creation_fails__then_it_prints_startup_summary_first(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_live_pipeline_controller",
            lambda *, app_config, logger, emit_line: (_ for _ in ()).throw(
                AudioRuntimeError("controller build failed")
            ),
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 1
        assert f"Config: {config_path}" in result.output
        assert "Translation: disabled" in result.output
        assert "[info] Starting runtime pipeline..." in result.output
        assert "controller build failed" in result.output

    def test_when_iflytek_secret_is_missing__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.env.AppSecrets.require_iflytek_credentials",
            lambda self: (_ for _ in ()).throw(
                SecretError(
                    "IFLYTEK_APP_ID not found. Add it to .env or set the environment variable."
                )
            ),
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "IFLYTEK_APP_ID not found" in result.output

    def test_when_openai_secret_is_missing__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        config_path = config_file_factory(stt_overrides={"provider": "openai_realtime"})
        monkeypatch.setattr(
            "vrc_live_caption.env.AppSecrets.require_openai_credentials",
            lambda self: (_ for _ in ()).throw(
                SecretError(
                    "OPENAI_API_KEY not found. Add it to .env or set the environment variable."
                )
            ),
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "OPENAI_API_KEY not found" in result.output

    def test_when_runtime_raises_vrc_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        async def fake_run_live_command(**kwargs) -> None:
            raise AudioRuntimeError("capture failed")

        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli._run_live_command", fake_run_live_command
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "capture failed" in result.output

    def test_when_runtime_raises_unexpected_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        async def fake_run_live_command(**kwargs) -> None:
            raise RuntimeError("unexpected failure")

        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli._run_live_command", fake_run_live_command
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "unexpected failure" in result.output

    def test_when_keyboard_interrupt_happens__then_it_returns_cleanly(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        async def fake_run_live_command(**kwargs) -> None:
            raise KeyboardInterrupt

        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli._run_live_command", fake_run_live_command
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 0

    def test_when_translation_is_enabled__then_it_prints_the_translation_summary(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        config_path = config_file_factory(
            translation_overrides={
                "enabled": True,
                "provider": "deepl",
                "target_language": "en",
                "output_mode": "source_target",
            }
        )
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_live_pipeline_controller",
            lambda *, app_config, logger, emit_line: _FakeController(),
        )

        result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 0
        assert (
            "Translation: deepl -> en (mode=source_target, strategy=final_only)"
            in result.output
        )
