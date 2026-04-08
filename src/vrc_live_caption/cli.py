"""Define the Typer CLI and compose the async pipeline used by commands."""

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .audio import AudioBackendError, AudioDeviceInfo, SoundDeviceBackend
from .chatbox import ChatboxOutput
from .config import AppConfig, ConfigError, LoggingConfig, LogLevel
from .env import AppSecrets, SecretError
from .errors import OscError, VrcLiveCaptionError
from .local_stt.funasr import FunasrLocalServiceConfig, run_funasr_local_server
from .local_translation.translategemma import (
    TranslateGemmaLocalServiceConfig,
    run_translategemma_local_server,
)
from .logging_utils import configure_logging
from .osc import OscChatboxTransport
from .pipeline import LivePipelineController, record_audio_sample
from .runtime import DropOldestAsyncQueue, MicrophoneCapture, default_recording_path
from .stt import (
    AsyncSttSessionRunner,
    create_stt_backend,
    describe_stt_backend,
    probe_funasr_local_service,
    validate_stt_secrets,
)
from .translation import (
    create_translation_backend,
    describe_translation_backend,
    probe_translategemma_local_service,
    validate_translation_runtime,
)

_INTERRUPT_EXCEPTIONS = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)

app = typer.Typer(
    help="VRC Live Caption CLI for audio diagnostics, live transcription, and local STT and translation sidecars.",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
local_stt_app = typer.Typer(
    help="Manage repository-local STT sidecars.",
    add_completion=False,
    no_args_is_help=True,
)
app.add_typer(local_stt_app, name="local-stt")
local_translation_app = typer.Typer(
    help="Manage repository-local translation sidecars.",
    add_completion=False,
    no_args_is_help=True,
)
app.add_typer(local_translation_app, name="local-translation")


ConfigPathOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="Path to the TOML config file.",
        rich_help_panel="Configuration",
    ),
]
ConsoleLogLevelOption = Annotated[
    LogLevel | None,
    typer.Option(
        "--console-log-level",
        help="Override the console log level for this command.",
        case_sensitive=False,
        rich_help_panel="Logging",
    ),
]
FileLogLevelOption = Annotated[
    LogLevel | None,
    typer.Option(
        "--file-log-level",
        help="Override the file log level for this command.",
        case_sensitive=False,
        rich_help_panel="Logging",
    ),
]
RecordingSecondsOption = Annotated[
    float,
    typer.Option(
        "--seconds",
        min=0.1,
        help="Recording duration in seconds.",
        rich_help_panel="Recording",
    ),
]
RecordingOutputOption = Annotated[
    Path | None,
    typer.Option(
        "--output",
        help="Optional WAV output path.",
        rich_help_panel="Recording",
    ),
]
LocalSttConfigPathOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="Path to the local STT sidecar TOML config file.",
        rich_help_panel="Configuration",
    ),
]
LocalSttHostOption = Annotated[
    str,
    typer.Option(
        "--host",
        help="Host interface for the local STT sidecar websocket server.",
        rich_help_panel="Network",
    ),
]
LocalSttPortOption = Annotated[
    int,
    typer.Option(
        "--port",
        min=1,
        max=65_535,
        help="Port for the local STT sidecar websocket server.",
        rich_help_panel="Network",
    ),
]
LocalTranslationConfigPathOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="Path to the local translation sidecar TOML config file.",
        rich_help_panel="Configuration",
    ),
]
LocalTranslationHostOption = Annotated[
    str,
    typer.Option(
        "--host",
        help="Host interface for the local translation sidecar websocket server.",
        rich_help_panel="Network",
    ),
]
LocalTranslationPortOption = Annotated[
    int,
    typer.Option(
        "--port",
        min=1,
        max=65_535,
        help="Port for the local translation sidecar websocket server.",
        rich_help_panel="Network",
    ),
]


def _show_version(value: bool) -> None:
    if not value:
        return
    typer.echo(__version__)
    raise typer.Exit()


@app.callback()
def app_callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_show_version,
            is_eager=True,
            help="Show the application version and exit.",
        ),
    ] = False,
) -> None:
    """Register root CLI options that should run before any command executes."""
    return None


def create_audio_backend() -> SoundDeviceBackend:
    """Build the default audio backend used by CLI commands."""
    return SoundDeviceBackend()


def create_osc_chatbox_transport(
    *,
    app_config: AppConfig,
    logger: logging.Logger,
) -> OscChatboxTransport:
    """Build the OSC chatbox transport from validated app config."""
    return OscChatboxTransport(osc_config=app_config.osc, logger=logger)


def create_live_pipeline_controller(
    *,
    app_config: AppConfig,
    logger: logging.Logger,
    emit_line,
) -> LivePipelineController:
    """Build the full async `run` pipeline from capture, STT, and OSC pieces."""
    secrets = AppSecrets()
    audio_queue = DropOldestAsyncQueue(
        max_items=app_config.pipeline.audio_buffer_max_chunks,
        logger=logger.getChild("queue"),
        label="audio queue",
    )
    capture = MicrophoneCapture(
        capture_config=app_config.capture,
        queue=audio_queue,
        backend=create_audio_backend(),
        logger=logger.getChild("capture"),
    )
    backend = create_stt_backend(
        capture_config=app_config.capture,
        stt_config=app_config.stt,
        secrets=secrets,
        logger=logger.getChild("stt"),
    )
    translation_backend = create_translation_backend(
        translation_config=app_config.translation,
        secrets=secrets,
        logger=logger.getChild("translation"),
    )
    session_runner = AsyncSttSessionRunner(
        backend=backend,
        retry_config=app_config.stt.retry,
        audio_queue=audio_queue,
        event_buffer_max_items=app_config.pipeline.event_buffer_max_items,
        logger=logger.getChild("stt.runner"),
    )
    transport = create_osc_chatbox_transport(
        app_config=app_config,
        logger=logger.getChild("osc"),
    )
    transcript_output = ChatboxOutput(
        transport=transport,
        emit_line=emit_line,
        logger=logger.getChild("chatbox"),
        translation_config=app_config.translation,
        translation_backend=translation_backend,
    )
    return LivePipelineController(
        capture=capture,
        session_runner=session_runner,
        transcript_output=transcript_output,
        emit_line=emit_line,
        heartbeat_seconds=app_config.pipeline.heartbeat_seconds,
        shutdown_timeout_seconds=app_config.pipeline.shutdown_timeout_seconds,
        logger=logger.getChild("pipeline"),
    )


def _build_capture(
    *,
    app_config: AppConfig,
    logger: logging.Logger,
) -> MicrophoneCapture:
    return MicrophoneCapture(
        capture_config=app_config.capture,
        queue=DropOldestAsyncQueue(
            max_items=app_config.pipeline.audio_buffer_max_chunks,
            logger=logger.getChild("queue"),
            label="audio queue",
        ),
        backend=create_audio_backend(),
        logger=logger,
    )


def _load_required_config(config_path: Path | None) -> tuple[AppConfig, Path]:
    resolved = config_path or AppConfig.default_path()
    return AppConfig.from_toml_file(resolved, required=True), resolved


def _load_optional_config(config_path: Path | None) -> tuple[AppConfig, Path, bool]:
    resolved = config_path or AppConfig.default_path()
    exists = resolved.exists()
    return AppConfig.from_toml_file(resolved, required=False), resolved, exists


def _load_optional_local_stt_config(
    config_path: Path | None,
) -> tuple[FunasrLocalServiceConfig, Path, bool]:
    resolved = config_path or FunasrLocalServiceConfig.default_path()
    exists = resolved.exists()
    return (
        FunasrLocalServiceConfig.from_toml_file(resolved, required=False),
        resolved,
        exists,
    )


def _load_optional_local_translation_config(
    config_path: Path | None,
) -> tuple[TranslateGemmaLocalServiceConfig, Path, bool]:
    resolved = config_path or TranslateGemmaLocalServiceConfig.default_path()
    exists = resolved.exists()
    return (
        TranslateGemmaLocalServiceConfig.from_toml_file(resolved, required=False),
        resolved,
        exists,
    )


def _exit_with_error(message: str, *, code: int = 1) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code)


def _parse_cli_bool(value: str, *, context: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{context} must be true or false")


def _format_device_row(device: AudioDeviceInfo) -> str:
    default_marker = "*" if device.is_default else " "
    rate = int(device.default_sample_rate)
    return (
        f"{default_marker} {device.index:>3}  {device.name}  "
        f"in={device.max_input_channels}  rate={rate}"
    )


def _apply_logging_overrides(
    config: LoggingConfig,
    *,
    console_log_level: LogLevel | None,
    file_log_level: LogLevel | None,
) -> LoggingConfig:
    updates: dict[str, LogLevel] = {}

    if console_log_level is not None:
        updates["console_level"] = console_log_level

    if file_log_level is not None:
        updates["file_level"] = file_log_level

    if not updates:
        return config
    return config.model_copy(update=updates)


def _log_shutdown_failure(
    logger: logging.Logger,
    message: str,
    exc: BaseException,
) -> None:
    if isinstance(exc, VrcLiveCaptionError):
        logger.error("%s: %s", message, exc)
        return
    logger.exception(message)


def _consume_current_task_cancellation() -> bool:
    """Clear pending cancellation on the current task after the first Ctrl+C."""
    task = asyncio.current_task()
    if task is None:
        return False

    uncancel = getattr(task, "uncancel", None)
    if uncancel is None:
        return False

    while task.cancelling():
        if uncancel() == 0:
            return True
    return True


async def _run_live_command(
    *,
    app_config: AppConfig,
    resolved_config_path: Path,
    root_logger: logging.Logger,
) -> None:
    controller = create_live_pipeline_controller(
        app_config=app_config,
        logger=root_logger,
        emit_line=typer.echo,
    )
    shutdown_logger = root_logger.getChild("cli")
    try:
        await controller.start()
        typer.echo(f"Running with config: {resolved_config_path}")
        typer.echo(
            "Input device: "
            f"{controller.resolved_device.label if controller.resolved_device else 'unresolved'}"
        )
        typer.echo(f"OSC target: {app_config.osc.host}:{app_config.osc.port}")
        typer.echo(f"STT backend: {controller.backend_description}")
        if app_config.translation.enabled:
            typer.echo(
                "Translation: "
                f"{describe_translation_backend(app_config.translation)} "
                f"-> {app_config.translation.target_language} "
                f"(mode={app_config.translation.output_mode}, "
                f"strategy={app_config.translation.strategy})"
            )
        typer.echo("Press Ctrl+C to stop.")
        await controller.run_forever()
    except asyncio.CancelledError:
        if not _consume_current_task_cancellation():
            raise
        typer.echo("Stopping... Press Ctrl+C again to force exit.")
    finally:
        try:
            await controller.stop()
        except _INTERRUPT_EXCEPTIONS:
            raise
        except Exception as exc:
            _log_shutdown_failure(shutdown_logger, "Runner shutdown failed", exc)


async def _run_record_sample_command(
    *,
    app_config: AppConfig,
    output_path: Path,
    seconds: float,
    logger: logging.Logger,
) -> None:
    capture = _build_capture(app_config=app_config, logger=logger.getChild("capture"))
    await record_audio_sample(
        capture=capture,
        output_path=output_path,
        duration_seconds=seconds,
        logger=logger.getChild("recording"),
    )


@app.command(short_help="List audio input devices.")
def devices() -> None:
    """List available audio input devices."""
    try:
        backend = create_audio_backend()
        device_list = backend.list_input_devices()
    except AudioBackendError as exc:
        _exit_with_error(str(exc))

    if not device_list:
        typer.echo("No input audio devices found.")
        raise typer.Exit(code=1)

    typer.echo("Default Index Name")
    for device in device_list:
        typer.echo(_format_device_row(device))


@app.command(short_help="Validate config and audio setup.")
def doctor(
    config: ConfigPathOption = None,
    console_log_level: ConsoleLogLevelOption = None,
    file_log_level: FileLogLevelOption = None,
) -> None:
    """Validate config, audio backend availability, and a short input stream probe."""
    try:
        app_config, resolved_config_path, config_exists = _load_optional_config(config)
    except ConfigError as exc:
        _exit_with_error(str(exc))
    logging_config = _apply_logging_overrides(
        app_config.logging,
        console_log_level=console_log_level,
        file_log_level=file_log_level,
    )
    root_logger = configure_logging(logging_config)
    logger = root_logger.getChild("cli")
    backend = create_audio_backend()
    exit_code = 0
    logger.info(
        "Doctor command started: config=%s config_exists=%s",
        resolved_config_path,
        config_exists,
    )

    typer.echo("Doctor checks:")

    if config_exists:
        typer.echo(f"[ok] config loaded: {resolved_config_path}")
    else:
        typer.echo(
            f"[warn] config missing: {resolved_config_path} (doctor used built-in defaults)"
        )

    try:
        device = backend.resolve_input_device(app_config.capture.device)
        typer.echo(f"[ok] input device resolved: {device.label}")
    except AudioBackendError as exc:
        typer.echo(f"[error] input device resolution failed: {exc}")
        typer.echo(
            "        Hint: run `vrc-live-caption devices` and update [capture].device."
        )
        raise typer.Exit(code=1)

    try:
        backend.probe_input_stream(
            capture_config=app_config.capture,
            device_index=device.index,
            duration_seconds=app_config.debug.probe_seconds,
        )
        typer.echo(
            f"[ok] stream probe succeeded: {app_config.debug.probe_seconds:.2f}s"
        )
    except AudioBackendError as exc:
        exit_code = 1
        logger.error("Doctor stream probe failed: %s", exc)
        typer.echo(f"[error] stream probe failed: {exc}")
        typer.echo(
            "        Hint: check OS microphone permissions and sample-rate compatibility."
        )

    try:
        transport = create_osc_chatbox_transport(
            app_config=app_config,
            logger=root_logger.getChild("osc"),
        )
        typer.echo(f"[ok] osc target configured: {transport.host}:{transport.port}")
    except OscError as exc:
        exit_code = 1
        typer.echo(f"[error] osc target configuration failed: {exc}")

    typer.echo(f"[ok] stt backend configured: {describe_stt_backend(app_config.stt)}")
    if app_config.stt.provider == "funasr_local":
        try:
            probe_result = asyncio.run(
                probe_funasr_local_service(
                    capture_config=app_config.capture,
                    provider_config=app_config.stt.providers.funasr_local,
                    timeout_seconds=app_config.stt.retry.connect_timeout_seconds,
                )
            )
            if probe_result.resolved_device:
                suffix = f": {probe_result.resolved_device}"
                if probe_result.device_policy:
                    suffix += f" (policy={probe_result.device_policy})"
                typer.echo(f"[ok] local STT sidecar reachable{suffix}")
            else:
                typer.echo("[ok] local STT sidecar reachable")
        except Exception as exc:
            exit_code = 1
            typer.echo(f"[error] local STT sidecar check failed: {exc}")
            typer.echo(
                "        Hint: start `vrc-live-caption local-stt serve` and confirm host/port match [stt.providers.funasr_local]."
            )
    else:
        try:
            validate_stt_secrets(stt_config=app_config.stt, secrets=AppSecrets())
            typer.echo("[ok] required STT secrets found")
        except SecretError as exc:
            exit_code = 1
            typer.echo(f"[error] {exc}")
            typer.echo(
                "        Hint: create a .env file in the repo root or export the backend-specific credentials."
            )

    if app_config.translation.enabled:
        if app_config.translation.provider == "translategemma_local":
            try:
                probe_result = probe_translategemma_local_service(
                    provider_config=app_config.translation.providers.translategemma_local,
                    timeout_seconds=app_config.translation.request_timeout_seconds,
                )
                details: list[str] = []
                if probe_result.model:
                    details.append(f"model={probe_result.model}")
                if probe_result.resolved_device:
                    details.append(f"device={probe_result.resolved_device}")
                if probe_result.device_policy:
                    details.append(f"policy={probe_result.device_policy}")
                if probe_result.resolved_dtype:
                    details.append(f"dtype={probe_result.resolved_dtype}")
                suffix = f": {', '.join(details)}" if details else ""
                typer.echo(f"[ok] local translation sidecar reachable{suffix}")
                typer.echo(
                    "[ok] translation configured: "
                    f"{describe_translation_backend(app_config.translation)} "
                    f"-> {app_config.translation.target_language} "
                    f"(mode={app_config.translation.output_mode}, "
                    f"strategy={app_config.translation.strategy})"
                )
            except VrcLiveCaptionError as exc:
                exit_code = 1
                typer.echo(f"[error] {exc}")
                typer.echo(
                    "        Hint: start `vrc-live-caption local-translation serve` and confirm host/port match [translation.providers.translategemma_local]."
                )
        else:
            try:
                validate_translation_runtime(
                    translation_config=app_config.translation,
                    secrets=AppSecrets(),
                    logger=root_logger.getChild("translation"),
                )
                typer.echo(
                    "[ok] translation configured: "
                    f"{describe_translation_backend(app_config.translation)} "
                    f"-> {app_config.translation.target_language} "
                    f"(mode={app_config.translation.output_mode}, "
                    f"strategy={app_config.translation.strategy})"
                )
            except (SecretError, VrcLiveCaptionError) as exc:
                exit_code = 1
                typer.echo(f"[error] {exc}")
                typer.echo(
                    "        Hint: set DEEPL_AUTH_KEY for DeepL, or configure Google ADC plus translation.providers.google_cloud.project_id."
                )

    raise typer.Exit(code=exit_code)


@app.command("osc-test", short_help="Send a test message to the VRChat Chatbox.")
def osc_test(
    text: Annotated[
        str,
        typer.Argument(help="Text to send to the VRChat chatbox."),
    ] = "OSC test",
    config: ConfigPathOption = None,
    console_log_level: ConsoleLogLevelOption = None,
    file_log_level: FileLogLevelOption = None,
    typing: Annotated[
        str | None,
        typer.Option(
            "--typing",
            help="Optional typing state to send before the chatbox text (true/false).",
            rich_help_panel="OSC",
        ),
    ] = None,
) -> None:
    """Send a one-off OSC chatbox message without requiring STT credentials."""
    try:
        app_config, resolved_config_path = _load_required_config(config)
    except ConfigError as exc:
        _exit_with_error(str(exc))

    logging_config = _apply_logging_overrides(
        app_config.logging,
        console_log_level=console_log_level,
        file_log_level=file_log_level,
    )
    root_logger = configure_logging(logging_config)
    logger = root_logger.getChild("cli")
    logger.info(
        "osc-test command started: config=%s host=%s port=%s",
        resolved_config_path,
        app_config.osc.host,
        app_config.osc.port,
    )

    try:
        transport = create_osc_chatbox_transport(
            app_config=app_config,
            logger=root_logger.getChild("osc"),
        )
        if typing is not None:
            typing_state = _parse_cli_bool(typing, context="--typing")
            transport.send_typing(typing_state)
            typer.echo(f"Sent typing state: {typing_state}")
        transport.send_text(text)
    except ValueError as exc:
        _exit_with_error(str(exc))
    except VrcLiveCaptionError as exc:
        logger.error("osc-test failed: %s", exc)
        _exit_with_error(str(exc))
    except Exception as exc:
        logger.exception("osc-test failed")
        _exit_with_error(str(exc))

    typer.echo(f"Sent chatbox text to {transport.host}:{transport.port}")
    typer.echo(f"[chatbox] {text}")


@app.command(short_help="Run live speech transcription.")
def run(
    config: ConfigPathOption = None,
    console_log_level: ConsoleLogLevelOption = None,
    file_log_level: FileLogLevelOption = None,
) -> None:
    """Run the microphone-to-STT transcription pipeline."""
    try:
        app_config, resolved_config_path = _load_required_config(config)
    except ConfigError as exc:
        _exit_with_error(str(exc))

    logging_config = _apply_logging_overrides(
        app_config.logging,
        console_log_level=console_log_level,
        file_log_level=file_log_level,
    )
    root_logger = configure_logging(logging_config)
    logger = root_logger.getChild("cli")
    logger.info(
        "Run command started: config=%s provider=%s",
        resolved_config_path,
        app_config.stt.provider,
    )

    try:
        asyncio.run(
            _run_live_command(
                app_config=app_config,
                resolved_config_path=resolved_config_path,
                root_logger=root_logger,
            )
        )
    except VrcLiveCaptionError as exc:
        logger.error("Run failed: %s", exc)
        _exit_with_error(str(exc))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        logger.exception("Runtime failed")
        _exit_with_error(str(exc))


@app.command("record-sample", short_help="Record a debug WAV sample.")
def record_sample(
    config: ConfigPathOption = None,
    console_log_level: ConsoleLogLevelOption = None,
    file_log_level: FileLogLevelOption = None,
    seconds: RecordingSecondsOption = 10.0,
    output: RecordingOutputOption = None,
) -> None:
    """Record a debug WAV sample using the same capture service as `run`."""
    try:
        app_config, resolved_config_path = _load_required_config(config)
    except ConfigError as exc:
        _exit_with_error(str(exc))

    logging_config = _apply_logging_overrides(
        app_config.logging,
        console_log_level=console_log_level,
        file_log_level=file_log_level,
    )
    root_logger = configure_logging(logging_config)
    logger = root_logger.getChild("cli")
    output_path = output or default_recording_path(app_config.debug.recordings_dir)
    logger.info(
        "record-sample command started: config=%s output=%s duration_seconds=%.2f",
        resolved_config_path,
        output_path,
        seconds,
    )

    try:
        typer.echo(
            f"Recording {seconds:.2f}s sample with config: {resolved_config_path}"
        )
        asyncio.run(
            _run_record_sample_command(
                app_config=app_config,
                output_path=output_path,
                seconds=seconds,
                logger=root_logger,
            )
        )
    except VrcLiveCaptionError as exc:
        logger.error("record-sample failed: %s", exc)
        _exit_with_error(str(exc))
    except Exception as exc:
        logger.exception("record-sample failed")
        _exit_with_error(str(exc))

    typer.echo(f"Recorded sample: {output_path}")


@local_stt_app.command("serve", short_help="Run the local FunASR STT sidecar.")
def local_stt_serve(
    config: LocalSttConfigPathOption = None,
    host: LocalSttHostOption = "127.0.0.1",
    port: LocalSttPortOption = 10095,
    console_log_level: ConsoleLogLevelOption = None,
    file_log_level: FileLogLevelOption = None,
) -> None:
    """Run the repository-local FunASR websocket sidecar."""
    try:
        local_config, resolved_config_path, config_exists = (
            _load_optional_local_stt_config(config)
        )
    except ConfigError as exc:
        _exit_with_error(str(exc))

    logging_config = _apply_logging_overrides(
        LoggingConfig(file_path=local_config.log_path),
        console_log_level=console_log_level,
        file_log_level=file_log_level,
    )
    root_logger = configure_logging(logging_config)
    logger = root_logger.getChild("cli")
    logger.info(
        "local-stt serve started: config=%s config_exists=%s host=%s port=%s",
        resolved_config_path,
        config_exists,
        host,
        port,
    )

    if config_exists:
        typer.echo(f"Local STT config: {resolved_config_path}")
    else:
        typer.echo(
            f"[warn] local STT config missing: {resolved_config_path} (serve used built-in defaults)"
        )
    typer.echo(f"Local STT device policy: {local_config.device}")
    typer.echo(f"Starting local FunASR sidecar on ws://{host}:{port}")

    try:
        asyncio.run(
            run_funasr_local_server(
                config=local_config,
                host=host,
                port=port,
                logger=root_logger.getChild("local_stt.funasr"),
            )
        )
    except KeyboardInterrupt:
        return
    except VrcLiveCaptionError as exc:
        logger.error("local-stt serve failed: %s", exc)
        _exit_with_error(str(exc))
    except Exception as exc:
        logger.exception("local-stt serve failed")
        _exit_with_error(str(exc))


@local_translation_app.command(
    "serve", short_help="Run the local TranslateGemma translation sidecar."
)
def local_translation_serve(
    config: LocalTranslationConfigPathOption = None,
    host: LocalTranslationHostOption = "127.0.0.1",
    port: LocalTranslationPortOption = 10096,
    console_log_level: ConsoleLogLevelOption = None,
    file_log_level: FileLogLevelOption = None,
) -> None:
    """Run the repository-local TranslateGemma websocket sidecar."""
    try:
        local_config, resolved_config_path, config_exists = (
            _load_optional_local_translation_config(config)
        )
    except ConfigError as exc:
        _exit_with_error(str(exc))

    logging_config = _apply_logging_overrides(
        LoggingConfig(file_path=local_config.log_path),
        console_log_level=console_log_level,
        file_log_level=file_log_level,
    )
    root_logger = configure_logging(logging_config)
    logger = root_logger.getChild("cli")
    logger.info(
        "local-translation serve started: config=%s config_exists=%s host=%s port=%s",
        resolved_config_path,
        config_exists,
        host,
        port,
    )

    if config_exists:
        typer.echo(f"Local translation config: {resolved_config_path}")
    else:
        typer.echo(
            "[warn] local translation config missing: "
            f"{resolved_config_path} (serve used built-in defaults)"
        )
    typer.echo(f"Local translation model: {local_config.model}")
    typer.echo(f"Local translation device policy: {local_config.device}")
    typer.echo(f"Local translation dtype policy: {local_config.dtype}")
    typer.echo(f"Starting local TranslateGemma sidecar on ws://{host}:{port}")

    try:
        asyncio.run(
            run_translategemma_local_server(
                config=local_config,
                host=host,
                port=port,
                logger=root_logger.getChild("local_translation.translategemma"),
            )
        )
    except KeyboardInterrupt:
        return
    except VrcLiveCaptionError as exc:
        logger.error("local-translation serve failed: %s", exc)
        _exit_with_error(str(exc))
    except Exception as exc:
        logger.exception("local-translation serve failed")
        _exit_with_error(str(exc))


def main() -> None:
    """Run the Typer application entrypoint."""
    app()
