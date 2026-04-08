"""Build configured translation backends and validate their prerequisites."""

import logging

from ..config import TranslationConfig
from ..env import AppSecrets
from ..errors import TranslationError
from .backends import DeepLTranslationBackend, GoogleCloudTranslationBackend
from .translategemma_local import TranslateGemmaLocalTranslationBackend
from .types import TranslationBackend


def create_translation_backend(
    *,
    translation_config: TranslationConfig,
    secrets: AppSecrets,
    logger: logging.Logger,
) -> TranslationBackend | None:
    """Create the configured translation backend when translation is enabled."""
    if not translation_config.enabled:
        return None

    if translation_config.provider == "deepl":
        credentials = secrets.require_deepl_credentials()
        backend: TranslationBackend = DeepLTranslationBackend(
            auth_key=credentials.auth_key,
            logger=logger.getChild("deepl"),
        )
        backend.validate_environment()
        return backend

    if translation_config.provider == "google_cloud":
        backend = GoogleCloudTranslationBackend(
            provider_config=translation_config.providers.google_cloud,
            logger=logger.getChild("google_cloud"),
        )
        backend.validate_environment()
        return backend

    if translation_config.provider == "translategemma_local":
        backend = TranslateGemmaLocalTranslationBackend(
            provider_config=translation_config.providers.translategemma_local,
            timeout_seconds=translation_config.request_timeout_seconds,
            logger=logger.getChild("translategemma_local"),
        )
        backend.validate_environment()
        return backend

    raise TranslationError(
        f"Unsupported translation provider: {translation_config.provider}"
    )


def describe_translation_backend(translation_config: TranslationConfig) -> str:
    """Describe the configured translation backend in CLI-friendly terms."""
    if translation_config.provider == "google_cloud":
        provider_config = translation_config.providers.google_cloud
        return (
            f"google_cloud ({provider_config.project_id}, {provider_config.location})"
        )
    if translation_config.provider == "translategemma_local":
        provider_config = translation_config.providers.translategemma_local
        return f"translategemma_local ({provider_config.host}:{provider_config.port})"
    return translation_config.provider


def validate_translation_runtime(
    *,
    translation_config: TranslationConfig,
    secrets: AppSecrets,
    logger: logging.Logger,
) -> None:
    """Validate that the active translation provider can run."""
    backend = create_translation_backend(
        translation_config=translation_config,
        secrets=secrets,
        logger=logger,
    )
    if backend is None:
        return
    backend.validate_environment()


__all__ = [
    "create_translation_backend",
    "describe_translation_backend",
    "validate_translation_runtime",
]
