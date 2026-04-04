import asyncio
import importlib
import logging
import runpy
import sys
from pathlib import Path

import pytest

from tests.support.audio_fakes import FakeBackend
from vrc_live_caption import __version__
from vrc_live_caption import cli as cli_module
from vrc_live_caption.audio import AudioDeviceInfo
from vrc_live_caption.cli import app
from vrc_live_caption.config import ConfigError, LoggingConfig, LogLevel
from vrc_live_caption.errors import AudioRuntimeError, OscError, SecretError
from vrc_live_caption.stt.funasr_local import FunasrLocalReadyEvent


class _FakeOscTransport:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 9000
        self.text_messages: list[str] = []
        self.typing_messages: list[bool] = []

    def send_text(self, text: str) -> None:
        self.text_messages.append(text)

    def send_typing(self, is_typing: bool) -> None:
        self.typing_messages.append(is_typing)


def test_devices_lists_input_devices(monkeypatch, cli_runner) -> None:
    backend = FakeBackend(
        devices=[
            AudioDeviceInfo(
                index=4,
                name="USB Mic",
                max_input_channels=1,
                default_sample_rate=48_000.0,
                is_default=True,
            )
        ]
    )
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)

    result = cli_runner.invoke(app, ["devices"])

    assert result.exit_code == 0
    assert "USB Mic" in result.output
    assert "*" in result.output


def test_devices_reports_backend_errors(monkeypatch, cli_runner) -> None:
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_audio_backend",
        lambda: FakeBackend(list_error="backend down"),
    )

    result = cli_runner.invoke(app, ["devices"])

    assert result.exit_code == 1
    assert "backend down" in result.output


def test_devices_exits_non_zero_when_no_input_devices_exist(
    monkeypatch, cli_runner
) -> None:
    class EmptyBackend:
        def list_input_devices(self):
            return []

    monkeypatch.setattr(
        "vrc_live_caption.cli.create_audio_backend",
        lambda: EmptyBackend(),
    )

    result = cli_runner.invoke(app, ["devices"])

    assert result.exit_code == 1
    assert "No input audio devices found." in result.output


def test_root_help_shows_description_and_version_option(cli_runner) -> None:
    result = cli_runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "local STT sidecars" in result.output
    assert "--version" in result.output


def test_root_short_help_alias_matches_long_help(cli_runner) -> None:
    long_help = cli_runner.invoke(app, ["--help"])
    short_help = cli_runner.invoke(app, ["-h"])

    assert long_help.exit_code == 0
    assert short_help.exit_code == 0
    assert short_help.output == long_help.output


def test_command_short_help_alias_matches_long_help(cli_runner) -> None:
    long_help = cli_runner.invoke(app, ["run", "--help"])
    short_help = cli_runner.invoke(app, ["run", "-h"])

    assert long_help.exit_code == 0
    assert short_help.exit_code == 0
    assert short_help.output == long_help.output


def test_version_option_prints_package_version(cli_runner) -> None:
    result = cli_runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_python_m_entrypoint_invokes_cli_main(monkeypatch) -> None:
    called = False

    def fake_main() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("vrc_live_caption.cli.main", fake_main)

    runpy.run_module("vrc_live_caption", run_name="__main__", alter_sys=True)

    assert called is True


def test_importing_main_module_does_not_invoke_cli_main(monkeypatch) -> None:
    called = False

    def fake_main() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("vrc_live_caption.cli.main", fake_main)
    sys.modules.pop("vrc_live_caption.__main__", None)

    importlib.import_module("vrc_live_caption.__main__")

    assert called is False


def test_doctor_warns_when_config_is_missing(
    monkeypatch, tmp_cwd: Path, cli_runner
) -> None:
    backend = FakeBackend()
    osc_transport = _FakeOscTransport()
    missing_config = tmp_cwd / "missing.toml"
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: osc_transport,
    )

    result = cli_runner.invoke(
        app,
        ["doctor", "--config", str(missing_config)],
        env={
            "IFLYTEK_APP_ID": "app-id",
            "IFLYTEK_API_KEY": "api-key",
            "IFLYTEK_API_SECRET": "api-secret",
        },
    )

    assert result.exit_code == 0
    assert "[warn] config missing" in result.output
    assert "[ok] stream probe succeeded" in result.output
    assert "[ok] osc target configured: 127.0.0.1:9000" in result.output
    assert "[ok] required STT secrets found" in result.output


def test_run_requires_config_file(tmp_path: Path, cli_runner) -> None:
    missing_config = tmp_path / "missing.toml"

    result = cli_runner.invoke(app, ["run", "--config", str(missing_config)])

    assert result.exit_code == 1
    assert "Config file not found" in result.output


def test_doctor_reports_missing_iflytek_secrets(
    monkeypatch,
    tmp_cwd: Path,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend()
    osc_transport = _FakeOscTransport()
    config_path = config_file_factory()
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: osc_transport,
    )
    monkeypatch.delenv("IFLYTEK_APP_ID", raising=False)
    monkeypatch.delenv("IFLYTEK_API_KEY", raising=False)
    monkeypatch.delenv("IFLYTEK_API_SECRET", raising=False)

    result = cli_runner.invoke(app, ["doctor", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "IFLYTEK_APP_ID not found" in result.output


def test_doctor_reports_device_resolution_failures(
    monkeypatch,
    tmp_cwd: Path,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend()
    osc_transport = _FakeOscTransport()
    config_path = config_file_factory(capture_overrides={"device": "Missing Mic"})
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: osc_transport,
    )

    result = cli_runner.invoke(app, ["doctor", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "[error] input device resolution failed" in result.output
    assert "Hint: run `vrc-live-caption devices`" in result.output


def test_doctor_reports_probe_failures(
    monkeypatch,
    tmp_cwd: Path,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend(probe_error="probe failed")
    osc_transport = _FakeOscTransport()
    config_path = config_file_factory()
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: osc_transport,
    )

    result = cli_runner.invoke(
        app,
        ["doctor", "--config", str(config_path)],
        env={
            "IFLYTEK_APP_ID": "app-id",
            "IFLYTEK_API_KEY": "api-key",
            "IFLYTEK_API_SECRET": "api-secret",
        },
    )

    assert result.exit_code == 1
    assert "[error] stream probe failed: probe failed" in result.output


def test_doctor_cli_log_level_flags_override_config(
    monkeypatch,
    tmp_cwd: Path,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend()
    osc_transport = _FakeOscTransport()
    config_path = config_file_factory()
    captured = {}

    def fake_configure_logging(config):
        captured["config"] = config
        return logging.getLogger("test.cli.logging")

    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: osc_transport,
    )
    monkeypatch.setattr(
        "vrc_live_caption.cli.configure_logging", fake_configure_logging
    )

    result = cli_runner.invoke(
        app,
        [
            "doctor",
            "--config",
            str(config_path),
            "--file-log-level",
            "debug",
            "--console-log-level",
            "error",
        ],
        env={
            "IFLYTEK_APP_ID": "app-id",
            "IFLYTEK_API_KEY": "api-key",
            "IFLYTEK_API_SECRET": "api-secret",
        },
    )

    assert result.exit_code == 0
    assert captured["config"].console_level == LogLevel.ERROR
    assert captured["config"].file_level == LogLevel.DEBUG


def test_apply_logging_overrides_returns_existing_config_when_no_updates() -> None:
    config = LoggingConfig()

    assert (
        cli_module._apply_logging_overrides(
            config,
            console_log_level=None,
            file_log_level=None,
        )
        is config
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("YES", True),
        ("0", False),
        ("off", False),
    ],
)
def test_parse_cli_bool_accepts_expected_values(raw: str, expected: bool) -> None:
    assert cli_module._parse_cli_bool(raw, context="--typing") is expected


def test_parse_cli_bool_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="--typing must be true or false"):
        cli_module._parse_cli_bool("maybe", context="--typing")


def test_consume_current_task_cancellation_handles_missing_task(monkeypatch) -> None:
    monkeypatch.setattr("vrc_live_caption.cli.asyncio.current_task", lambda: None)

    assert cli_module._consume_current_task_cancellation() is False


def test_consume_current_task_cancellation_handles_task_without_uncancel(
    monkeypatch,
) -> None:
    class FakeTask:
        def cancelling(self) -> int:
            return 1

    monkeypatch.setattr(
        "vrc_live_caption.cli.asyncio.current_task",
        lambda: FakeTask(),
    )

    assert cli_module._consume_current_task_cancellation() is False


def test_consume_current_task_cancellation_uncancels_pending_state(monkeypatch) -> None:
    class FakeTask:
        def __init__(self) -> None:
            self.remaining = 2

        def cancelling(self) -> int:
            return self.remaining

        def uncancel(self) -> int:
            self.remaining -= 1
            return self.remaining

    task = FakeTask()
    monkeypatch.setattr("vrc_live_caption.cli.asyncio.current_task", lambda: task)

    assert cli_module._consume_current_task_cancellation() is True
    assert task.remaining == 0


def test_run_help_shows_grouped_panels_and_log_level_choices(cli_runner) -> None:
    result = cli_runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    assert "Configuration" in result.output
    assert "Logging" in result.output


def test_record_sample_help_shows_recording_panel(cli_runner) -> None:
    result = cli_runner.invoke(app, ["record-sample", "--help"])

    assert result.exit_code == 0
    assert "Recording" in result.output


def test_run_uses_live_pipeline_controller(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    class FakeController:
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

    config_path = config_file_factory()
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_live_pipeline_controller",
        lambda *, app_config, logger, emit_line: FakeController(),
    )

    result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "OSC target: 127.0.0.1:9000" in result.output
    assert "STT backend: iflytek_rtasr (autodialect, near_field)" in result.output


def test_run_gracefully_stops_after_asyncio_cancellation(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    class FakeController:
        def __init__(self) -> None:
            self.resolved_device = AudioDeviceInfo(
                index=7,
                name="Fake Mic",
                max_input_channels=1,
                default_sample_rate=16_000.0,
                is_default=True,
            )
            self.backend_description = "iflytek_rtasr (autodialect, near_field)"
            self.stop_calls = 0

        async def start(self) -> None:
            return None

        async def run_forever(self) -> None:
            raise asyncio.CancelledError

        async def stop(self) -> None:
            self.stop_calls += 1

    controller = FakeController()
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


def test_run_reports_missing_iflytek_secret(
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


def test_run_reports_vrc_runtime_errors(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    async def fake_run_live_command(**kwargs) -> None:
        raise AudioRuntimeError("capture failed")

    config_path = config_file_factory()
    monkeypatch.setattr("vrc_live_caption.cli._run_live_command", fake_run_live_command)

    result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "capture failed" in result.output


def test_run_reports_unexpected_runtime_errors(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    async def fake_run_live_command(**kwargs) -> None:
        raise RuntimeError("unexpected failure")

    config_path = config_file_factory()
    monkeypatch.setattr("vrc_live_caption.cli._run_live_command", fake_run_live_command)

    result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "unexpected failure" in result.output


def test_run_returns_cleanly_on_keyboard_interrupt(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    async def fake_run_live_command(**kwargs) -> None:
        raise KeyboardInterrupt

    config_path = config_file_factory()
    monkeypatch.setattr("vrc_live_caption.cli._run_live_command", fake_run_live_command)

    result = cli_runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0


def test_doctor_accepts_openai_backend_when_openai_key_is_present(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend()
    osc_transport = _FakeOscTransport()
    config_path = config_file_factory(stt_overrides={"provider": "openai_realtime"})
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: osc_transport,
    )

    result = cli_runner.invoke(
        app,
        ["doctor", "--config", str(config_path)],
        env={"OPENAI_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    assert "[ok] stt backend configured: openai_realtime" in result.output


def test_doctor_checks_local_funasr_sidecar_without_secret_validation(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend()
    osc_transport = _FakeOscTransport()
    config_path = config_file_factory(stt_overrides={"provider": "funasr_local"})
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: osc_transport,
    )

    async def fake_probe(**kwargs):
        return FunasrLocalReadyEvent(
            message="ready",
            resolved_device="cuda:0",
            device_policy="auto",
        )

    monkeypatch.setattr("vrc_live_caption.cli.probe_funasr_local_service", fake_probe)
    monkeypatch.setattr(
        "vrc_live_caption.cli.validate_stt_secrets",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    result = cli_runner.invoke(app, ["doctor", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "[ok] stt backend configured: funasr_local" in result.output
    assert "[ok] local STT sidecar reachable: cuda:0 (policy=auto)" in result.output


def test_local_stt_serve_uses_local_sidecar_entrypoint(
    monkeypatch,
    tmp_cwd: Path,
    cli_runner,
) -> None:
    captured = {}

    async def fake_run_funasr_local_server(*, config, host, port, logger) -> None:
        captured["config"] = config
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(
        "vrc_live_caption.cli.run_funasr_local_server",
        fake_run_funasr_local_server,
    )

    result = cli_runner.invoke(
        app,
        ["local-stt", "serve", "--host", "127.0.0.1", "--port", "10095"],
    )

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 10095
    assert "Local STT device policy: auto" in result.output
    assert "Starting local FunASR sidecar on ws://127.0.0.1:10095" in result.output


def test_local_stt_serve_reports_config_errors(monkeypatch, cli_runner) -> None:
    monkeypatch.setattr(
        "vrc_live_caption.cli._load_optional_local_stt_config",
        lambda config: (_ for _ in ()).throw(ConfigError("bad local stt config")),
    )

    result = cli_runner.invoke(app, ["local-stt", "serve"])

    assert result.exit_code == 1
    assert "bad local stt config" in result.output


def test_local_stt_serve_reports_vrc_runtime_errors(monkeypatch, cli_runner) -> None:
    async def fake_run_funasr_local_server(**kwargs) -> None:
        raise AudioRuntimeError("sidecar failed")

    monkeypatch.setattr(
        "vrc_live_caption.cli.run_funasr_local_server",
        fake_run_funasr_local_server,
    )

    result = cli_runner.invoke(app, ["local-stt", "serve"])

    assert result.exit_code == 1
    assert "sidecar failed" in result.output


def test_local_stt_serve_reports_unexpected_runtime_errors(
    monkeypatch, cli_runner
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


def test_local_stt_serve_returns_on_keyboard_interrupt(monkeypatch, cli_runner) -> None:
    async def fake_run_funasr_local_server(**kwargs) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "vrc_live_caption.cli.run_funasr_local_server",
        fake_run_funasr_local_server,
    )

    result = cli_runner.invoke(app, ["local-stt", "serve"])

    assert result.exit_code == 0


def test_run_reports_missing_openai_secret_for_openai_backend(
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


def test_record_sample_writes_output_file(
    monkeypatch,
    tmp_cwd: Path,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend()
    config_path = config_file_factory()
    output_path = tmp_cwd / "sample.wav"
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)

    result = cli_runner.invoke(
        app,
        [
            "record-sample",
            "--config",
            str(config_path),
            "--seconds",
            "0.1",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    assert f"Recorded sample: {output_path}" in result.output


def test_record_sample_reports_capture_failures(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend(fail_on_start=True)
    config_path = config_file_factory()
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)

    result = cli_runner.invoke(app, ["record-sample", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Failed to start audio capture" in result.output


def test_record_sample_reports_vrc_runtime_errors(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    async def fake_record_sample_command(**kwargs) -> None:
        raise AudioRuntimeError("recording failed")

    config_path = config_file_factory()
    monkeypatch.setattr(
        "vrc_live_caption.cli._run_record_sample_command",
        fake_record_sample_command,
    )

    result = cli_runner.invoke(app, ["record-sample", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "recording failed" in result.output


def test_record_sample_reports_unexpected_errors(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    async def fake_record_sample_command(**kwargs) -> None:
        raise RuntimeError("unexpected recording failure")

    config_path = config_file_factory()
    monkeypatch.setattr(
        "vrc_live_caption.cli._run_record_sample_command",
        fake_record_sample_command,
    )

    result = cli_runner.invoke(app, ["record-sample", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "unexpected recording failure" in result.output


def test_osc_test_sends_text_and_optional_typing(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    osc_transport = _FakeOscTransport()
    config_path = config_file_factory()
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: osc_transport,
    )

    result = cli_runner.invoke(
        app,
        [
            "osc-test",
            "hello world",
            "--config",
            str(config_path),
            "--typing",
            "true",
        ],
    )

    assert result.exit_code == 0
    assert osc_transport.typing_messages == [True]
    assert osc_transport.text_messages == ["hello world"]
    assert "Sent chatbox text to 127.0.0.1:9000" in result.output
    assert "[chatbox] hello world" in result.output


def test_osc_test_rejects_invalid_typing_values(
    config_file_factory,
    cli_runner,
) -> None:
    config_path = config_file_factory()

    result = cli_runner.invoke(
        app,
        [
            "osc-test",
            "hello world",
            "--config",
            str(config_path),
            "--typing",
            "maybe",
        ],
    )

    assert result.exit_code == 1
    assert "--typing must be true or false" in result.output


def test_osc_test_reports_vrc_runtime_errors(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    class BrokenOscTransport(_FakeOscTransport):
        def send_text(self, text: str) -> None:
            raise OscError("osc send failed")

    config_path = config_file_factory()
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: BrokenOscTransport(),
    )

    result = cli_runner.invoke(app, ["osc-test", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "osc send failed" in result.output


def test_osc_test_reports_unexpected_errors(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    class BrokenOscTransport(_FakeOscTransport):
        def send_text(self, text: str) -> None:
            raise RuntimeError("unexpected osc failure")

    config_path = config_file_factory()
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: BrokenOscTransport(),
    )

    result = cli_runner.invoke(app, ["osc-test", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "unexpected osc failure" in result.output


def test_doctor_reports_osc_configuration_errors(
    monkeypatch,
    config_file_factory,
    cli_runner,
) -> None:
    backend = FakeBackend()
    config_path = config_file_factory()
    monkeypatch.setattr("vrc_live_caption.cli.create_audio_backend", lambda: backend)
    monkeypatch.setattr(
        "vrc_live_caption.cli.create_osc_chatbox_transport",
        lambda *, app_config, logger: (_ for _ in ()).throw(OscError("bad osc")),
    )

    result = cli_runner.invoke(
        app,
        ["doctor", "--config", str(config_path)],
        env={
            "IFLYTEK_APP_ID": "app-id",
            "IFLYTEK_API_KEY": "api-key",
            "IFLYTEK_API_SECRET": "api-secret",
        },
    )

    assert result.exit_code == 1
    assert "[error] osc target configuration failed: bad osc" in result.output
