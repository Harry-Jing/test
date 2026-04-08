"""Implement DeepL and Google Cloud text translation backends."""

import asyncio
import html
import logging

import deepl
import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import translate_v3

from ..config import GoogleCloudTranslationProviderConfig
from ..errors import SecretError, TranslationError
from .types import TranslationRequest, TranslationResult

_GOOGLE_AUTH_SCOPE = ("https://www.googleapis.com/auth/cloud-platform",)
_DEEPL_GENERIC_TARGET_LANGUAGE_ALIASES = {
    "EN": "EN-US",
    "PT": "PT-BR",
}


def _normalize_deepl_source_language(language: str | None) -> str | None:
    if language is None:
        return None
    normalized = language.strip().upper()
    return normalized or None


def _normalize_deepl_target_language(language: str) -> str:
    normalized = language.strip().upper()
    return _DEEPL_GENERIC_TARGET_LANGUAGE_ALIASES.get(normalized, normalized)


class DeepLTranslationBackend:
    """Translate transcript text through the DeepL Python SDK."""

    name = "deepl"

    def __init__(self, *, auth_key: str, logger: logging.Logger) -> None:
        self._client = deepl.DeepLClient(auth_key)
        self._logger = logger

    def describe(self) -> str:
        """Return a CLI-friendly backend summary."""
        return "deepl"

    def validate_environment(self) -> None:
        """Validate that the DeepL client is configured."""
        return None

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Translate one transcript revision through DeepL."""
        try:
            result = await asyncio.to_thread(
                self._client.translate_text,
                request.text,
                source_lang=_normalize_deepl_source_language(request.source_language),
                target_lang=_normalize_deepl_target_language(request.target_language),
            )
        except Exception as exc:
            raise TranslationError(f"DeepL translation failed: {exc}") from exc

        translated_text = getattr(result, "text", "").strip()
        if not translated_text:
            raise TranslationError("DeepL translation returned an empty result")
        return TranslationResult(
            utterance_id=request.utterance_id,
            revision=request.revision,
            source_text=request.text,
            translated_text=translated_text,
        )


class GoogleCloudTranslationBackend:
    """Translate transcript text through Google Cloud Translation v3."""

    name = "google_cloud"

    def __init__(
        self,
        *,
        provider_config: GoogleCloudTranslationProviderConfig,
        logger: logging.Logger,
    ) -> None:
        project_id = provider_config.project_id
        assert project_id is not None
        self._project_id = project_id
        self._location = provider_config.location
        self._logger = logger
        self._client: translate_v3.TranslationServiceClient | None = None

    def describe(self) -> str:
        """Return a CLI-friendly backend summary."""
        return f"google_cloud ({self._project_id}, {self._location})"

    def validate_environment(self) -> None:
        """Validate that ADC are available for the Google client."""
        try:
            google.auth.default(scopes=_GOOGLE_AUTH_SCOPE)
        except DefaultCredentialsError as exc:
            raise SecretError(
                "Google Cloud ADC not found. Configure GOOGLE_APPLICATION_CREDENTIALS or run `gcloud auth application-default login`."
            ) from exc
        self._get_client()

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Translate one transcript revision through Google Cloud."""
        try:
            response = await asyncio.to_thread(self._translate_sync, request)
        except SecretError:
            raise
        except Exception as exc:
            raise TranslationError(f"Google Cloud translation failed: {exc}") from exc

        translated_text = response.strip()
        if not translated_text:
            raise TranslationError("Google Cloud translation returned an empty result")
        return TranslationResult(
            utterance_id=request.utterance_id,
            revision=request.revision,
            source_text=request.text,
            translated_text=translated_text,
        )

    def _get_client(self) -> translate_v3.TranslationServiceClient:
        if self._client is None:
            self._client = translate_v3.TranslationServiceClient()
        return self._client

    def _translate_sync(self, request: TranslationRequest) -> str:
        parent = f"projects/{self._project_id}/locations/{self._location}"
        response = self._get_client().translate_text(
            request=translate_v3.TranslateTextRequest(
                parent=parent,
                contents=[request.text],
                mime_type="text/plain",
                target_language_code=request.target_language,
                source_language_code=request.source_language,
            )
        )
        if not response.translations:
            return ""
        return html.unescape(response.translations[0].translated_text)


__all__ = [
    "DeepLTranslationBackend",
    "GoogleCloudTranslationBackend",
]
