"""Provide bounded async queues that keep the newest items under backpressure."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class QueueClosedError(RuntimeError):
    """Raised when a closed async queue can no longer produce items."""


class DropOldestAsyncQueue(Generic[T]):
    """Keep the newest items in a bounded async queue with drop-oldest semantics."""

    def __init__(
        self,
        *,
        max_items: int,
        logger: logging.Logger,
        label: str,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_items = max_items
        self._logger = logger
        self._label = label
        self._now = now
        self._items: deque[T] = deque()
        self._waiters: deque[asyncio.Future[T]] = deque()
        self._closed = False
        self._dropped_items = 0
        self._last_drop_log_at = 0.0

    @property
    def dropped_items(self) -> int:
        """Return the total number of items dropped due to queue pressure."""
        return self._dropped_items

    @property
    def max_items(self) -> int:
        """Return the configured maximum item count."""
        return self._max_items

    def put_nowait(self, item: T) -> None:
        """Enqueue an item immediately, dropping the oldest queued item when full."""
        if self._closed:
            raise QueueClosedError(f"{self._label} is closed")

        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.done():
                continue
            waiter.set_result(item)
            return

        if len(self._items) >= self._max_items:
            self._items.popleft()
            self._dropped_items += 1
            current_time = self._now()
            if current_time - self._last_drop_log_at >= 1.0:
                self._last_drop_log_at = current_time
                self._logger.warning(
                    "%s full; dropping oldest item to stay responsive", self._label
                )

        self._items.append(item)

    def put_from_thread(self, item: T, loop: asyncio.AbstractEventLoop) -> None:
        """Schedule an enqueue from a non-event-loop thread."""
        loop.call_soon_threadsafe(self._threadsafe_put_nowait, item)

    def _threadsafe_put_nowait(self, item: T) -> None:
        try:
            self.put_nowait(item)
        except QueueClosedError:
            return

    async def get(self, timeout: float | None = None) -> T:
        """Return the next item or raise after the timeout or queue closure."""
        if self._items:
            return self._items.popleft()
        if self._closed:
            raise QueueClosedError(f"{self._label} is closed")

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[T] = loop.create_future()
        self._waiters.append(waiter)
        try:
            if timeout is None:
                return await waiter
            return await asyncio.wait_for(waiter, timeout=timeout)
        except asyncio.TimeoutError:
            if not waiter.done():
                waiter.cancel()
            raise
        except asyncio.CancelledError:
            if not waiter.done():
                waiter.cancel()
            raise

    def close(self) -> None:
        """Close the queue and fail any pending getters."""
        if self._closed:
            return
        self._closed = True
        error = QueueClosedError(f"{self._label} is closed")
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.done():
                continue
            waiter.set_exception(error)

    def qsize(self) -> int:
        """Return the current queued item count."""
        return len(self._items)

    def empty(self) -> bool:
        """Return whether the queue currently holds no items."""
        return not self._items


__all__ = ["DropOldestAsyncQueue", "QueueClosedError"]
