import logging
from types import SimpleNamespace
from typing import cast

import pytest

from vrc_live_caption.config import CaptureConfig, FunasrLocalProviderConfig, SttConfig
from vrc_live_caption.env import AppSecrets, IflytekCredentials, OpenAICredentials
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.stt.factory import (
    create_stt_backend,
    describe_stt_backend,
    validate_stt_secrets,
)


class _FakeSecrets:
    def __init__(self) -> None:
        self.iflytek_calls = 0
        self.openai_calls = 0

    def require_iflytek_credentials(self) -> IflytekCredentials:
        self.iflytek_calls += 1
        return IflytekCredentials(
            app_id="app-id",
            api_key="api-key",
            api_secret="api-secret",
        )

    def require_openai_credentials(self) -> OpenAICredentials:
        self.openai_calls += 1
        return OpenAICredentials(api_key="openai-key")


def test_create_stt_backend_builds_funasr_local_without_reading_secrets(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    secrets = _FakeSecrets()

    def fake_backend(**kwargs):
        captured.update(kwargs)
        return "funasr-backend"

    monkeypatch.setattr("vrc_live_caption.stt.factory.FunasrLocalBackend", fake_backend)

    result = create_stt_backend(
        capture_config=CaptureConfig(),
        stt_config=SttConfig(provider="funasr_local"),
        secrets=cast(AppSecrets, secrets),
        logger=logging.getLogger("test.stt.factory.funasr"),
    )

    assert result == "funasr-backend"
    assert cast(FunasrLocalProviderConfig, captured["provider_config"]).host == (
        "127.0.0.1"
    )
    assert secrets.iflytek_calls == 0
    assert secrets.openai_calls == 0


def test_create_stt_backend_builds_iflytek_backend_with_credentials(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    secrets = _FakeSecrets()

    def fake_backend(**kwargs):
        captured.update(kwargs)
        return "iflytek-backend"

    monkeypatch.setattr(
        "vrc_live_caption.stt.factory.IflytekRtasrBackend", fake_backend
    )

    result = create_stt_backend(
        capture_config=CaptureConfig(),
        stt_config=SttConfig(provider="iflytek_rtasr"),
        secrets=cast(AppSecrets, secrets),
        logger=logging.getLogger("test.stt.factory.iflytek"),
    )

    assert result == "iflytek-backend"
    assert captured["app_id"] == "app-id"
    assert captured["api_key"] == "api-key"
    assert captured["api_secret"] == "api-secret"
    assert secrets.iflytek_calls == 1
    assert secrets.openai_calls == 0


def test_create_stt_backend_builds_openai_backend_with_credentials(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    secrets = _FakeSecrets()

    def fake_backend(**kwargs):
        captured.update(kwargs)
        return "openai-backend"

    monkeypatch.setattr(
        "vrc_live_caption.stt.factory.OpenAIRealtimeBackend",
        fake_backend,
    )

    result = create_stt_backend(
        capture_config=CaptureConfig(),
        stt_config=SttConfig(provider="openai_realtime"),
        secrets=cast(AppSecrets, secrets),
        logger=logging.getLogger("test.stt.factory.openai"),
    )

    assert result == "openai-backend"
    assert captured["api_key"] == "openai-key"
    assert secrets.iflytek_calls == 0
    assert secrets.openai_calls == 1


def test_create_stt_backend_rejects_unknown_provider() -> None:
    fake_config = SimpleNamespace(provider="unknown")

    with pytest.raises(SttSessionError, match="Unsupported STT provider: unknown"):
        create_stt_backend(
            capture_config=CaptureConfig(),
            stt_config=cast(SttConfig, fake_config),
            secrets=cast(AppSecrets, _FakeSecrets()),
            logger=logging.getLogger("test.stt.factory.unknown"),
        )


def test_describe_stt_backend_formats_supported_providers() -> None:
    assert (
        describe_stt_backend(SttConfig(provider="funasr_local"))
        == "funasr_local (127.0.0.1:10095)"
    )
    assert (
        describe_stt_backend(SttConfig(provider="iflytek_rtasr"))
        == "iflytek_rtasr (autodialect, near_field)"
    )
    assert (
        describe_stt_backend(SttConfig(provider="openai_realtime"))
        == "openai_realtime (gpt-4o-transcribe)"
    )


def test_describe_stt_backend_falls_back_to_unknown_provider_name() -> None:
    fake_config = SimpleNamespace(provider="custom")

    assert describe_stt_backend(cast(SttConfig, fake_config)) == "custom"


def test_validate_stt_secrets_routes_to_active_provider() -> None:
    secrets = _FakeSecrets()

    validate_stt_secrets(
        stt_config=SttConfig(provider="funasr_local"),
        secrets=cast(AppSecrets, secrets),
    )
    validate_stt_secrets(
        stt_config=SttConfig(provider="iflytek_rtasr"),
        secrets=cast(AppSecrets, secrets),
    )
    validate_stt_secrets(
        stt_config=SttConfig(provider="openai_realtime"),
        secrets=cast(AppSecrets, secrets),
    )

    assert secrets.iflytek_calls == 1
    assert secrets.openai_calls == 1


def test_validate_stt_secrets_rejects_unknown_provider() -> None:
    fake_config = SimpleNamespace(provider="custom")

    with pytest.raises(SttSessionError, match="Unsupported STT provider: custom"):
        validate_stt_secrets(
            stt_config=cast(SttConfig, fake_config),
            secrets=cast(AppSecrets, _FakeSecrets()),
        )
