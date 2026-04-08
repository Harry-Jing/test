import asyncio
import logging

import pytest

from tests.support.fakes.stt import FakeAttempt, FakeSttBackend
from tests.unit.stt._runner_support import (
    ManualAttempt,
    make_audio_queue,
    require_status_event,
)
from vrc_live_caption.config import SttRetryConfig
from vrc_live_caption.errors import SttSessionError, VrcLiveCaptionError
from vrc_live_caption.stt import (
    AsyncSttSessionRunner,
    AttemptContext,
    ConnectionAttempt,
    SttStatus,
)


@pytest.mark.asyncio
class TestAsyncSttSessionRunnerFailureHandling:
    async def test_when_transport_error_is_retriable__then_it_emits_retrying_and_connects_again(
        self,
    ) -> None:
        attempt_calls = {"count": 0}

        def make_attempt(context):
            attempt_calls["count"] += 1
            if attempt_calls["count"] == 1:
                return FakeAttempt(context=context, run_error=OSError("network down"))
            return FakeAttempt(context=context, auto_stop=True)

        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(attempt_factories=[make_attempt, make_attempt]),
            retry_config=SttRetryConfig(
                connect_timeout_seconds=0.1,
                max_attempts=2,
                initial_backoff_seconds=0.1,
                max_backoff_seconds=0.1,
            ),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.retry"),
        )

        await runner.start()
        statuses = []
        for _ in range(4):
            event = await runner.get_event(timeout=0.1)
            if event is not None:
                status_event = require_status_event(event)
                statuses.append(status_event.status)
                if status_event.status == SttStatus.READY and len(statuses) > 2:
                    break
        await runner.close(timeout_seconds=1.0)

        assert statuses[0] == SttStatus.CONNECTING
        assert SttStatus.READY in statuses
        assert SttStatus.RETRYING in statuses
        assert attempt_calls["count"] >= 2

    async def test_when_close_happens_during_retry_backoff__then_it_stops_without_starting_another_attempt(
        self,
    ) -> None:
        attempt_calls = {"count": 0}

        def make_attempt(context: AttemptContext) -> ConnectionAttempt:
            attempt_calls["count"] += 1
            return ManualAttempt(
                context=context,
                ready_message="fake ready",
                run_error=OSError("network down"),
            )

        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(attempt_factories=[make_attempt, make_attempt]),
            retry_config=SttRetryConfig(
                connect_timeout_seconds=0.1,
                max_attempts=2,
                initial_backoff_seconds=5.0,
                max_backoff_seconds=5.0,
            ),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.backoff_stop"),
        )

        await runner.start()
        statuses = [
            require_status_event(await runner.get_event(timeout=0.1)).status
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
        assert require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CLOSING
        )
        assert require_status_event(await runner.get_event(timeout=0.1)).status == (
            SttStatus.CLOSED
        )

    async def test_when_retry_budget_is_exhausted__then_check_health_raises_terminal_error(
        self,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(
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
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.error"),
        )

        await runner.start()
        await runner.get_event(timeout=0.1)
        await runner.get_event(timeout=0.1)

        with pytest.raises(SttSessionError, match="fake backend failed after retries"):
            runner.check_health()

    async def test_when_error_is_not_retriable__then_it_emits_error_and_closed(
        self,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(
                attempt_factories=[
                    lambda context: ManualAttempt(
                        context=context,
                        ready_message="fake ready",
                        run_error=RuntimeError("boom"),
                    )
                ],
                retriable_errors=(OSError,),
            ),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
            event_buffer_max_items=16,
            logger=logging.getLogger("test.stt.runner.non_retriable"),
        )

        await runner.start()
        statuses = [
            require_status_event(await runner.get_event(timeout=0.1)).status
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

    async def test_when_close_times_out__then_it_raises_stt_session_error(self) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(
                attempt_factories=[
                    lambda context: ManualAttempt(
                        context=context,
                        ready_message="fake ready",
                        wait_forever=True,
                    )
                ]
            ),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
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


class TestAsyncSttSessionRunnerLogging:
    def test_when_failure_is_domain_specific__then_it_logs_without_exc_info(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        runner = AsyncSttSessionRunner(
            backend=FakeSttBackend(),
            retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
            audio_queue=make_audio_queue(),
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
