import asyncio
import logging
from types import SimpleNamespace
from typing import cast

import pytest
from google.auth.exceptions import DefaultCredentialsError

from vrc_live_caption.config import (
    GoogleCloudTranslationProviderConfig,
    TranslationConfig,
    TranslationProvidersConfig,
)
from vrc_live_caption.env import AppSecrets, DeepLCredentials
from vrc_live_caption.errors import SecretError
from vrc_live_caption.translation import (
    AsyncTranslationWorker,
    TranslationRequest,
    TranslationResult,
    create_translation_backend,
    validate_translation_runtime,
)
from vrc_live_caption.translation.backends import DeepLTranslationBackend


class _FakeDeepLSecrets:
    def __init__(self) -> None:
        self.deepl_calls = 0

    def require_deepl_credentials(self) -> DeepLCredentials:
        self.deepl_calls += 1
        return DeepLCredentials(auth_key="deepl-key")


class _SlowBackend:
    name = "slow"

    def __init__(self) -> None:
        self.requests: list[TranslationRequest] = []
        self.release = asyncio.Event()

    def describe(self) -> str:
        return "slow"

    def validate_environment(self) -> None:
        return None

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self.requests.append(request)
        await self.release.wait()
        return TranslationResult(
            utterance_id=request.utterance_id,
            revision=request.revision,
            source_text=request.text,
            translated_text=f"translated:{request.text}",
        )


class _FakeDeepLClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def translate_text(
        self,
        text: str,
        *,
        source_lang: str | None,
        target_lang: str,
    ) -> object:
        self.calls.append(
            {
                "text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
            }
        )
        return SimpleNamespace(text="translated")


def test_create_translation_backend_builds_deepl_backend_with_credentials(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    secrets = _FakeDeepLSecrets()

    class _FakeBackend:
        def __init__(self, *, auth_key: str, logger) -> None:
            captured["auth_key"] = auth_key

        def validate_environment(self) -> None:
            return None

    monkeypatch.setattr(
        "vrc_live_caption.translation.factory.DeepLTranslationBackend",
        _FakeBackend,
    )

    backend = create_translation_backend(
        translation_config=TranslationConfig(enabled=True, target_language="en"),
        secrets=cast(AppSecrets, secrets),
        logger=logging.getLogger("test.translation.factory"),
    )

    assert backend is not None
    assert captured["auth_key"] == "deepl-key"
    assert secrets.deepl_calls == 1


def test_deepl_backend_normalizes_generic_target_language_codes(monkeypatch) -> None:
    fake_client = _FakeDeepLClient()
    monkeypatch.setattr(
        "vrc_live_caption.translation.backends.deepl.DeepLClient",
        lambda auth_key: fake_client,
    )
    backend = DeepLTranslationBackend(
        auth_key="deepl-key",
        logger=logging.getLogger("test.translation.deepl.normalize"),
    )

    result = asyncio.run(
        backend.translate(
            TranslationRequest(
                utterance_id="utt-1",
                revision=1,
                text="hello",
                source_language="zh",
                target_language="en",
            )
        )
    )

    assert result.translated_text == "translated"
    assert fake_client.calls == [
        {
            "text": "hello",
            "source_lang": "ZH",
            "target_lang": "EN-US",
        }
    ]


def test_validate_translation_runtime_surfaces_google_adc_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "vrc_live_caption.translation.backends.google.auth.default",
        lambda **kwargs: (_ for _ in ()).throw(DefaultCredentialsError("missing adc")),
    )

    with pytest.raises(SecretError, match="Google Cloud ADC not found"):
        validate_translation_runtime(
            translation_config=TranslationConfig(
                enabled=True,
                provider="google_cloud",
                target_language="en",
                providers=TranslationProvidersConfig(
                    google_cloud=GoogleCloudTranslationProviderConfig(
                        project_id="test-project",
                        location="global",
                    ),
                ),
            ),
            secrets=cast(AppSecrets, _FakeDeepLSecrets()),
            logger=logging.getLogger("test.translation.google"),
        )


def test_async_translation_worker_drops_oldest_pending_requests() -> None:
    async def scenario() -> None:
        backend = _SlowBackend()
        completed: list[TranslationResult] = []
        failed: list[tuple[str, str]] = []
        worker = AsyncTranslationWorker(
            backend=backend,
            request_timeout_seconds=1.0,
            max_pending_requests=1,
            logger=logging.getLogger("test.translation.worker"),
            on_result=lambda result: completed.append(result) or True,
            on_failure=lambda request, exc: (
                failed.append((request.utterance_id, str(exc))) or True
            ),
        )

        await worker.start()
        worker.submit(
            TranslationRequest(
                utterance_id="utt-1",
                revision=1,
                text="first",
                target_language="en",
            )
        )
        worker.submit(
            TranslationRequest(
                utterance_id="utt-2",
                revision=1,
                text="second",
                target_language="en",
            )
        )
        await asyncio.sleep(0.02)
        backend.release.set()
        await asyncio.sleep(0.05)
        await worker.shutdown(timeout_seconds=1.0)

        metrics = worker.metrics()
        assert failed[0][0] == "utt-1"
        assert backend.requests[0].utterance_id == "utt-2"
        assert completed[0].utterance_id == "utt-2"
        assert metrics.dropped_requests == 1

    asyncio.run(scenario())


def test_async_translation_worker_reports_timeouts_as_failures() -> None:
    async def scenario() -> None:
        backend = _SlowBackend()
        failed: list[str] = []
        worker = AsyncTranslationWorker(
            backend=backend,
            request_timeout_seconds=0.01,
            max_pending_requests=1,
            logger=logging.getLogger("test.translation.timeout"),
            on_result=lambda result: True,
            on_failure=lambda request, exc: failed.append(type(exc).__name__) or True,
        )

        await worker.start()
        worker.submit(
            TranslationRequest(
                utterance_id="utt-timeout",
                revision=1,
                text="timeout",
                target_language="en",
            )
        )
        await asyncio.sleep(0.05)
        await worker.shutdown(timeout_seconds=1.0)

        assert failed == ["TimeoutError"]

    asyncio.run(scenario())
