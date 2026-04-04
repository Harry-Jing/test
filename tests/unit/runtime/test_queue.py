import asyncio
import logging

import pytest

from vrc_live_caption.runtime import DropOldestAsyncQueue, QueueClosedError


class _FakeClock:
    def __init__(self) -> None:
        self.current = 1.0

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def test_drop_oldest_async_queue_drops_oldest_item_when_full(caplog) -> None:
    clock = _FakeClock()
    logger = logging.getLogger("test.runtime.queue.drop")
    queue = DropOldestAsyncQueue[int](
        max_items=2,
        logger=logger,
        label="audio queue",
        now=clock.now,
    )

    with caplog.at_level(logging.WARNING, logger=logger.name):
        queue.put_nowait(1)
        queue.put_nowait(2)
        queue.put_nowait(3)
        queue.put_nowait(4)

    first = asyncio.run(queue.get(timeout=0.0))
    second = asyncio.run(queue.get(timeout=0.0))

    assert first == 3
    assert second == 4
    assert queue.dropped_items == 2
    assert caplog.messages == [
        "audio queue full; dropping oldest item to stay responsive"
    ]


def test_drop_oldest_async_queue_warning_is_throttled(caplog) -> None:
    clock = _FakeClock()
    logger = logging.getLogger("test.runtime.queue.throttle")
    queue = DropOldestAsyncQueue[int](
        max_items=1,
        logger=logger,
        label="audio queue",
        now=clock.now,
    )

    with caplog.at_level(logging.WARNING, logger=logger.name):
        queue.put_nowait(1)
        queue.put_nowait(2)
        queue.put_nowait(3)
        clock.advance(1.1)
        queue.put_nowait(4)

    assert caplog.messages == [
        "audio queue full; dropping oldest item to stay responsive",
        "audio queue full; dropping oldest item to stay responsive",
    ]


def test_drop_oldest_async_queue_closure_unblocks_waiters() -> None:
    async def scenario() -> None:
        logger = logging.getLogger("test.runtime.queue.close")
        queue = DropOldestAsyncQueue[int](
            max_items=1,
            logger=logger,
            label="audio queue",
        )
        waiter = asyncio.create_task(queue.get(timeout=None))
        await asyncio.sleep(0)
        queue.close()
        try:
            await waiter
        except QueueClosedError:
            return
        raise AssertionError("expected QueueClosedError")

    asyncio.run(scenario())


def test_drop_oldest_async_queue_rejects_put_after_close() -> None:
    queue = DropOldestAsyncQueue[int](
        max_items=1,
        logger=logging.getLogger("test.runtime.queue.closed_put"),
        label="audio queue",
    )
    queue.close()

    with pytest.raises(QueueClosedError, match="audio queue is closed"):
        queue.put_nowait(1)


def test_drop_oldest_async_queue_skips_completed_waiters_when_putting() -> None:
    async def scenario() -> None:
        queue = DropOldestAsyncQueue[int](
            max_items=1,
            logger=logging.getLogger("test.runtime.queue.done_waiter"),
            label="audio queue",
        )

        waiter = asyncio.create_task(queue.get(timeout=0.01))
        with pytest.raises(asyncio.TimeoutError):
            await waiter

        queue.put_nowait(7)

        assert await queue.get(timeout=0.0) == 7

    asyncio.run(scenario())


def test_drop_oldest_async_queue_timeout_cancels_waiter() -> None:
    async def scenario() -> None:
        queue = DropOldestAsyncQueue[int](
            max_items=1,
            logger=logging.getLogger("test.runtime.queue.timeout_waiter"),
            label="audio queue",
        )

        with pytest.raises(asyncio.TimeoutError):
            await queue.get(timeout=0.01)

        assert len(queue._waiters) == 1
        assert queue._waiters[0].cancelled() is True

    asyncio.run(scenario())


def test_drop_oldest_async_queue_cancellation_cancels_waiter() -> None:
    async def scenario() -> None:
        queue = DropOldestAsyncQueue[int](
            max_items=1,
            logger=logging.getLogger("test.runtime.queue.cancel_waiter"),
            label="audio queue",
        )

        waiter = asyncio.create_task(queue.get(timeout=None))
        await asyncio.sleep(0)
        waiter.cancel()

        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert len(queue._waiters) == 1
        assert queue._waiters[0].cancelled() is True

    asyncio.run(scenario())


def test_drop_oldest_async_queue_threadsafe_put_is_ignored_after_close() -> None:
    async def scenario() -> None:
        queue = DropOldestAsyncQueue[int](
            max_items=1,
            logger=logging.getLogger("test.runtime.queue.threadsafe_close"),
            label="audio queue",
        )
        queue.close()

        queue._threadsafe_put_nowait(3)
        queue.put_from_thread(4, asyncio.get_running_loop())
        await asyncio.sleep(0)

        assert queue.qsize() == 0
        assert queue.empty() is True

    asyncio.run(scenario())


def test_drop_oldest_async_queue_reports_size_and_empty_state() -> None:
    queue = DropOldestAsyncQueue[int](
        max_items=2,
        logger=logging.getLogger("test.runtime.queue.size"),
        label="audio queue",
    )

    assert queue.empty() is True
    assert queue.qsize() == 0

    queue.put_nowait(1)
    queue.put_nowait(2)

    assert queue.empty() is False
    assert queue.qsize() == 2
