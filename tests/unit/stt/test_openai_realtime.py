import asyncio
import logging

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from vrc_live_caption.config import (
    CaptureConfig,
    OpenAIRealtimeProviderConfig,
    SttRetryConfig,
)
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt.openai_realtime import (
    OpenAIConnectionState,
    OpenAIRealtimeAttempt,
    OpenAIRealtimeBackend,
    OpenAIUtteranceState,
    _build_transcription_session_update_event,
    is_fatal_openai_realtime_error,
    is_retriable_openai_realtime_error,
    normalize_openai_realtime_event,
)
from vrc_live_caption.stt.types import (
    AttemptContext,
    SttStatusEvent,
    TranscriptRevisionEvent,
)


def _make_attempt_context(logger: logging.Logger) -> AttemptContext:
    return AttemptContext(
        audio_queue=DropOldestAsyncQueue[AudioChunk](
            max_items=4,
            logger=logging.getLogger("test.openai.audio"),
            label="audio queue",
        ),
        publish_event=lambda _event: None,
        mark_ready=lambda _message: None,
        stop_requested=asyncio.Event(),
        connect_timeout_seconds=1.0,
        logger=logger,
    )


def test_normalize_openai_realtime_event_accumulates_delta_and_completed() -> None:
    utterances: dict[str, OpenAIUtteranceState] = {}

    delta_event = {
        "type": "conversation.item.input_audio_transcription.delta",
        "item_id": "utt-1",
        "delta": "ni hao",
    }
    completed_event = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "utt-1",
        "transcript": "ni hao ma",
    }

    first = normalize_openai_realtime_event(delta_event, utterances)
    second = normalize_openai_realtime_event(completed_event, utterances)

    assert len(first) == 1
    assert len(second) == 1
    assert isinstance(first[0], TranscriptRevisionEvent)
    assert isinstance(second[0], TranscriptRevisionEvent)
    assert first[0].text == "ni hao"
    assert first[0].is_final is False
    assert second[0].text == "ni hao ma"
    assert second[0].is_final is True
    assert utterances == {}


def test_normalize_openai_realtime_event_emits_status_for_failed_utterance() -> None:
    utterances = {"utt-2": OpenAIUtteranceState(text="partial", revision=3)}
    failed_event = {
        "type": "conversation.item.input_audio_transcription.failed",
        "item_id": "utt-2",
        "error": {"message": "bad audio"},
    }

    events = normalize_openai_realtime_event(failed_event, utterances)

    assert len(events) == 1
    assert isinstance(events[0], SttStatusEvent)
    assert events[0].message == "utt-2: bad audio"
    assert utterances == {}


def test_is_fatal_openai_realtime_error_flags_authentication_errors() -> None:
    event = {
        "type": "error",
        "error": {
            "type": "authentication_error",
            "code": "invalid_api_key",
            "message": "bad key",
        },
    }

    assert is_fatal_openai_realtime_error(event) is True


def test_is_retriable_openai_realtime_error_only_retries_transport_failures() -> None:
    invalid_status = InvalidStatus(
        Response(
            status_code=401, reason_phrase="Unauthorized", headers=Headers(), body=b""
        )
    )

    assert is_retriable_openai_realtime_error(OSError("network down")) is True
    assert is_retriable_openai_realtime_error(invalid_status) is False
    assert is_retriable_openai_realtime_error(RuntimeError("boom")) is False


def test_build_transcription_session_update_event_uses_provider_config() -> None:
    event = _build_transcription_session_update_event(
        OpenAIRealtimeProviderConfig(
            language="zh",
            prompt="Expect Mandarin",
            noise_reduction="far_field",
        )
    )

    assert event["session"]["type"] == "transcription"
    assert event["session"]["audio"]["input"]["noise_reduction"] == {
        "type": "far_field"
    }
    assert event["session"]["audio"]["input"]["transcription"]["language"] == "zh"
    assert event["session"]["audio"]["input"]["transcription"]["prompt"] == (
        "Expect Mandarin"
    )


def test_openai_backend_rejects_invalid_capture_shape() -> None:
    with pytest.raises(SttSessionError, match="capture.channels = 1"):
        OpenAIRealtimeBackend(
            capture_config=CaptureConfig(channels=2),
            retry_config=SttRetryConfig(),
            provider_config=OpenAIRealtimeProviderConfig(),
            api_key="test",
            logger=logging.getLogger("test.openai.backend"),
        )


def test_openai_backend_creates_fresh_attempt_state() -> None:
    backend = OpenAIRealtimeBackend(
        capture_config=CaptureConfig(),
        retry_config=SttRetryConfig(),
        provider_config=OpenAIRealtimeProviderConfig(),
        api_key="test",
        logger=logging.getLogger("test.openai.backend.state"),
    )

    context = _make_attempt_context(backend.logger)
    first = backend.create_attempt(context=context)
    second = backend.create_attempt(context=context)

    assert isinstance(first, OpenAIRealtimeAttempt)
    assert isinstance(second, OpenAIRealtimeAttempt)
    assert isinstance(first._state, OpenAIConnectionState)
    assert isinstance(second._state, OpenAIConnectionState)
    assert first._state is not second._state
