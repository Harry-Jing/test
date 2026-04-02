import asyncio
import logging

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
