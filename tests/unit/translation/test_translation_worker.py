import asyncio
import logging

import pytest

from tests.unit.translation._support import SlowTranslationBackend
from vrc_live_caption.translation import AsyncTranslationWorker, TranslationRequest


@pytest.mark.asyncio
class TestAsyncTranslationWorker:
    async def test_when_queue_is_full__then_it_drops_the_oldest_request(self) -> None:
        backend = SlowTranslationBackend()
        completed = []
        failed = []
        worker = AsyncTranslationWorker(
            backend=backend,
            request_timeout_seconds=1.0,
            max_pending_requests=1,
            logger=logging.getLogger("test.translation.worker"),
            on_result=lambda result: completed.append(result) or True,
            on_failure=lambda request, exc: (
                failed.append((request.utterance_id, str(exc))) or True
            ),
        )

        await worker.start()
        worker.submit(
            TranslationRequest(
                utterance_id="utt-1",
                revision=1,
                text="first",
                target_language="en",
            )
        )
        worker.submit(
            TranslationRequest(
                utterance_id="utt-2",
                revision=1,
                text="second",
                target_language="en",
            )
        )
        await asyncio.sleep(0.02)
        backend.release.set()
        await asyncio.sleep(0.05)
        await worker.shutdown(timeout_seconds=1.0)

        metrics = worker.metrics()
        assert failed[0][0] == "utt-1"
        assert backend.requests[0].utterance_id == "utt-2"
        assert completed[0].utterance_id == "utt-2"
        assert metrics.dropped_requests == 1

    async def test_when_shutdown_cancels_inflight_work__then_it_does_not_count_as_failure(
        self,
    ) -> None:
        backend = SlowTranslationBackend()
        failed = []
        worker = AsyncTranslationWorker(
            backend=backend,
            request_timeout_seconds=5.0,
            max_pending_requests=2,
            logger=logging.getLogger("test.translation.cancel"),
            on_result=lambda result: True,
            on_failure=lambda request, exc: (
                failed.append((request.utterance_id, type(exc))) or True
            ),
        )

        await worker.start()
        worker.submit(
            TranslationRequest(
                utterance_id="utt-cancel",
                revision=1,
                text="cancel me",
                target_language="en",
            )
        )
        await asyncio.sleep(0.02)
        await worker.shutdown(timeout_seconds=0.05)

        assert failed == []
        metrics = worker.metrics()
        assert metrics.failed_requests == 0
        assert metrics.stale_results == 0

    async def test_when_request_times_out__then_it_reports_timeout_failure(
        self,
    ) -> None:
        backend = SlowTranslationBackend()
        failed = []
        worker = AsyncTranslationWorker(
            backend=backend,
            request_timeout_seconds=0.01,
            max_pending_requests=1,
            logger=logging.getLogger("test.translation.timeout"),
            on_result=lambda result: True,
            on_failure=lambda request, exc: failed.append(type(exc).__name__) or True,
        )

        await worker.start()
        worker.submit(
            TranslationRequest(
                utterance_id="utt-timeout",
                revision=1,
                text="timeout",
                target_language="en",
            )
        )
        await asyncio.sleep(0.05)
        await worker.shutdown(timeout_seconds=1.0)

        assert failed == ["TimeoutError"]
