"""Build and validate immutable application config models from TOML input."""

import tomllib
from enum import Enum
from pathlib import Path
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path("vrc-live-caption.toml")
_SUPPORTED_STT_PROVIDERS = {"funasr_local", "iflytek_rtasr", "openai_realtime"}
_SUPPORTED_TRANSLATION_PROVIDERS = {"deepl", "google_cloud"}
_SUPPORTED_TRANSLATION_OUTPUT_MODES = {"source", "target", "source_target"}
_SUPPORTED_TRANSLATION_STRATEGIES = {"final_only"}
_SUPPORTED_TRANSLATION_CHATBOX_LAYOUT_MODES = {"stacked_two_zone"}


def _coerce_int(value: Any, context: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{context} must be >= {minimum}")
    return value


def _coerce_float(value: Any, context: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{context} must be >= {minimum}")
    return result


def _coerce_str(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{context} must not be empty")
    return stripped


def _coerce_optional_str(value: Any, context: str) -> str | None:
    if value is None:
        return None
    return _coerce_str(value, context)


def _coerce_choice_str(value: Any, context: str, *, allowed: set[str]) -> str:
    result = _coerce_str(value, context)
    if result not in allowed:
        options = ", ".join(sorted(allowed))
        raise ValueError(f"{context} must be one of: {options}")
    return result


def _coerce_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def _coerce_path(value: Any, context: str) -> Path:
    if isinstance(value, Path):
        return value
    return Path(_coerce_str(value, context))


def parse_device_value(value: Any) -> int | str | None:
    """Parse `capture.device` into the capture selector shape."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("capture.device must be an integer, string, or omitted")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("capture.device must be >= 0")
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() == "default":
            return None
        return stripped
    raise ValueError("capture.device must be an integer, string, or omitted")


class LogLevel(str, Enum):
    """Enumerate supported log levels for console and file logging."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def parse_log_level(value: Any, context: str) -> LogLevel:
    """Parse one log level value using the shared validation rules."""
    normalized = _coerce_str(value, context).upper()
    try:
        return LogLevel(normalized)
    except ValueError as exc:
        options = ", ".join(level.value for level in LogLevel)
        raise ValueError(f"{context} must be one of: {options}") from exc


def _simplify_validation_message(message: str) -> str:
    return message.removeprefix("Value error, ")


def _format_config_validation_error(exc: ValidationError) -> str:
    extra_by_context: dict[str, list[str]] = {}
    messages: list[str] = []

    for error in exc.errors(include_url=False):
        loc = tuple(str(part) for part in error.get("loc", ()))
        if error.get("type") == "extra_forbidden" and loc:
            context = ".".join(loc[:-1]) or "root config"
            extra_by_context.setdefault(context, []).append(loc[-1])
            continue

        message = _simplify_validation_message(error["msg"])
        if loc and not message.startswith(".".join(loc)):
            message = f"{'.'.join(loc)}: {message}"
        messages.append(message)

    for context in sorted(extra_by_context):
        keys = ", ".join(sorted(extra_by_context[context]))
        messages.insert(0, f"Unknown keys in {context}: {keys}")

    return "; ".join(messages)


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaptureConfig(_ConfigModel):
    """Store audio capture settings and validate stream-related constraints."""

    device: int | str | None = None
    sample_rate: int = 16_000
    channels: int = 1
    dtype: str = "int16"
    block_duration_ms: int = 100

    @property
    def frames_per_chunk(self) -> int:
        """Return the input frame count produced for one configured block."""
        return int((self.sample_rate * self.block_duration_ms) / 1000)

    @field_validator("device", mode="before")
    @classmethod
    def _validate_device(cls, value: Any) -> int | str | None:
        return parse_device_value(value)

    @field_validator("sample_rate", "channels", "block_duration_ms", mode="before")
    @classmethod
    def _validate_int_fields(cls, value: Any, info: ValidationInfo) -> int:
        field_name = info.field_name
        assert field_name is not None
        minimums = {
            "sample_rate": 1,
            "channels": 1,
            "block_duration_ms": 10,
        }
        return _coerce_int(
            value,
            f"capture.{field_name}",
            minimum=minimums[field_name],
        )

    @field_validator("dtype", mode="before")
    @classmethod
    def _validate_dtype(cls, value: Any) -> str:
        return _coerce_str(value, "capture.dtype")


class PipelineConfig(_ConfigModel):
    """Store async pipeline runtime settings and bounded queue sizes."""

    audio_buffer_max_chunks: int = 50
    event_buffer_max_items: int = 200
    shutdown_timeout_seconds: float = 5.0
    heartbeat_seconds: int = 5

    @field_validator(
        "audio_buffer_max_chunks",
        "event_buffer_max_items",
        "heartbeat_seconds",
        mode="before",
    )
    @classmethod
    def _validate_int_fields(cls, value: Any, info: ValidationInfo) -> int:
        field_name = info.field_name
        assert field_name is not None
        minimums = {
            "audio_buffer_max_chunks": 1,
            "event_buffer_max_items": 1,
            "heartbeat_seconds": 1,
        }
        return _coerce_int(
            value,
            f"pipeline.{field_name}",
            minimum=minimums[field_name],
        )

    @field_validator("shutdown_timeout_seconds", mode="before")
    @classmethod
    def _validate_shutdown_timeout(cls, value: Any) -> float:
        return _coerce_float(
            value,
            "pipeline.shutdown_timeout_seconds",
            minimum=0.1,
        )


class LoggingConfig(_ConfigModel):
    """Store logging levels and file output settings."""

    console_level: LogLevel = LogLevel.WARNING
    file_level: LogLevel = LogLevel.INFO
    file_path: Path = Path(".runtime/logs/vrc-live-caption.log")
    max_bytes: int = 1_048_576
    backup_count: int = 3

    @field_validator("console_level", "file_level", mode="before")
    @classmethod
    def _validate_level(cls, value: Any, info: ValidationInfo) -> LogLevel:
        field_name = info.field_name
        assert field_name is not None
        return parse_log_level(value, f"logging.{field_name}")

    @field_validator("max_bytes", "backup_count", mode="before")
    @classmethod
    def _validate_int_fields(cls, value: Any, info: ValidationInfo) -> int:
        field_name = info.field_name
        assert field_name is not None
        minimums = {
            "max_bytes": 1,
            "backup_count": 1,
        }
        return _coerce_int(
            value,
            f"logging.{field_name}",
            minimum=minimums[field_name],
        )

    @field_validator("file_path", mode="before")
    @classmethod
    def _validate_file_path(cls, value: Any) -> Path:
        return _coerce_path(value, "logging.file_path")


class DebugConfig(_ConfigModel):
    """Store runtime scratch paths and lightweight diagnostic timing settings."""

    runtime_dir: Path = Path(".runtime")
    recordings_dir: Path = Path(".runtime/recordings")
    probe_seconds: float = 0.25

    @field_validator("runtime_dir", "recordings_dir", mode="before")
    @classmethod
    def _validate_paths(cls, value: Any, info: ValidationInfo) -> Path:
        return _coerce_path(value, f"debug.{info.field_name}")

    @field_validator("probe_seconds", mode="before")
    @classmethod
    def _validate_probe_seconds(cls, value: Any) -> float:
        return _coerce_float(value, "debug.probe_seconds", minimum=0.0)


class OscConfig(_ConfigModel):
    """Store OSC target settings for VRChat chatbox output."""

    host: str = "127.0.0.1"
    port: int = 9000
    notification_sfx: bool = False

    @field_validator("host", mode="before")
    @classmethod
    def _validate_host(cls, value: Any) -> str:
        return _coerce_str(value, "osc.host")

    @field_validator("port", mode="before")
    @classmethod
    def _validate_port(cls, value: Any) -> int:
        return _coerce_int(value, "osc.port", minimum=1)

    @field_validator("notification_sfx", mode="before")
    @classmethod
    def _validate_notification_sfx(cls, value: Any) -> bool:
        return _coerce_bool(value, "osc.notification_sfx")

    @model_validator(mode="after")
    def _validate_port_range(self) -> Self:
        if self.port > 65_535:
            raise ValueError("osc.port must be <= 65535")
        return self


class OpenAIRealtimeProviderConfig(_ConfigModel):
    """Store OpenAI Realtime transcription settings and VAD-related options."""

    model: str = "gpt-4o-transcribe"
    language: str | None = None
    prompt: str | None = None
    noise_reduction: str = "near_field"
    turn_detection: str = "server_vad"
    vad_prefix_padding_ms: int = 300
    vad_silence_duration_ms: int = 500
    vad_threshold: float = 0.5

    @field_validator("model", mode="before")
    @classmethod
    def _validate_model(cls, value: Any) -> str:
        return _coerce_str(value, "stt.providers.openai_realtime.model")

    @field_validator("language", "prompt", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: Any, info: ValidationInfo) -> str | None:
        return _coerce_optional_str(
            value, f"stt.providers.openai_realtime.{info.field_name}"
        )

    @field_validator("noise_reduction", mode="before")
    @classmethod
    def _validate_noise_reduction(cls, value: Any) -> str:
        return _coerce_choice_str(
            value,
            "stt.providers.openai_realtime.noise_reduction",
            allowed={"far_field", "near_field"},
        )

    @field_validator("turn_detection", mode="before")
    @classmethod
    def _validate_turn_detection(cls, value: Any) -> str:
        return _coerce_choice_str(
            value,
            "stt.providers.openai_realtime.turn_detection",
            allowed={"server_vad"},
        )

    @field_validator("vad_prefix_padding_ms", "vad_silence_duration_ms", mode="before")
    @classmethod
    def _validate_int_fields(cls, value: Any, info: ValidationInfo) -> int:
        field_name = info.field_name
        assert field_name is not None
        minimums = {
            "vad_prefix_padding_ms": 0,
            "vad_silence_duration_ms": 1,
        }
        return _coerce_int(
            value,
            f"stt.providers.openai_realtime.{field_name}",
            minimum=minimums[field_name],
        )

    @field_validator("vad_threshold", mode="before")
    @classmethod
    def _validate_vad_threshold(cls, value: Any) -> float:
        return _coerce_float(
            value,
            "stt.providers.openai_realtime.vad_threshold",
            minimum=0.0,
        )

    @model_validator(mode="after")
    def _validate_ranges(self) -> Self:
        if self.vad_threshold > 1.0:
            raise ValueError(
                "stt.providers.openai_realtime.vad_threshold must be <= 1.0"
            )
        return self


class IflytekRtasrProviderConfig(_ConfigModel):
    """Store iFLYTEK RTASR language, VAD mode, and optional domain settings."""

    language: str = "autodialect"
    vad_mode: str = "near_field"
    domain: str | None = None

    @field_validator("language", mode="before")
    @classmethod
    def _validate_language(cls, value: Any) -> str:
        return _coerce_str(value, "stt.providers.iflytek_rtasr.language")

    @field_validator("vad_mode", mode="before")
    @classmethod
    def _validate_vad_mode(cls, value: Any) -> str:
        return _coerce_choice_str(
            value,
            "stt.providers.iflytek_rtasr.vad_mode",
            allowed={"far_field", "near_field"},
        )

    @field_validator("domain", mode="before")
    @classmethod
    def _validate_domain(cls, value: Any) -> str | None:
        return _coerce_optional_str(value, "stt.providers.iflytek_rtasr.domain")


class FunasrLocalProviderConfig(_ConfigModel):
    """Store local FunASR sidecar connection settings."""

    host: str = "127.0.0.1"
    port: int = 10095
    use_ssl: bool = False

    @field_validator("host", mode="before")
    @classmethod
    def _validate_host(cls, value: Any) -> str:
        return _coerce_str(value, "stt.providers.funasr_local.host")

    @field_validator("port", mode="before")
    @classmethod
    def _validate_port(cls, value: Any) -> int:
        return _coerce_int(value, "stt.providers.funasr_local.port", minimum=1)

    @field_validator("use_ssl", mode="before")
    @classmethod
    def _validate_use_ssl(cls, value: Any) -> bool:
        return _coerce_bool(value, "stt.providers.funasr_local.use_ssl")

    @model_validator(mode="after")
    def _validate_ranges(self) -> Self:
        if self.port > 65_535:
            raise ValueError("stt.providers.funasr_local.port must be <= 65535")
        return self


class SttProvidersConfig(_ConfigModel):
    """Store provider-specific STT configuration blocks."""

    funasr_local: FunasrLocalProviderConfig = Field(
        default_factory=FunasrLocalProviderConfig
    )
    iflytek_rtasr: IflytekRtasrProviderConfig = Field(
        default_factory=IflytekRtasrProviderConfig
    )
    openai_realtime: OpenAIRealtimeProviderConfig = Field(
        default_factory=OpenAIRealtimeProviderConfig
    )


class SttRetryConfig(_ConfigModel):
    """Store retry and timeout settings shared by STT backends."""

    connect_timeout_seconds: float = 10.0
    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 5.0

    @field_validator(
        "connect_timeout_seconds",
        "initial_backoff_seconds",
        "max_backoff_seconds",
        mode="before",
    )
    @classmethod
    def _validate_float_fields(cls, value: Any, info: ValidationInfo) -> float:
        field_name = info.field_name
        assert field_name is not None
        minimums = {
            "connect_timeout_seconds": 0.1,
            "initial_backoff_seconds": 0.1,
            "max_backoff_seconds": 0.1,
        }
        return _coerce_float(
            value,
            f"stt.retry.{field_name}",
            minimum=minimums[field_name],
        )

    @field_validator("max_attempts", mode="before")
    @classmethod
    def _validate_max_attempts(cls, value: Any) -> int:
        return _coerce_int(value, "stt.retry.max_attempts", minimum=0)

    @model_validator(mode="after")
    def _validate_ranges(self) -> Self:
        if self.initial_backoff_seconds > self.max_backoff_seconds:
            raise ValueError(
                "stt.retry.initial_backoff_seconds must be <= stt.retry.max_backoff_seconds"
            )
        return self


class SttConfig(_ConfigModel):
    """Store the active STT provider, retry policy, and provider blocks."""

    provider: str = "openai_realtime"
    retry: SttRetryConfig = Field(default_factory=SttRetryConfig)
    providers: SttProvidersConfig = Field(default_factory=SttProvidersConfig)

    @field_validator("provider", mode="before")
    @classmethod
    def _validate_provider(cls, value: Any) -> str:
        return _coerce_choice_str(
            value,
            "stt.provider",
            allowed=_SUPPORTED_STT_PROVIDERS,
        )


class GoogleCloudTranslationProviderConfig(_ConfigModel):
    """Store Google Cloud Translation project and location settings."""

    project_id: str | None = None
    location: str = "global"

    @field_validator("project_id", mode="before")
    @classmethod
    def _validate_project_id(cls, value: Any) -> str | None:
        return _coerce_optional_str(
            value, "translation.providers.google_cloud.project_id"
        )

    @field_validator("location", mode="before")
    @classmethod
    def _validate_location(cls, value: Any) -> str:
        return _coerce_str(value, "translation.providers.google_cloud.location")


class TranslationChatboxLayoutConfig(_ConfigModel):
    """Store source-target stacked chatbox layout line budgets."""

    mode: str = "stacked_two_zone"
    source_visible_lines: int = 4
    separator_blank_lines: int = 1
    target_visible_lines: int = 4

    @field_validator("mode", mode="before")
    @classmethod
    def _validate_mode(cls, value: Any) -> str:
        return _coerce_choice_str(
            value,
            "translation.chatbox_layout.mode",
            allowed=_SUPPORTED_TRANSLATION_CHATBOX_LAYOUT_MODES,
        )

    @field_validator(
        "source_visible_lines",
        "separator_blank_lines",
        "target_visible_lines",
        mode="before",
    )
    @classmethod
    def _validate_int_fields(cls, value: Any, info: ValidationInfo) -> int:
        field_name = info.field_name
        assert field_name is not None
        minimums = {
            "source_visible_lines": 1,
            "separator_blank_lines": 1,
            "target_visible_lines": 1,
        }
        return _coerce_int(
            value,
            f"translation.chatbox_layout.{field_name}",
            minimum=minimums[field_name],
        )

    @model_validator(mode="after")
    def _validate_total_visible_lines(self) -> Self:
        total_visible_lines = (
            self.source_visible_lines
            + self.separator_blank_lines
            + self.target_visible_lines
        )
        if total_visible_lines > 9:
            raise ValueError(
                "translation.chatbox_layout source_visible_lines + "
                "separator_blank_lines + target_visible_lines must be <= 9"
            )
        return self


class TranslationProvidersConfig(_ConfigModel):
    """Store provider-specific translation configuration blocks."""

    google_cloud: GoogleCloudTranslationProviderConfig = Field(
        default_factory=GoogleCloudTranslationProviderConfig
    )


class TranslationConfig(_ConfigModel):
    """Store the translation feature toggle, provider, and rendering policy."""

    enabled: bool = False
    provider: str = "deepl"
    target_language: str | None = None
    source_language: str | None = None
    output_mode: str = "source_target"
    strategy: str = "final_only"
    request_timeout_seconds: float = 3.0
    max_pending_finals: int = 8
    chatbox_layout: TranslationChatboxLayoutConfig = Field(
        default_factory=TranslationChatboxLayoutConfig
    )
    providers: TranslationProvidersConfig = Field(
        default_factory=TranslationProvidersConfig
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def _validate_enabled(cls, value: Any) -> bool:
        return _coerce_bool(value, "translation.enabled")

    @field_validator("provider", mode="before")
    @classmethod
    def _validate_provider(cls, value: Any) -> str:
        return _coerce_choice_str(
            value,
            "translation.provider",
            allowed=_SUPPORTED_TRANSLATION_PROVIDERS,
        )

    @field_validator("target_language", "source_language", mode="before")
    @classmethod
    def _validate_optional_language(
        cls, value: Any, info: ValidationInfo
    ) -> str | None:
        return _coerce_optional_str(value, f"translation.{info.field_name}")

    @field_validator("output_mode", mode="before")
    @classmethod
    def _validate_output_mode(cls, value: Any) -> str:
        return _coerce_choice_str(
            value,
            "translation.output_mode",
            allowed=_SUPPORTED_TRANSLATION_OUTPUT_MODES,
        )

    @field_validator("strategy", mode="before")
    @classmethod
    def _validate_strategy(cls, value: Any) -> str:
        return _coerce_choice_str(
            value,
            "translation.strategy",
            allowed=_SUPPORTED_TRANSLATION_STRATEGIES,
        )

    @field_validator("request_timeout_seconds", mode="before")
    @classmethod
    def _validate_request_timeout(cls, value: Any) -> float:
        return _coerce_float(
            value,
            "translation.request_timeout_seconds",
            minimum=0.1,
        )

    @field_validator("max_pending_finals", mode="before")
    @classmethod
    def _validate_max_pending_finals(cls, value: Any) -> int:
        return _coerce_int(
            value,
            "translation.max_pending_finals",
            minimum=1,
        )

    @model_validator(mode="after")
    def _validate_enabled_requirements(self) -> Self:
        if not self.enabled:
            return self
        if self.target_language is None:
            raise ValueError(
                "translation.target_language is required when translation.enabled = true"
            )
        if (
            self.provider == "google_cloud"
            and self.providers.google_cloud.project_id is None
        ):
            raise ValueError(
                'translation.providers.google_cloud.project_id is required when translation.provider = "google_cloud"'
            )
        return self


class AppConfig(_ConfigModel):
    """Store the full application config tree with validated subsystem defaults."""

    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    osc: OscConfig = Field(default_factory=OscConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)

    @classmethod
    def default_path(cls) -> Path:
        """Return the default repository-relative application config path."""
        return DEFAULT_CONFIG_PATH

    @classmethod
    def from_toml_file(
        cls,
        path: Path | None = None,
        *,
        required: bool = True,
    ) -> Self:
        """Load config from TOML and validate it against the application models."""
        resolved = path or cls.default_path()
        if not resolved.exists():
            if required:
                raise ConfigError(
                    f"Config file not found: {resolved}. Create it from vrc-live-caption.toml.example."
                )
            return cls()

        try:
            with resolved.open("rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Failed to parse TOML config {resolved}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Failed to read config {resolved}: {exc}") from exc

        if not isinstance(data, dict):
            raise ConfigError(
                f"Config file {resolved} must define a TOML table at the root"
            )

        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(_format_config_validation_error(exc)) from exc


__all__ = [
    "AppConfig",
    "CaptureConfig",
    "ConfigError",
    "DebugConfig",
    "FunasrLocalProviderConfig",
    "IflytekRtasrProviderConfig",
    "LogLevel",
    "LoggingConfig",
    "OpenAIRealtimeProviderConfig",
    "OscConfig",
    "PipelineConfig",
    "GoogleCloudTranslationProviderConfig",
    "SttConfig",
    "SttProvidersConfig",
    "SttRetryConfig",
    "TranslationChatboxLayoutConfig",
    "TranslationConfig",
    "TranslationProvidersConfig",
    "parse_device_value",
    "parse_log_level",
]
