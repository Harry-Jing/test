import json
from collections.abc import Mapping
from pathlib import Path

from vrc_live_caption.config import (
    AppConfig,
    CaptureConfig,
    DebugConfig,
    FunasrLocalProviderConfig,
    IflytekRtasrProviderConfig,
    LoggingConfig,
    LogLevel,
    OpenAIRealtimeProviderConfig,
    OscConfig,
    PipelineConfig,
    SttConfig,
    SttProvidersConfig,
    SttRetryConfig,
)


def build_config(tmp_path: Path, **capture_overrides: object) -> AppConfig:
    capture_values: dict[str, object] = {
        "device": None,
        "sample_rate": 16_000,
        "channels": 1,
        "dtype": "int16",
        "block_duration_ms": 100,
    }
    capture_values.update(capture_overrides)
    return AppConfig(
        capture=CaptureConfig(**capture_values),
        pipeline=PipelineConfig(
            audio_buffer_max_chunks=50,
            event_buffer_max_items=200,
            heartbeat_seconds=1,
            shutdown_timeout_seconds=5.0,
        ),
        logging=LoggingConfig(
            console_level=LogLevel.WARNING,
            file_level=LogLevel.INFO,
            file_path=tmp_path / ".runtime" / "logs" / "test.log",
            max_bytes=1_048_576,
            backup_count=1,
        ),
        debug=DebugConfig(
            runtime_dir=tmp_path / ".runtime",
            recordings_dir=tmp_path / ".runtime" / "recordings",
            probe_seconds=0.0,
        ),
        osc=OscConfig(
            host="127.0.0.1",
            port=9000,
            notification_sfx=False,
        ),
        stt=SttConfig(
            provider="iflytek_rtasr",
            retry=SttRetryConfig(),
            providers=SttProvidersConfig(
                funasr_local=FunasrLocalProviderConfig(),
                iflytek_rtasr=IflytekRtasrProviderConfig(),
                openai_realtime=OpenAIRealtimeProviderConfig(),
            ),
        ),
    )


def write_test_config(
    path: Path,
    *,
    capture_overrides: Mapping[str, object] | None = None,
    audio_overrides: Mapping[str, object] | None = None,
    pipeline_overrides: Mapping[str, object] | None = None,
    logging_overrides: Mapping[str, object] | None = None,
    debug_overrides: Mapping[str, object] | None = None,
    osc_overrides: Mapping[str, object] | None = None,
    stt_overrides: Mapping[str, object] | None = None,
    stt_retry_overrides: Mapping[str, object] | None = None,
    funasr_local_overrides: Mapping[str, object] | None = None,
    iflytek_rtasr_overrides: Mapping[str, object] | None = None,
    openai_realtime_overrides: Mapping[str, object] | None = None,
) -> Path:
    capture_values: dict[str, object] = {
        "sample_rate": 16_000,
        "channels": 1,
        "dtype": "int16",
        "block_duration_ms": 100,
    }
    pipeline_values: dict[str, object] = {
        "audio_buffer_max_chunks": 50,
        "event_buffer_max_items": 200,
        "shutdown_timeout_seconds": 5.0,
        "heartbeat_seconds": 1,
    }
    logging_values: dict[str, object] = {
        "console_level": "WARNING",
        "file_level": "INFO",
        "file_path": (path.parent / ".runtime" / "logs" / "cli.log").as_posix(),
    }
    debug_values: dict[str, object] = {
        "runtime_dir": (path.parent / ".runtime").as_posix(),
        "recordings_dir": (path.parent / ".runtime" / "recordings").as_posix(),
        "probe_seconds": 0.0,
    }
    osc_values: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 9000,
        "notification_sfx": False,
    }
    stt_values: dict[str, object] = {
        "provider": "iflytek_rtasr",
    }
    stt_retry_values: dict[str, object] = {
        "connect_timeout_seconds": 10.0,
        "max_attempts": 3,
        "initial_backoff_seconds": 1.0,
        "max_backoff_seconds": 5.0,
    }
    iflytek_rtasr_values: dict[str, object] = {
        "language": "autodialect",
        "vad_mode": "near_field",
    }
    funasr_local_values: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 10095,
        "use_ssl": False,
    }
    openai_realtime_values: dict[str, object] = {
        "model": "gpt-4o-transcribe",
        "noise_reduction": "near_field",
        "turn_detection": "server_vad",
        "vad_prefix_padding_ms": 300,
        "vad_silence_duration_ms": 500,
        "vad_threshold": 0.5,
    }

    capture_values.update(audio_overrides or {})
    capture_values.update(capture_overrides or {})
    pipeline_values.update(pipeline_overrides or {})
    logging_values.update(logging_overrides or {})
    debug_values.update(debug_overrides or {})
    osc_values.update(osc_overrides or {})
    stt_values.update(stt_overrides or {})
    if "backend" in stt_values and "provider" not in stt_values:
        stt_values["provider"] = stt_values.pop("backend")
    stt_retry_values.update(stt_retry_overrides or {})
    if "max_reconnect_attempts" in stt_values:
        stt_retry_values["max_attempts"] = stt_values.pop("max_reconnect_attempts")
    if "connect_timeout_seconds" in stt_values:
        stt_retry_values["connect_timeout_seconds"] = stt_values.pop(
            "connect_timeout_seconds"
        )
    if "initial_backoff_seconds" in stt_values:
        stt_retry_values["initial_backoff_seconds"] = stt_values.pop(
            "initial_backoff_seconds"
        )
    if "max_backoff_seconds" in stt_values:
        stt_retry_values["max_backoff_seconds"] = stt_values.pop("max_backoff_seconds")
    funasr_local_values.update(funasr_local_overrides or {})
    iflytek_rtasr_values.update(iflytek_rtasr_overrides or {})
    openai_realtime_values.update(openai_realtime_overrides or {})

    sections = [
        ("capture", capture_values),
        ("pipeline", pipeline_values),
        ("logging", logging_values),
        ("debug", debug_values),
        ("osc", osc_values),
        ("stt", stt_values),
        ("stt.retry", stt_retry_values),
        ("stt.providers.funasr_local", funasr_local_values),
        ("stt.providers.iflytek_rtasr", iflytek_rtasr_values),
        ("stt.providers.openai_realtime", openai_realtime_values),
    ]
    lines: list[str] = []
    for section_name, values in sections:
        lines.append(f"[{section_name}]")
        for key, value in values.items():
            if value is None:
                continue
            lines.append(f"{key} = {_toml_literal(value)}")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _toml_literal(value: object) -> str:
    if isinstance(value, Path):
        return json.dumps(value.as_posix())
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    raise TypeError(f"Unsupported TOML literal type: {type(value).__name__}")
