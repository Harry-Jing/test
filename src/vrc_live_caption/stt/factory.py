"""Build configured STT backends and validate provider-specific secrets."""

import logging

from ..config import CaptureConfig, SttConfig
from ..env import AppSecrets
from ..errors import SttSessionError
from .iflytek_rtasr import IflytekRtasrBackend
from .openai_realtime import OpenAIRealtimeBackend
from .types import SttBackend


def create_stt_backend(
    *,
    capture_config: CaptureConfig,
    stt_config: SttConfig,
    secrets: AppSecrets,
    logger: logging.Logger,
) -> SttBackend:
    """Create the configured STT backend with validated credentials."""
    if stt_config.provider == "iflytek_rtasr":
        credentials = secrets.require_iflytek_credentials()
        return IflytekRtasrBackend(
            capture_config=capture_config,
            retry_config=stt_config.retry,
            provider_config=stt_config.providers.iflytek_rtasr,
            app_id=credentials.app_id,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            logger=logger.getChild("iflytek"),
        )

    if stt_config.provider == "openai_realtime":
        credentials = secrets.require_openai_credentials()
        return OpenAIRealtimeBackend(
            capture_config=capture_config,
            retry_config=stt_config.retry,
            provider_config=stt_config.providers.openai_realtime,
            api_key=credentials.api_key,
            logger=logger.getChild("openai"),
        )

    raise SttSessionError(f"Unsupported STT provider: {stt_config.provider}")


def describe_stt_backend(stt_config: SttConfig) -> str:
    """Describe the configured STT backend in CLI-friendly terms."""
    if stt_config.provider == "iflytek_rtasr":
        provider_config = stt_config.providers.iflytek_rtasr
        return (
            f"{stt_config.provider} "
            f"({provider_config.language}, {provider_config.vad_mode})"
        )

    if stt_config.provider == "openai_realtime":
        return f"{stt_config.provider} ({stt_config.providers.openai_realtime.model})"

    return stt_config.provider


def validate_stt_secrets(*, stt_config: SttConfig, secrets: AppSecrets) -> None:
    """Validate that the active STT provider has the required credentials."""
    if stt_config.provider == "iflytek_rtasr":
        secrets.require_iflytek_credentials()
        return
    if stt_config.provider == "openai_realtime":
        secrets.require_openai_credentials()
        return
    raise SttSessionError(f"Unsupported STT provider: {stt_config.provider}")


__all__ = [
    "create_stt_backend",
    "describe_stt_backend",
    "validate_stt_secrets",
]
