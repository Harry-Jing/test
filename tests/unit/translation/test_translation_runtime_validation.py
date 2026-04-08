import logging
from typing import cast

import pytest
from google.auth.exceptions import DefaultCredentialsError

from tests.unit.translation._support import FakeDeepLSecrets
from vrc_live_caption.config import (
    GoogleCloudTranslationProviderConfig,
    TranslationConfig,
    TranslationProvidersConfig,
)
from vrc_live_caption.env import AppSecrets
from vrc_live_caption.errors import SecretError
from vrc_live_caption.translation import validate_translation_runtime


class TestValidateTranslationRuntime:
    def test_when_google_adc_is_missing__then_it_surfaces_secret_error(
        self,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            "vrc_live_caption.translation.backends.google.auth.default",
            lambda **kwargs: (_ for _ in ()).throw(
                DefaultCredentialsError("missing adc")
            ),
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
                secrets=cast(AppSecrets, FakeDeepLSecrets()),
                logger=logging.getLogger("test.translation.google"),
            )
