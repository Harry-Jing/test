import asyncio
import logging

import pytest

from tests.support.fakes.stt import FakeSttBackend
from tests.unit.stt._runner_support import make_audio_queue, require_status_event
from vrc_live_caption.config import SttRetryConfig
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.stt import AsyncSttSessionRunner, SttStatus, SttStatusEvent


@pytest.mark.asyncio
class TestAsyncSttSessionRunnerLifecycle:
    async def test_when_runner_starts_and_closes__then_it_publishes_connecting_ready_closing_and_closed(
        self,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.ready"),
        )

        await runner.start()
        first = await runner.get_event(timeout=0.1)
        second = await runner.get_event(timeout=0.1)
        await runner.close(timeout_seconds=1.0)
        third = await runner.get_event(timeout=0.1)
        fourth = await runner.get_event(timeout=0.1)

        assert require_status_event(first).status == SttStatus.CONNECTING
        assert require_status_event(second).status == SttStatus.READY
        assert require_status_event(third).status == SttStatus.CLOSING
        assert require_status_event(fourth).status == SttStatus.CLOSED

    async def test_when_start_is_called_twice__then_it_reuses_the_existing_task(
        self,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.idempotent"),
        )

        await runner.start()
        first_task = runner._task
        await runner.start()

        assert runner._task is first_task
        assert require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CONNECTING
        )
        assert require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.READY
        )

        await runner.close(timeout_seconds=1.0)

    async def test_when_stop_is_requested_before_ready__then_start_raises_stt_session_error(
        self,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.stopped_before_ready"),
        )
        runner._stop_requested.set()

        with pytest.raises(
            SttSessionError, match="STT runner stopped before becoming ready"
        ):
            await runner.start()

        assert require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CONNECTING
        )
        assert require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CLOSED
        )

    async def test_when_runner_is_cancelled_externally__then_it_only_emits_closed(
        self,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.cancel"),
        )

        await runner.start()
        first = await runner.get_event(timeout=0.1)
        second = await runner.get_event(timeout=0.1)
        assert require_status_event(first).status == SttStatus.CONNECTING
        assert require_status_event(second).status == SttStatus.READY
        assert runner._task is not None

        runner._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner._task

        closed = await runner.get_event(timeout=0.1)
        assert require_status_event(closed).status == SttStatus.CLOSED
        assert runner._error is None
        runner.check_health()
        assert await runner.get_event(timeout=0.0) is None

    async def test_when_close_is_called_before_start__then_event_queue_is_closed(
        self,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.close_without_start"),
        )

        await runner.close(timeout_seconds=1.0)

        assert await runner.get_event(timeout=0.0) is None

    async def test_when_mark_ready_is_called_and_queue_is_closed__then_publish_ignores_closed_queue(
        self,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.mark_ready"),
        )

        runner._mark_ready("first")
        runner._mark_ready("second")

        first = require_status_event(await runner.get_event(timeout=0.0))
        second = require_status_event(await runner.get_event(timeout=0.0))

        assert first.status == SttStatus.READY
        assert first.message == "first"
        assert second.status == SttStatus.READY
        assert second.message == "second"
        assert runner._first_ready.is_set() is True

        runner._event_queue.close()
        runner._publish(SttStatusEvent(status=SttStatus.ERROR, message="ignored"))
