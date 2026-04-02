import asyncio
import logging

import pytest

from tests.support.stt_fakes import FakeAttempt, FakeBackend
from vrc_live_caption.config import SttRetryConfig
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt import AsyncSttSessionRunner, SttStatus, SttStatusEvent


def _make_audio_queue() -> DropOldestAsyncQueue[AudioChunk]:
    return DropOldestAsyncQueue(
        max_items=4,
        logger=logging.getLogger("test.stt.audio"),
        label="audio queue",
    )


def _require_status_event(event: object) -> SttStatusEvent:
    assert isinstance(event, SttStatusEvent)
    return event


def test_async_stt_session_runner_publishes_ready_and_closing_events() -> None:
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.ready"),
        )

        await runner.start()
        first = await runner.get_event(timeout=0.1)
        second = await runner.get_event(timeout=0.1)
        await runner.close(timeout_seconds=1.0)
        third = await runner.get_event(timeout=0.1)
        fourth = await runner.get_event(timeout=0.1)

        assert _require_status_event(first).status == SttStatus.CONNECTING
        assert _require_status_event(second).status == SttStatus.READY
        assert _require_status_event(third).status == SttStatus.CLOSING
        assert _require_status_event(fourth).status == SttStatus.CLOSED

    asyncio.run(scenario())


def test_async_stt_session_runner_retries_transport_errors() -> None:
    async def scenario() -> None:
        attempt_calls = {"count": 0}

        def make_attempt(context):
            attempt_calls["count"] += 1
            if attempt_calls["count"] == 1:
                return FakeAttempt(context=context, run_error=OSError("network down"))
            return FakeAttempt(context=context, auto_stop=True)

        runner = AsyncSttSessionRunner(
            backend=FakeBackend(attempt_factories=[make_attempt, make_attempt]),
            retry_config=SttRetryConfig(
                connect_timeout_seconds=0.1,
                max_attempts=2,
                initial_backoff_seconds=0.1,
                max_backoff_seconds=0.1,
            ),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.retry"),
        )

        await runner.start()
        statuses = []
        for _ in range(4):
            event = await runner.get_event(timeout=0.1)
            if event is not None:
                status_event = _require_status_event(event)
                statuses.append(status_event.status)
                if status_event.status == SttStatus.READY and len(statuses) > 2:
                    break
        await runner.close(timeout_seconds=1.0)

        assert statuses[0] == SttStatus.CONNECTING
        assert SttStatus.READY in statuses
        assert SttStatus.RETRYING in statuses
        assert attempt_calls["count"] >= 2

    asyncio.run(scenario())


def test_async_stt_session_runner_raises_terminal_error_after_retry_budget() -> None:
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(
                attempt_factories=[
                    lambda context: FakeAttempt(
                        context=context,
                        run_error=OSError("network down"),
                    )
                ]
            ),
            retry_config=SttRetryConfig(
                connect_timeout_seconds=0.1,
                max_attempts=0,
            ),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.error"),
        )

        await runner.start()
        await runner.get_event(timeout=0.1)
        await runner.get_event(timeout=0.1)
        with pytest.raises(SttSessionError, match="fake backend failed after retries"):
            runner.check_health()

    asyncio.run(scenario())


def test_async_stt_session_runner_does_not_report_error_on_external_cancellation() -> (
    None
):
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.cancel"),
        )

        await runner.start()
        first = await runner.get_event(timeout=0.1)
        second = await runner.get_event(timeout=0.1)
        assert _require_status_event(first).status == SttStatus.CONNECTING
        assert _require_status_event(second).status == SttStatus.READY
        assert runner._task is not None

        runner._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner._task

        closed = await runner.get_event(timeout=0.1)
        assert _require_status_event(closed).status == SttStatus.CLOSED
        assert runner._error is None
        runner.check_health()
        assert await runner.get_event(timeout=0.0) is None

    asyncio.run(scenario())
