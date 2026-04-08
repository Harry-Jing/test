import asyncio
import logging

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidMessage, InvalidStatus
from websockets.http11 import Response

from vrc_live_caption.config import (
    CaptureConfig,
    IflytekRtasrProviderConfig,
    SttRetryConfig,
)
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt.iflytek_rtasr import (
    _IFLYTEK_RTASR_URL,
    IflytekAudioChunker,
    IflytekConnectionState,
    IflytekRtasrAttempt,
    IflytekRtasrBackend,
    IflytekUtteranceState,
    _build_iflytek_signature_base_string,
    build_iflytek_auth_params,
    build_iflytek_auth_url,
    is_fatal_iflytek_error_event,
    is_retriable_iflytek_error,
    normalize_iflytek_rtasr_event,
)
from vrc_live_caption.stt.types import AttemptContext, TranscriptRevisionEvent


def _make_attempt_context(logger: logging.Logger) -> AttemptContext:
    return AttemptContext(
        audio_queue=DropOldestAsyncQueue[AudioChunk](
            max_items=4,
            logger=logging.getLogger("test.iflytek.audio"),
            label="audio queue",
        ),
        publish_event=lambda _event: None,
        mark_ready=lambda _message: None,
        stop_requested=asyncio.Event(),
        connect_timeout_seconds=1.0,
        logger=logger,
    )


def test_build_iflytek_auth_params_sorts_and_signs_query_fields() -> None:
    params = build_iflytek_auth_params(
        provider_config=IflytekRtasrProviderConfig(
            language="autodialect",
            vad_mode="near_field",
            domain="tech",
        ),
        app_id="app-id",
        api_key="api-key",
        api_secret="api-secret",
        utc="2025-03-24T00:01:19+0800",
        session_uuid="edf53e32-6533-4d6a-acd3-fe4df14ee332",
    )

    assert _build_iflytek_signature_base_string(params) == (
        "accessKeyId=api-key&appId=app-id&audio_encode=pcm_s16le&eng_vad_mdn=2&"
        "lang=autodialect&pd=tech&samplerate=16000&"
        "utc=2025-03-24T00%3A01%3A19%2B0800&uuid=edf53e32-6533-4d6a-acd3-fe4df14ee332"
    )
    assert params["signature"] == "vvZQpjqH+Z+pdtv4Ey5r/hkQ1RM="


def test_build_iflytek_auth_url_includes_signed_query_string() -> None:
    url = build_iflytek_auth_url(
        provider_config=IflytekRtasrProviderConfig(vad_mode="far_field"),
        app_id="app-id",
        api_key="api-key",
        api_secret="api-secret",
        utc="2025-03-24T00:01:19+0800",
        session_uuid="edf53e32-6533-4d6a-acd3-fe4df14ee332",
    )

    assert url.startswith(f"{_IFLYTEK_RTASR_URL}?")
    assert "accessKeyId=api-key" in url
    assert "eng_vad_mdn=1" in url
    assert "signature=" in url


def test_iflytek_audio_chunker_flushes_tail_frame() -> None:
    chunker = IflytekAudioChunker(frame_bytes=4)

    frames = chunker.append(b"abcd123")
    tail = chunker.flush()

    assert frames == [b"abcd"]
    assert tail == [b"123"]


def test_normalize_iflytek_rtasr_event_emits_partial_and_final_revisions() -> None:
    utterances: dict[str, IflytekUtteranceState] = {}
    partial_event = {
        "msg_type": "result",
        "res_type": "asr",
        "data": {
            "seg_id": 7,
            "ls": False,
            "cn": {"st": {"type": "1", "rt": [{"ws": [{"cw": [{"w": "hello"}]}]}]}},
        },
    }
    final_event = {
        "msg_type": "result",
        "res_type": "asr",
        "data": {
            "seg_id": 7,
            "ls": True,
            "cn": {
                "st": {"type": "0", "rt": [{"ws": [{"cw": [{"w": "hello world"}]}]}]}
            },
        },
    }

    first = normalize_iflytek_rtasr_event(partial_event, utterances)
    second = normalize_iflytek_rtasr_event(final_event, utterances)

    assert len(first) == 1
    assert len(second) == 1
    assert isinstance(first[0], TranscriptRevisionEvent)
    assert isinstance(second[0], TranscriptRevisionEvent)
    assert first[0].text == "hello"
    assert first[0].is_final is False
    assert second[0].text == "hello world"
    assert second[0].is_final is True
    assert utterances == {}


def test_is_fatal_iflytek_error_event_flags_authentication_codes() -> None:
    assert is_fatal_iflytek_error_event({"action": "error", "code": "35001"}) is True


def test_is_retriable_iflytek_error_only_retries_transport_failures() -> None:
    invalid_status = InvalidStatus(
        Response(
            status_code=401, reason_phrase="Unauthorized", headers=Headers(), body=b""
        )
    )

    assert is_retriable_iflytek_error(OSError("network down")) is True
    assert is_retriable_iflytek_error(invalid_status) is False
    assert is_retriable_iflytek_error(RuntimeError("boom")) is False


def test_is_retriable_iflytek_error_accepts_retryable_handshake_codes() -> None:
    try:
        try:
            raise ValueError("invalid status code; expected 100–599; got 35006")
        except ValueError as exc:
            raise InvalidMessage("did not receive a valid HTTP response") from exc
    except InvalidMessage as exc:
        assert is_retriable_iflytek_error(exc) is True


def test_iflytek_backend_rejects_invalid_capture_shape() -> None:
    with pytest.raises(SttSessionError, match="capture.sample_rate = 16000"):
        IflytekRtasrBackend(
            capture_config=CaptureConfig(sample_rate=48_000),
            retry_config=SttRetryConfig(),
            provider_config=IflytekRtasrProviderConfig(),
            app_id="app",
            api_key="key",
            api_secret="secret",
            logger=logging.getLogger("test.iflytek.backend"),
        )


def test_iflytek_backend_creates_fresh_attempt_state() -> None:
    backend = IflytekRtasrBackend(
        capture_config=CaptureConfig(),
        retry_config=SttRetryConfig(),
        provider_config=IflytekRtasrProviderConfig(),
        app_id="app",
        api_key="key",
        api_secret="secret",
        logger=logging.getLogger("test.iflytek.backend.state"),
    )

    context = _make_attempt_context(backend.logger)
    first = backend.create_attempt(context=context)
    second = backend.create_attempt(context=context)

    assert isinstance(first, IflytekRtasrAttempt)
    assert isinstance(second, IflytekRtasrAttempt)
    assert isinstance(first._state, IflytekConnectionState)
    assert isinstance(second._state, IflytekConnectionState)
    assert first._state is not second._state
