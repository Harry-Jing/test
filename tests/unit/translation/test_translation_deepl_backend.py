import logging

import pytest

from tests.unit.translation._support import FakeDeepLClient
from vrc_live_caption.translation import TranslationRequest
from vrc_live_caption.translation.backends import DeepLTranslationBackend


@pytest.mark.asyncio
class TestDeepLTranslationBackend:
    async def test_when_generic_language_codes_are_used__then_it_normalizes_them(
        self,
        monkeypatch,
    ) -> None:
        fake_client = FakeDeepLClient()
        monkeypatch.setattr(
            "vrc_live_caption.translation.backends.deepl.DeepLClient",
            lambda auth_key: fake_client,
        )
        backend = DeepLTranslationBackend(
            auth_key="deepl-key",
            logger=logging.getLogger("test.translation.deepl.normalize"),
        )

        result = await backend.translate(
            TranslationRequest(
                utterance_id="utt-1",
                revision=1,
                text="hello",
                source_language="zh",
                target_language="en",
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
