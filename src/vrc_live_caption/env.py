"""Loads application secrets from the environment and optional `.env` files.

Keeps provider-specific credential requirements separate from config parsing.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import SecretError

DEFAULT_DOTENV_PATH = Path(".env")
_MISSING_OPENAI_API_KEY_MESSAGE = (
    "OPENAI_API_KEY not found. Add it to .env or set the environment variable."
)
_MISSING_IFLYTEK_APP_ID_MESSAGE = (
    "IFLYTEK_APP_ID not found. Add it to .env or set the environment variable."
)
_MISSING_IFLYTEK_API_KEY_MESSAGE = (
    "IFLYTEK_API_KEY not found. Add it to .env or set the environment variable."
)
_MISSING_IFLYTEK_API_SECRET_MESSAGE = (
    "IFLYTEK_API_SECRET not found. Add it to .env or set the environment variable."
)
_MISSING_DEEPL_AUTH_KEY_MESSAGE = (
    "DEEPL_AUTH_KEY not found. Add it to .env or set the environment variable."
)


@dataclass(slots=True, frozen=True)
class OpenAICredentials:
    """Store the complete credential set required for OpenAI STT access."""

    api_key: str


@dataclass(slots=True, frozen=True)
class IflytekCredentials:
    """Store the complete credential set required for iFLYTEK STT access."""

    app_id: str
    api_key: str
    api_secret: str


@dataclass(slots=True, frozen=True)
class DeepLCredentials:
    """Store the complete credential set required for DeepL translation access."""

    auth_key: str


class AppSecrets(BaseSettings):
    """Load provider secrets and expose helpers that require complete credentials.

    Normalize blank secret inputs to `None` before provider-specific validation.
    """

    model_config = SettingsConfigDict(
        env_file=DEFAULT_DOTENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("openai_api_key", "OPENAI_API_KEY"),
    )
    iflytek_app_id: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("iflytek_app_id", "IFLYTEK_APP_ID"),
    )
    iflytek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("iflytek_api_key", "IFLYTEK_API_KEY"),
    )
    iflytek_api_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("iflytek_api_secret", "IFLYTEK_API_SECRET"),
    )
    deepl_auth_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("deepl_auth_key", "DEEPL_AUTH_KEY"),
    )

    @field_validator(
        "openai_api_key",
        "iflytek_app_id",
        "iflytek_api_key",
        "iflytek_api_secret",
        "deepl_auth_key",
        mode="before",
    )
    @classmethod
    def _validate_optional_secret(cls, value: Any) -> str | None:
        if isinstance(value, SecretStr):
            candidate = value.get_secret_value().strip()
        elif isinstance(value, str):
            candidate = value.strip()
        elif value is None:
            return None
        else:
            raise ValueError("secret values must be strings")
        return candidate or None

    def require_openai_credentials(self) -> OpenAICredentials:
        """Return complete OpenAI credentials or raise if any secret is missing."""
        return OpenAICredentials(
            api_key=_require_secret(
                self.openai_api_key, _MISSING_OPENAI_API_KEY_MESSAGE
            )
        )

    def require_iflytek_credentials(self) -> IflytekCredentials:
        """Return complete iFLYTEK credentials or raise if any secret is missing."""
        return IflytekCredentials(
            app_id=_require_secret(
                self.iflytek_app_id, _MISSING_IFLYTEK_APP_ID_MESSAGE
            ),
            api_key=_require_secret(
                self.iflytek_api_key, _MISSING_IFLYTEK_API_KEY_MESSAGE
            ),
            api_secret=_require_secret(
                self.iflytek_api_secret, _MISSING_IFLYTEK_API_SECRET_MESSAGE
            ),
        )

    def require_deepl_credentials(self) -> DeepLCredentials:
        """Return complete DeepL credentials or raise if the auth key is missing."""
        return DeepLCredentials(
            auth_key=_require_secret(
                self.deepl_auth_key,
                _MISSING_DEEPL_AUTH_KEY_MESSAGE,
            )
        )


def _require_secret(value: SecretStr | None, message: str) -> str:
    if value is None:
        raise SecretError(message)
    candidate = value.get_secret_value().strip()
    if not candidate:
        raise SecretError(message)
    return candidate
