import asyncio
import logging

import pytest

from tests.support.stt_fakes import FakeAttempt, FakeBackend
from vrc_live_caption.config import SttRetryConfig
from vrc_live_caption.errors import SttSessionError, VrcLiveCaptionError
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt import (
    AsyncSttSessionRunner,
    AttemptContext,
    ConnectionAttempt,
    SttStatus,
    SttStatusEvent,
)


def _make_audio_queue() -> DropOldestAsyncQueue[AudioChunk]:
    return DropOldestAsyncQueue(
        max_items=4,
        logger=logging.getLogger("test.stt.audio"),
        label="audio queue",
    )


def _require_status_event(event: object) -> SttStatusEvent:
    assert isinstance(event, SttStatusEvent)
    return event


class _ManualAttempt(ConnectionAttempt):
    def __init__(
        self,
        *,
        context: AttemptContext,
        ready_message: str | None = None,
        run_error: BaseException | None = None,
        wait_forever: bool = False,
    ) -> None:
        self.context = context
        self.ready_message = ready_message
        self.run_error = run_error
        self.wait_forever = wait_forever

    async def run(self) -> None:
        if self.ready_message is not None:
            self.context.mark_ready(self.ready_message)
        if self.run_error is not None:
            raise self.run_error
        if self.wait_forever:
            await asyncio.Event().wait()


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


def test_async_stt_session_runner_start_is_idempotent() -> None:
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.idempotent"),
        )

        await runner.start()
        first_task = runner._task
        await runner.start()

        assert runner._task is first_task
        assert _require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CONNECTING
        )
        assert _require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.READY
        )

        await runner.close(timeout_seconds=1.0)

    asyncio.run(scenario())


def test_async_stt_session_runner_start_raises_when_stopped_before_ready() -> None:
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.stopped_before_ready"),
        )
        runner._stop_requested.set()

        with pytest.raises(
            SttSessionError,
            match="STT runner stopped before becoming ready",
        ):
            await runner.start()

        assert _require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CONNECTING
        )
        assert _require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CLOSED
        )

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


def test_async_stt_session_runner_stops_during_retry_backoff() -> None:
    async def scenario() -> None:
        attempt_calls = {"count": 0}

        def make_attempt(context: AttemptContext) -> ConnectionAttempt:
            attempt_calls["count"] += 1
            return _ManualAttempt(
                context=context,
                ready_message="fake ready",
                run_error=OSError("network down"),
            )

        runner = AsyncSttSessionRunner(
            backend=FakeBackend(attempt_factories=[make_attempt, make_attempt]),
            retry_config=SttRetryConfig(
                connect_timeout_seconds=0.1,
                max_attempts=2,
                initial_backoff_seconds=5.0,
                max_backoff_seconds=5.0,
            ),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.backoff_stop"),
        )

        await runner.start()
        statuses = [
            _require_status_event(await runner.get_event(timeout=0.1)).status
            for _ in range(3)
        ]
        assert statuses == [
            SttStatus.CONNECTING,
            SttStatus.READY,
            SttStatus.RETRYING,
        ]

        await runner.close(timeout_seconds=1.0)
        await asyncio.sleep(0)

        assert attempt_calls["count"] == 1
        assert _require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CLOSING
        )
        assert _require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CLOSED
        )

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


def test_async_stt_session_runner_surfaces_non_retriable_errors() -> None:
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(
                attempt_factories=[
                    lambda context: _ManualAttempt(
                        context=context,
                        ready_message="fake ready",
                        run_error=RuntimeError("boom"),
                    )
                ],
                retriable_errors=(OSError,),
            ),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.non_retriable"),
        )

        await runner.start()
        statuses = [
            _require_status_event(await runner.get_event(timeout=0.1)).status
            for _ in range(4)
        ]

        assert statuses == [
            SttStatus.CONNECTING,
            SttStatus.READY,
            SttStatus.ERROR,
            SttStatus.CLOSED,
        ]
        with pytest.raises(SttSessionError, match="boom"):
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


def test_async_stt_session_runner_close_without_start_closes_event_queue() -> None:
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.close_without_start"),
        )

        await runner.close(timeout_seconds=1.0)

        assert await runner.get_event(timeout=0.0) is None

    asyncio.run(scenario())


def test_async_stt_session_runner_close_raises_on_timeout() -> None:
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(
                attempt_factories=[
                    lambda context: _ManualAttempt(
                        context=context,
                        ready_message="fake ready",
                        wait_forever=True,
                    )
                ]
            ),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.close_timeout"),
        )

        await runner.start()
        await runner.get_event(timeout=0.1)
        await runner.get_event(timeout=0.1)

        with pytest.raises(
            SttSessionError,
            match="Timed out waiting for fake backend to stop",
        ):
            await runner.close(timeout_seconds=0.01)

        assert runner._task is not None
        runner._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner._task

    asyncio.run(scenario())


def test_async_stt_session_runner_mark_ready_and_publish_ignore_closed_queue() -> None:
    async def scenario() -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=_make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.mark_ready"),
        )

        runner._mark_ready("first")
        runner._mark_ready("second")

        first = _require_status_event(await runner.get_event(timeout=0.0))
        second = _require_status_event(await runner.get_event(timeout=0.0))

        assert first.status == SttStatus.READY
        assert first.message == "first"
        assert second.status == SttStatus.READY
        assert second.message == "second"
        assert runner._first_ready.is_set() is True

        runner._event_queue.close()
        runner._publish(SttStatusEvent(status=SttStatus.ERROR, message="ignored"))

    asyncio.run(scenario())


def test_async_stt_session_runner_log_failure_uses_expected_logger_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = AsyncSttSessionRunner(
        backend=FakeBackend(),
        retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
        audio_queue=_make_audio_queue(),
        event_buffer_max_items=16,
        logger=logging.getLogger("test.stt.runner.log_failure"),
    )

    with caplog.at_level(logging.ERROR, logger=runner._logger.name):
        runner._log_failure("known failure", VrcLiveCaptionError("known"))
        try:
            raise RuntimeError("unexpected")
        except RuntimeError as exc:
            runner._log_failure("generic failure", exc)

    assert caplog.messages[0] == "known failure: known"
    assert caplog.records[1].message == "generic failure"
    assert caplog.records[1].exc_info is not None
