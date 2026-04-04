"""Parse and validate the local FunASR sidecar configuration."""

import tomllib
from pathlib import Path
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from ...errors import ConfigError

DEFAULT_LOCAL_STT_CONFIG_PATH = Path("local-stt-funasr.toml")


def _coerce_int(value: Any, context: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{context} must be >= {minimum}")
    return value


def _coerce_str(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{context} must not be empty")
    return result


def _coerce_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def _coerce_path(value: Any, context: str) -> Path:
    if isinstance(value, Path):
        return value
    return Path(_coerce_str(value, context))


def _format_validation_error(exc: ValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors(include_url=False):
        loc = ".".join(str(part) for part in error.get("loc", ()))
        message = error["msg"].removeprefix("Value error, ")
        if loc:
            messages.append(f"{loc}: {message}")
        else:
            messages.append(message)
    return "; ".join(messages)


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FunasrLocalServiceConfig(_ConfigModel):
    """Store the sidecar runtime models and inference settings."""

    mode: str = "2pass"
    device: str = "cpu"
    ncpu: int = 4
    offline_asr_model: str = "paraformer-zh"
    online_asr_model: str = "paraformer-zh-streaming"
    vad_model: str = "fsmn-vad"
    punc_model: str = "ct-punc"
    chunk_size: tuple[int, int, int] = (0, 10, 5)
    chunk_interval: int = 10
    encoder_chunk_look_back: int = 4
    decoder_chunk_look_back: int = 1
    log_path: Path = Path(".runtime/logs/local-stt-funasr.log")

    @property
    def online_window_ms(self) -> int:
        """Return the online inference window size in milliseconds."""
        return self.chunk_size[1] * 60

    @property
    def packet_duration_ms(self) -> int:
        """Return the internal packet duration used by VAD and streaming ASR."""
        return int(self.online_window_ms / self.chunk_interval)

    @property
    def chunk_size_list(self) -> list[int]:
        """Return the chunk size in the list form expected by FunASR."""
        return list(self.chunk_size)

    @classmethod
    def default_path(cls) -> Path:
        """Return the default repository-relative local STT config path."""
        return DEFAULT_LOCAL_STT_CONFIG_PATH

    @classmethod
    def from_toml_file(
        cls,
        path: Path | None = None,
        *,
        required: bool = True,
    ) -> Self:
        """Load the local sidecar config from TOML."""
        resolved = path or cls.default_path()
        if not resolved.exists():
            if required:
                raise ConfigError(
                    f"Local STT config file not found: {resolved}. Create it from local-stt-funasr.toml.example."
                )
            return cls()

        try:
            with resolved.open("rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"Failed to parse local STT TOML config {resolved}: {exc}"
            ) from exc
        except OSError as exc:
            raise ConfigError(f"Failed to read local STT config {resolved}: {exc}") from exc

        if not isinstance(data, dict):
            raise ConfigError(
                f"Local STT config file {resolved} must define a TOML table at the root"
            )

        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(_format_validation_error(exc)) from exc

    @field_validator("mode", mode="before")
    @classmethod
    def _validate_mode(cls, value: Any) -> str:
        result = _coerce_str(value, "mode")
        if result != "2pass":
            raise ValueError("mode must be 2pass")
        return result

    @field_validator("device", mode="before")
    @classmethod
    def _validate_device(cls, value: Any) -> str:
        result = _coerce_str(value, "device")
        if result not in {"cpu", "cuda"}:
            raise ValueError("device must be one of: cpu, cuda")
        return result

    @field_validator(
        "offline_asr_model", "online_asr_model", "vad_model", mode="before"
    )
    @classmethod
    def _validate_model_strings(cls, value: Any, info: ValidationInfo) -> str:
        return _coerce_str(value, info.field_name or "model")

    @field_validator(
        "encoder_chunk_look_back",
        "decoder_chunk_look_back",
        mode="before",
    )
    @classmethod
    def _validate_int_fields(cls, value: Any, info: ValidationInfo) -> int:
        return _coerce_int(value, info.field_name or "int_field", minimum=0)

    @field_validator("log_path", mode="before")
    @classmethod
    def _validate_log_path(cls, value: Any) -> Path:
        return _coerce_path(value, "log_path")

    @field_validator("chunk_size", mode="before")
    @classmethod
    def _validate_chunk_size(cls, value: Any) -> tuple[int, int, int]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("chunk_size must be an array of 3 integers")
        if len(value) != 3:
            raise ValueError("chunk_size must contain exactly 3 integers")
        result = (
            _coerce_int(value[0], "chunk_size", minimum=0),
            _coerce_int(value[1], "chunk_size", minimum=0),
            _coerce_int(value[2], "chunk_size", minimum=0),
        )
        if result[1] < 1:
            raise ValueError("chunk_size[1] must be >= 1")
        return result

    @field_validator("punc_model", mode="before")
    @classmethod
    def _validate_punc_model(cls, value: Any) -> str:
        return _coerce_str(value, "punc_model")

    @field_validator("ncpu", mode="before")
    @classmethod
    def _validate_ncpu(cls, value: Any) -> int:
        return _coerce_int(value, "ncpu", minimum=1)

    @field_validator("chunk_interval", mode="before")
    @classmethod
    def _validate_chunk_interval(cls, value: Any) -> int:
        return _coerce_int(value, "chunk_interval", minimum=1)
