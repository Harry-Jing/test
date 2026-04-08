"""Define the websocket protocol for the local TranslateGemma sidecar."""

from __future__ import annotations

import json
from typing import Any

from ...errors import TranslationError


def build_ready_message(
    *,
    model: str,
    device_policy: str,
    resolved_device: str,
    resolved_dtype: str,
    message: str = "TranslateGemma local sidecar ready",
) -> dict[str, object]:
    """Build one sidecar-ready event payload."""
    return {
        "type": "ready",
        "message": message,
        "model": model,
        "device_policy": device_policy,
        "resolved_device": resolved_device,
        "resolved_dtype": resolved_dtype,
    }


def build_translate_request(
    *,
    text: str,
    source_language: str,
    target_language: str,
) -> dict[str, object]:
    """Build one translation request payload."""
    return {
        "type": "translate",
        "text": text,
        "source_language": source_language,
        "target_language": target_language,
    }


def build_result_message(translated_text: str) -> dict[str, object]:
    """Build one translated-result event payload."""
    return {
        "type": "result",
        "translated_text": translated_text,
    }


def build_error_message(message: str, *, fatal: bool = True) -> dict[str, object]:
    """Build one error event payload."""
    return {
        "type": "error",
        "message": message,
        "fatal": fatal,
    }


def encode_json_message(payload: dict[str, object]) -> str:
    """Serialize one protocol message as JSON."""
    return json.dumps(payload, ensure_ascii=False)


def decode_json_message(raw_message: str) -> dict[str, Any]:
    """Deserialize one protocol message and enforce an object payload."""
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise TranslationError(f"invalid JSON message: {exc}") from exc
    if not isinstance(payload, dict):
        raise TranslationError("protocol message must be a JSON object")
    return payload


__all__ = [
    "build_error_message",
    "build_ready_message",
    "build_result_message",
    "build_translate_request",
    "decode_json_message",
    "encode_json_message",
]
