"""Define the local websocket protocol used between app and FunASR sidecar."""

import json
from collections.abc import Mapping
from typing import Any

CLIENT_START = "start"
CLIENT_STOP = "stop"
SERVER_READY = "ready"
SERVER_TRANSCRIPT = "transcript"
SERVER_ERROR = "error"
LOCAL_STT_MODE = "2pass"
PCM16LE_FORMAT = "pcm16le"


def build_client_start_message(
    *,
    sample_rate: int,
    channels: int,
    sample_format: str = PCM16LE_FORMAT,
    mode: str = LOCAL_STT_MODE,
) -> dict[str, Any]:
    """Build the initial sidecar session-start message."""
    return {
        "type": CLIENT_START,
        "mode": mode,
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_format": sample_format,
    }


def build_client_stop_message() -> dict[str, Any]:
    """Build the sidecar stop message."""
    return {"type": CLIENT_STOP}


def build_ready_message(
    message: str,
    *,
    resolved_device: str | None = None,
    device_policy: str | None = None,
) -> dict[str, Any]:
    """Build the server ready event."""
    payload: dict[str, Any] = {"type": SERVER_READY, "message": message}
    if resolved_device is not None:
        payload["resolved_device"] = resolved_device
    if device_policy is not None:
        payload["device_policy"] = device_policy
    return payload


def build_transcript_message(
    *,
    phase: str,
    segment_id: int,
    text: str,
    is_final: bool,
) -> dict[str, Any]:
    """Build one transcript event emitted by the sidecar."""
    return {
        "type": SERVER_TRANSCRIPT,
        "phase": phase,
        "segment_id": segment_id,
        "text": text,
        "is_final": is_final,
    }


def build_error_message(message: str, *, fatal: bool) -> dict[str, Any]:
    """Build one server error event."""
    return {"type": SERVER_ERROR, "message": message, "fatal": fatal}


def encode_json_message(message: Mapping[str, Any]) -> str:
    """Serialize one websocket JSON message."""
    return json.dumps(dict(message), ensure_ascii=False)


def decode_json_message(raw: str) -> dict[str, Any]:
    """Deserialize one websocket JSON message."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON message must decode to an object")
    return value
