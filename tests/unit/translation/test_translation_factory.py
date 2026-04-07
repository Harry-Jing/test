import logging
from typing import cast

from tests.unit.translation._support import FakeDeepLSecrets
from vrc_live_caption.config import TranslationConfig
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
