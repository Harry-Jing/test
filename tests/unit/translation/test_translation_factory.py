import logging
from typing import cast

from tests.unit.translation._support import FakeDeepLSecrets
from vrc_live_caption.config import (
    AppConfig,
    TranslateGemmaLocalTranslationProviderConfig,
    TranslationConfig,
    TranslationProvidersConfig,
)
from vrc_live_caption.env import AppSecrets
from vrc_live_caption.translation import create_translation_backend


class TestCreateTranslationBackend:
    def test_when_deepl_backend_is_selected__then_it_builds_it_with_credentials(
        self,
        monkeypatch,
    ) -> None:
        captured: dict[str, object] = {}
        secrets = FakeDeepLSecrets()

        class FakeBackend:
            def __init__(self, *, auth_key: str, logger) -> None:
                captured["auth_key"] = auth_key

            def validate_environment(self) -> None:
                return None

        monkeypatch.setattr(
            "vrc_live_caption.translation.factory.DeepLTranslationBackend",
            FakeBackend,
        )

        backend = create_translation_backend(
            translation_config=TranslationConfig(enabled=True, target_language="en"),
            secrets=cast(AppSecrets, secrets),
            logger=logging.getLogger("test.translation.factory"),
        )

        assert backend is not None
        assert captured["auth_key"] == "deepl-key"
        assert secrets.deepl_calls == 1

    def test_when_local_translategemma_backend_is_selected__then_it_builds_it(
        self,
        monkeypatch,
    ) -> None:
        captured: dict[str, object] = {}

        class FakeBackend:
            def __init__(
                self, *, provider_config, timeout_seconds: float, logger
            ) -> None:
                captured["provider_config"] = provider_config
                captured["timeout_seconds"] = timeout_seconds

            def validate_environment(self) -> None:
                captured["validated"] = True

        monkeypatch.setattr(
            "vrc_live_caption.translation.factory.TranslateGemmaLocalTranslationBackend",
            FakeBackend,
        )

        backend = create_translation_backend(
            translation_config=TranslationConfig(
                enabled=True,
                provider="translategemma_local",
                source_language="zh",
                target_language="en",
                request_timeout_seconds=4.5,
                providers=TranslationProvidersConfig(
                    google_cloud=AppConfig().translation.providers.google_cloud,
                    translategemma_local=TranslateGemmaLocalTranslationProviderConfig(
                        host="127.0.0.1",
                        port=11096,
                    ),
                ),
            ),
            secrets=cast(AppSecrets, FakeDeepLSecrets()),
            logger=logging.getLogger("test.translation.factory.local"),
        )

        assert backend is not None
        assert captured["validated"] is True
        provider_config = cast(
            TranslateGemmaLocalTranslationProviderConfig,
            captured["provider_config"],
        )
        assert provider_config.port == 11096
        assert captured["timeout_seconds"] == 4.5
