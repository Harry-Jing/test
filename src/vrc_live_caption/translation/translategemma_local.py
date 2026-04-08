"""Implement the websocket-backed local TranslateGemma translation backend."""

import asyncio
import logging
import ssl
from dataclasses import dataclass
from typing import Any

from websockets.sync.client import connect

from ..config import TranslateGemmaLocalTranslationProviderConfig
from ..errors import TranslationError
from ..local_translation.translategemma.protocol import (
    build_translate_request,
    decode_json_message,
    encode_json_message,
)
from .types import TranslationRequest, TranslationResult


@dataclass(frozen=True, slots=True)
class TranslateGemmaLocalReadyEvent:
    """Store the ready metadata returned by the local translation sidecar."""

    message: str
    model: str | None = None
    resolved_device: str | None = None
    device_policy: str | None = None
    resolved_dtype: str | None = None


class TranslateGemmaLocalTranslationBackend:
    """Translate transcript text through the local TranslateGemma sidecar."""

    name = "translategemma_local"

    def __init__(
        self,
        *,
        provider_config: TranslateGemmaLocalTranslationProviderConfig,
        timeout_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self._provider_config = provider_config
        self._timeout_seconds = timeout_seconds
        self._logger = logger

    def describe(self) -> str:
        """Return a CLI-friendly backend summary."""
        return (
            f"{self.name} ({self._provider_config.host}:{self._provider_config.port})"
        )

    def validate_environment(self) -> None:
        """Validate that the local translation sidecar accepts connections."""
        probe_translategemma_local_service(
            provider_config=self._provider_config,
            timeout_seconds=self._timeout_seconds,
        )

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Translate one transcript revision through the local sidecar."""
        try:
            translated_text = await asyncio.to_thread(self._translate_sync, request)
        except TranslationError:
            raise
        except Exception as exc:
            raise TranslationError(
                f"TranslateGemma local translation failed: {exc}"
            ) from exc

        translated_text = translated_text.strip()
        if not translated_text:
            raise TranslationError(
                "TranslateGemma local translation returned an empty result"
            )
        return TranslationResult(
            utterance_id=request.utterance_id,
            revision=request.revision,
            source_text=request.text,
            translated_text=translated_text,
        )

    def _translate_sync(self, request: TranslationRequest) -> str:
        if request.source_language is None:
            raise TranslationError(
                "TranslateGemma local translation requires request.source_language"
            )

        with connect(
            build_translategemma_local_url(self._provider_config),
            ssl=_build_ssl_context(self._provider_config),
            open_timeout=self._timeout_seconds,
            close_timeout=self._timeout_seconds,
            ping_interval=None,
        ) as websocket:
            _await_ready_event(websocket.recv(timeout=self._timeout_seconds))
            websocket.send(
                encode_json_message(
                    build_translate_request(
                        text=request.text,
                        source_language=request.source_language,
                        target_language=request.target_language,
                    )
                )
            )

            while True:
                raw_message = websocket.recv(timeout=self._timeout_seconds)
                if not isinstance(raw_message, str):
                    continue
                event = decode_json_message(raw_message)
                event_type = _get_value(event, "type")
                if event_type == "result":
                    translated_text = _coerce_optional_text(
                        _get_value(event, "translated_text")
                    )
                    if translated_text is None:
                        raise TranslationError(
                            "TranslateGemma local sidecar returned an empty result payload"
                        )
                    return translated_text
                if event_type == "error":
                    message = _coerce_text(
                        _get_value(
                            event,
                            "message",
                            "TranslateGemma local sidecar error",
                        )
                    )
                    raise TranslationError(message)


def probe_translategemma_local_service(
    *,
    provider_config: TranslateGemmaLocalTranslationProviderConfig,
    timeout_seconds: float = 3.0,
) -> TranslateGemmaLocalReadyEvent:
    """Verify that the local translation sidecar is reachable and ready."""
    try:
        with connect(
            build_translategemma_local_url(provider_config),
            ssl=_build_ssl_context(provider_config),
            open_timeout=timeout_seconds,
            close_timeout=timeout_seconds,
            ping_interval=None,
        ) as websocket:
            return _await_ready_event(websocket.recv(timeout=timeout_seconds))
    except TranslationError:
        raise
    except Exception as exc:
        raise TranslationError(
            f"TranslateGemma local sidecar probe failed: {exc}"
        ) from exc


def build_translategemma_local_url(
    provider_config: TranslateGemmaLocalTranslationProviderConfig,
) -> str:
    """Return the websocket URL for the local translation sidecar."""
    scheme = "wss" if provider_config.use_ssl else "ws"
    return f"{scheme}://{provider_config.host}:{provider_config.port}"


def _build_ssl_context(
    provider_config: TranslateGemmaLocalTranslationProviderConfig,
) -> ssl.SSLContext | None:
    if not provider_config.use_ssl:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def parse_translategemma_local_ready_event(
    event: Any,
) -> TranslateGemmaLocalReadyEvent | None:
    """Parse one sidecar ready event and keep optional metadata."""
    if _get_value(event, "type") != "ready":
        return None
    return TranslateGemmaLocalReadyEvent(
        message=_coerce_text(
            _get_value(event, "message", "TranslateGemma local sidecar ready")
        ),
        model=_coerce_optional_text(_get_value(event, "model")),
        resolved_device=_coerce_optional_text(_get_value(event, "resolved_device")),
        device_policy=_coerce_optional_text(_get_value(event, "device_policy")),
        resolved_dtype=_coerce_optional_text(_get_value(event, "resolved_dtype")),
    )


def _await_ready_event(raw_message: Any) -> TranslateGemmaLocalReadyEvent:
    if not isinstance(raw_message, str):
        raise TranslationError("TranslateGemma local sidecar ready event must be text")

    event = decode_json_message(raw_message)
    ready_event = parse_translategemma_local_ready_event(event)
    if ready_event is not None:
        return ready_event
    if _get_value(event, "type") == "error":
        message = _coerce_text(
            _get_value(event, "message", "TranslateGemma local sidecar error")
        )
        raise TranslationError(message)
    raise TranslationError("TranslateGemma local sidecar did not send a ready event")


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _coerce_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _coerce_text(value).strip()
    return text or None


__all__ = [
    "TranslateGemmaLocalReadyEvent",
    "TranslateGemmaLocalTranslationBackend",
    "build_translategemma_local_url",
    "parse_translategemma_local_ready_event",
    "probe_translategemma_local_service",
]
