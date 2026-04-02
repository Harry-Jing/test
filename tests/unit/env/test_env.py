from pathlib import Path

import pytest
from pydantic import SecretStr

from vrc_live_caption.env import AppSecrets, SecretError


def test_app_secrets_reads_dotenv_when_process_env_is_missing(
    monkeypatch, tmp_cwd: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_cwd / ".env").write_text('OPENAI_API_KEY="dotenv-key"\n', encoding="utf-8")

    secrets = AppSecrets()

    assert secrets.require_openai_credentials().api_key == "dotenv-key"


def test_app_secrets_prefers_process_env_over_dotenv(
    monkeypatch, tmp_cwd: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    (tmp_cwd / ".env").write_text('OPENAI_API_KEY="dotenv-key"\n', encoding="utf-8")

    secrets = AppSecrets()

    assert secrets.require_openai_credentials().api_key == "env-key"


def test_app_secrets_accepts_field_name_constructor_argument() -> None:
    secrets = AppSecrets(openai_api_key=SecretStr("inline-key"))

    assert secrets.require_openai_credentials().api_key == "inline-key"


def test_app_secrets_accepts_alias_constructor_argument() -> None:
    secrets = AppSecrets.model_validate({"OPENAI_API_KEY": "inline-key"})

    assert secrets.require_openai_credentials().api_key == "inline-key"


def test_app_secrets_prefers_explicit_constructor_value_over_env_and_dotenv(
    monkeypatch, tmp_cwd: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    (tmp_cwd / ".env").write_text('OPENAI_API_KEY="dotenv-key"\n', encoding="utf-8")

    secrets = AppSecrets(openai_api_key=SecretStr("inline-key"))

    assert secrets.require_openai_credentials().api_key == "inline-key"


def test_app_secrets_requires_openai_api_key_when_openai_backend_needs_it(
    monkeypatch, tmp_cwd: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(SecretError, match="OPENAI_API_KEY not found"):
        AppSecrets().require_openai_credentials()


def test_app_secrets_requires_all_iflytek_fields(monkeypatch, tmp_cwd: Path) -> None:
    monkeypatch.delenv("IFLYTEK_APP_ID", raising=False)
    monkeypatch.delenv("IFLYTEK_API_KEY", raising=False)
    monkeypatch.delenv("IFLYTEK_API_SECRET", raising=False)

    with pytest.raises(SecretError, match="IFLYTEK_APP_ID not found"):
        AppSecrets().require_iflytek_credentials()


def test_app_secrets_reads_iflytek_credentials_from_dotenv(
    monkeypatch, tmp_cwd: Path
) -> None:
    monkeypatch.delenv("IFLYTEK_APP_ID", raising=False)
    monkeypatch.delenv("IFLYTEK_API_KEY", raising=False)
    monkeypatch.delenv("IFLYTEK_API_SECRET", raising=False)
    (tmp_cwd / ".env").write_text(
        "\n".join(
            [
                'IFLYTEK_APP_ID="app-id"',
                'IFLYTEK_API_KEY="api-key"',
                'IFLYTEK_API_SECRET="api-secret"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    credentials = AppSecrets().require_iflytek_credentials()

    assert credentials.app_id == "app-id"
    assert credentials.api_key == "api-key"
    assert credentials.api_secret == "api-secret"


def test_app_secrets_ignores_unknown_dotenv_keys(monkeypatch, tmp_cwd: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_cwd / ".env").write_text(
        'OPENAI_API_KEY="dotenv-key"\nUNUSED_KEY="ignored"\n',
        encoding="utf-8",
    )

    secrets = AppSecrets()

    assert secrets.require_openai_credentials().api_key == "dotenv-key"
