"""Parse and validate the local TranslateGemma sidecar configuration."""

import tomllib
from pathlib import Path
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
)

from ...errors import ConfigError

DEFAULT_LOCAL_TRANSLATION_CONFIG_PATH = Path("local-translation-translategemma.toml")


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


class TranslateGemmaLocalServiceConfig(_ConfigModel):
    """Store the sidecar runtime model and inference settings."""

    model: str = "google/translategemma-4b-it"
    device: str = "auto"
    dtype: str = "auto"
    max_new_tokens: int = 256
    log_path: Path = Path(".runtime/logs/local-translation-translategemma.log")

    @classmethod
    def default_path(cls) -> Path:
        """Return the default repository-relative local translation config path."""
        return DEFAULT_LOCAL_TRANSLATION_CONFIG_PATH

    @classmethod
    def from_toml_file(
        cls,
        path: Path | None = None,
        *,
        required: bool = True,
    ) -> Self:
        """Load the local translation sidecar config from TOML."""
        resolved = path or cls.default_path()
        if not resolved.exists():
            if required:
                raise ConfigError(
                    "Local translation config file not found: "
                    f"{resolved}. Create it from local-translation-translategemma.toml.example."
                )
            return cls()

        try:
            with resolved.open("rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"Failed to parse local translation TOML config {resolved}: {exc}"
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"Failed to read local translation config {resolved}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ConfigError(
                "Local translation config file "
                f"{resolved} must define a TOML table at the root"
            )

        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(_format_validation_error(exc)) from exc

    @field_validator("model", mode="before")
    @classmethod
    def _validate_model(cls, value: Any) -> str:
        return _coerce_str(value, "model")

    @field_validator("device", mode="before")
    @classmethod
    def _validate_device(cls, value: Any) -> str:
        result = _coerce_str(value, "device")
        if result not in {"auto", "cpu", "cuda"}:
            raise ValueError("device must be one of: auto, cpu, cuda")
        return result

    @field_validator("dtype", mode="before")
    @classmethod
    def _validate_dtype(cls, value: Any) -> str:
        result = _coerce_str(value, "dtype")
        if result not in {"auto", "bfloat16", "float32"}:
            raise ValueError("dtype must be one of: auto, bfloat16, float32")
        return result

    @field_validator("max_new_tokens", mode="before")
    @classmethod
    def _validate_max_new_tokens(cls, value: Any) -> int:
        return _coerce_int(value, "max_new_tokens", minimum=1)

    @field_validator("log_path", mode="before")
    @classmethod
    def _validate_log_path(cls, value: Any) -> Path:
        return _coerce_path(value, "log_path")


__all__ = [
    "DEFAULT_LOCAL_TRANSLATION_CONFIG_PATH",
    "TranslateGemmaLocalServiceConfig",
]
