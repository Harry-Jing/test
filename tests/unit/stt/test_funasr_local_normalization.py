import asyncio
import logging
import ssl
from types import SimpleNamespace

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.http11 import Response

from tests.unit.stt._funasr_local_support import make_attempt_context
from vrc_live_caption.config import (
    CaptureConfig,
    FunasrLocalProviderConfig,
    SttRetryConfig,
)
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.protocol import (
    build_error_message,
    build_ready_message,
    build_transcript_message,
)
from vrc_live_caption.stt import SttStatus
from vrc_live_caption.stt.funasr_local import (
    FatalFunasrLocalServerError,
    FunasrLocalAttempt,
    FunasrLocalBackend,
    FunasrLocalConnectionState,
    FunasrLocalReadyEvent,
    _build_ssl_context,
    build_funasr_local_url,
    is_retriable_funasr_local_error,
    normalize_funasr_local_transcript_event,
    parse_funasr_local_ready_event,
)
from vrc_live_caption.stt.types import SttStatusEvent, TranscriptRevisionEvent


class TestFunasrLocalTranscriptNormalization:
    def test_when_online_and_offline_events_arrive__then_revisions_are_tracked(
        self,
    ) -> None:
        revisions: dict[int, int] = {}

        first = normalize_funasr_local_transcript_event(
            build_transcript_message(
                phase="online",
                segment_id=3,
                text="hello",
                is_final=False,
            ),
            revisions,
        )
        second = normalize_funasr_local_transcript_event(
            build_transcript_message(
                phase="offline",
                segment_id=3,
                text="hello world",
                is_final=True,
            ),
            revisions,
        )

        assert first == [
            TranscriptRevisionEvent(
                utterance_id="segment-3",
                revision=1,
                text="hello",
                is_final=False,
            )
        ]
        assert second == [
            TranscriptRevisionEvent(
                utterance_id="segment-3",
                revision=2,
                text="hello world",
                is_final=True,
            )
        ]
        assert revisions == {}

    def test_when_event_is_not_a_transcript__then_it_returns_no_revisions(self) -> None:
        assert normalize_funasr_local_transcript_event({"type": "ready"}, {}) == []

    def test_when_segment_id_is_missing__then_it_raises_stt_session_error(self) -> None:
        with pytest.raises(SttSessionError, match="missing segment_id"):
            normalize_funasr_local_transcript_event({"type": "transcript"}, {})


class TestFunasrLocalReadyEventParsing:
    def test_when_ready_event_contains_device_metadata__then_it_keeps_it(self) -> None:
        ready_event = parse_funasr_local_ready_event(
            build_ready_message(
                "ready for test",
                resolved_device="cuda:0",
                device_policy="auto",
            )
        )

        assert ready_event is not None
        assert ready_event.message == "ready for test"
        assert ready_event.resolved_device == "cuda:0"
        assert ready_event.device_policy == "auto"

    def test_when_ready_event_uses_object_fields_and_blank_strings__then_it_normalizes_them(
        self,
    ) -> None:
        ready_event = parse_funasr_local_ready_event(
            SimpleNamespace(
                type="ready",
                message=None,
                resolved_device="   ",
                device_policy="auto",
            )
        )

        assert ready_event is not None
        assert ready_event.message == ""
        assert ready_event.resolved_device is None
        assert ready_event.device_policy == "auto"
        assert parse_funasr_local_ready_event({"type": "transcript"}) is None


class TestFunasrLocalHelpers:
    def test_when_transport_errors_are_classified__then_only_retryable_cases_return_true(
        self,
    ) -> None:
        invalid_status = InvalidStatus(Response(503, "busy", Headers(), b""))

        assert is_retriable_funasr_local_error(ConnectionClosedOK(None, None)) is True
        assert is_retriable_funasr_local_error(asyncio.TimeoutError()) is True
        assert is_retriable_funasr_local_error(OSError("boom")) is True
        assert is_retriable_funasr_local_error(invalid_status) is False
        assert (
            is_retriable_funasr_local_error(FatalFunasrLocalServerError("fatal"))
            is False
        )
        assert is_retriable_funasr_local_error(RuntimeError("other")) is False

    def test_when_backend_helper_text_is_requested__then_it_formats_expected_messages(
        self,
    ) -> None:
        backend = FunasrLocalBackend(
            capture_config=CaptureConfig(),
            retry_config=SttRetryConfig(),
            provider_config=FunasrLocalProviderConfig(
                host="localhost",
                port=9002,
                use_ssl=True,
            ),
            logger=logging.getLogger("test.funasr_local.backend_helpers"),
        )

        assert (
            build_funasr_local_url(backend._provider_config) == "wss://localhost:9002"
        )
        assert (
            backend.retrying_message(OSError("boom"), 2, 1.5)
            == "local sidecar error: boom; retrying in 1.5s"
        )
        assert isinstance(backend.exhausted_error(OSError("boom")), SttSessionError)

    def test_when_backend_description_and_ssl_context_are_requested__then_it_returns_expected_values(
        self,
    ) -> None:
        backend = FunasrLocalBackend(
            capture_config=CaptureConfig(),
            retry_config=SttRetryConfig(),
            provider_config=FunasrLocalProviderConfig(host="localhost", port=9002),
            logger=logging.getLogger("test.funasr_local.messages"),
        )

        assert backend.describe() == "funasr_local (localhost:9002)"
        assert backend.connecting_message() == "connecting to local FunASR sidecar"
        assert backend.closing_message() == "closing local FunASR sidecar session"
        assert backend.closed_message() == "local FunASR sidecar session closed"
        assert (
            backend.stop_timeout_message()
            == "Timed out waiting for the local FunASR sidecar session to stop"
        )
        assert _build_ssl_context(FunasrLocalProviderConfig(use_ssl=False)) is None

        context = _build_ssl_context(FunasrLocalProviderConfig(use_ssl=True))

        assert isinstance(context, ssl.SSLContext)
        assert context.check_hostname is False
        assert context.verify_mode == ssl.CERT_NONE

    def test_when_attempt_formats_ready_message__then_it_includes_device_metadata_if_present(
        self,
    ) -> None:
        context, _, _ = make_attempt_context()
        attempt = FunasrLocalAttempt(
            state=FunasrLocalConnectionState(),
            context=context,
            provider_config=FunasrLocalProviderConfig(host="localhost", port=9001),
            capture_config=CaptureConfig(),
            logger=logging.getLogger("test.funasr_local.format_ready"),
        )

        assert (
            attempt._format_ready_message(
                FunasrLocalReadyEvent(
                    message="ready",
                    resolved_device="cuda:0",
                    device_policy="auto",
                )
            )
            == "FunASR local sidecar ready (localhost:9001, device=cuda:0, policy=auto)"
        )
        assert (
            attempt._format_ready_message(FunasrLocalReadyEvent(message="ready"))
            == "FunASR local sidecar ready (localhost:9001)"
        )

    def test_when_non_fatal_server_error_arrives_after_ready__then_attempt_publishes_error_status(
        self,
    ) -> None:
        context, events, _ = make_attempt_context()
        attempt = FunasrLocalAttempt(
            state=FunasrLocalConnectionState(),
            context=context,
            provider_config=FunasrLocalProviderConfig(),
            capture_config=CaptureConfig(),
            logger=logging.getLogger("test.funasr_local.handle_error"),
        )

        ready = attempt._handle_server_message(build_ready_message("ready"))
        assert ready is not None

        result = attempt._handle_server_message(
            build_error_message("soft boom", fatal=False)
        )

        assert result is None
        assert events == [SttStatusEvent(status=SttStatus.ERROR, message="soft boom")]
