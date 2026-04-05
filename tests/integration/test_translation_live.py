import asyncio
import logging
import os

import pytest

from vrc_live_caption.config import (
    GoogleCloudTranslationProviderConfig,
    TranslationConfig,
    TranslationProvidersConfig,
)
from vrc_live_caption.env import AppSecrets
from vrc_live_caption.errors import SecretError
from vrc_live_caption.translation import create_translation_backend
from vrc_live_caption.translation.types import TranslationRequest


@pytest.mark.integration
@pytest.mark.deepl_live
def test_deepl_translation_backend_live() -> None:
    if not os.getenv("DEEPL_AUTH_KEY"):
        pytest.skip("DEEPL_AUTH_KEY not set")

    backend = create_translation_backend(
        translation_config=TranslationConfig(
            enabled=True,
            provider="deepl",
            target_language="ZH",
        ),
        secrets=AppSecrets(),
        logger=logging.getLogger("test.translation.deepl"),
    )
    assert backend is not None

    result = asyncio.run(
        backend.translate(
            TranslationRequest(
                utterance_id="live-deepl",
                revision=1,
                text="Hello, world!",
                target_language="ZH",
            )
        )
    )

    assert result.translated_text


@pytest.mark.integration
@pytest.mark.google_translate_live
def test_google_cloud_translation_backend_live() -> None:
    project_id = os.getenv("GOOGLE_TRANSLATE_PROJECT_ID")
    if not project_id:
        pytest.skip("GOOGLE_TRANSLATE_PROJECT_ID not set")
    try:
        backend = create_translation_backend(
            translation_config=TranslationConfig(
                enabled=True,
                provider="google_cloud",
                target_language="zh-CN",
                providers=TranslationProvidersConfig(
                    google_cloud=GoogleCloudTranslationProviderConfig(
                        project_id=project_id,
                        location=os.getenv("GOOGLE_TRANSLATE_LOCATION", "global"),
                    )
                ),
            ),
            secrets=AppSecrets(),
            logger=logging.getLogger("test.translation.google"),
        )
    except SecretError as exc:
        pytest.skip(str(exc))
    assert backend is not None

    result = asyncio.run(
        backend.translate(
            TranslationRequest(
                utterance_id="live-google",
                revision=1,
                text="Hello, world!",
                target_language="zh-CN",
            )
        )
    )

    assert result.translated_text
