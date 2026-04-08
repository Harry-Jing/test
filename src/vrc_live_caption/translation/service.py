"""Run bounded async translation requests without blocking the chatbox pipeline."""

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ..errors import TranslationError
from .types import TranslationBackend, TranslationRequest, TranslationResult


@dataclass(slots=True, frozen=True)
class TranslationMetrics:
    """Expose translation queue and lifecycle counters for diagnostics."""

    pending_requests: int
    dropped_requests: int
    failed_requests: int
    stale_results: int


class AsyncTranslationWorker:
    """Own a bounded async translation queue and one background worker task."""

    def __init__(
        self,
        *,
        backend: TranslationBackend,
        request_timeout_seconds: float,
        max_pending_requests: int,
        logger: logging.Logger,
        on_result: Callable[[TranslationResult], bool],
        on_failure: Callable[[TranslationRequest, BaseException], bool],
    ) -> None:
        self._backend = backend
        self._request_timeout_seconds = request_timeout_seconds
        self._max_pending_requests = max_pending_requests
        self._logger = logger
        self._on_result = on_result
        self._on_failure = on_failure
        self._pending: deque[TranslationRequest] = deque()
        self._wakeup: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._started = False
        self._stop_requested = False
        self._dropped_requests = 0
        self._failed_requests = 0
        self._stale_results = 0

    async def start(self) -> None:
        """Start the background worker task."""
        if self._started:
            return
        self._backend.validate_environment()
        self._wakeup = asyncio.Event()
        self._stop_requested = False
        self._task = asyncio.create_task(
            self._run(), name="vrc-live-caption-translation"
        )
        self._started = True

    def submit(self, request: TranslationRequest) -> None:
        """Queue one translation request and drop the oldest pending item when full."""
        if not self._started:
            raise TranslationError("Translation worker has not been started")

        dropped_request: TranslationRequest | None = None
        if len(self._pending) >= self._max_pending_requests:
            dropped_request = self._pending.popleft()
            self._dropped_requests += 1
            self._logger.warning(
                "translation queue full; dropping oldest request for utterance=%s revision=%s",
                dropped_request.utterance_id,
                dropped_request.revision,
            )
        self._pending.append(request)
        if dropped_request is not None and not self._on_failure(
            dropped_request,
            TranslationError("translation queue full"),
        ):
            self._stale_results += 1
        self._notify_worker()

    async def shutdown(self, *, timeout_seconds: float) -> None:
        """Flush queued work within the timeout, then cancel remaining activity."""
        if self._task is None:
            self._started = False
            return
        self._stop_requested = True
        self._notify_worker()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None
            self._wakeup = None
            self._started = False

    def metrics(self) -> TranslationMetrics:
        """Return the current translation diagnostics snapshot."""
        return TranslationMetrics(
            pending_requests=len(self._pending),
            dropped_requests=self._dropped_requests,
            failed_requests=self._failed_requests,
            stale_results=self._stale_results,
        )

    async def _run(self) -> None:
        while True:
            request = self._next_request()
            if request is None:
                if self._stop_requested:
                    return
                await self._wait_for_signal()
                continue
            await self._process(request)

    def _next_request(self) -> TranslationRequest | None:
        if not self._pending:
            return None
        return self._pending.popleft()

    async def _process(self, request: TranslationRequest) -> None:
        try:
            result = await asyncio.wait_for(
                self._backend.translate(request),
                timeout=self._request_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed_requests += 1
            if not self._on_failure(request, exc):
                self._stale_results += 1
            return

        if not self._on_result(result):
            self._stale_results += 1

    async def _wait_for_signal(self) -> None:
        wakeup = self._wakeup
        if wakeup is None:
            return
        await wakeup.wait()
        wakeup.clear()

    def _notify_worker(self) -> None:
        if self._wakeup is not None:
            self._wakeup.set()


__all__ = [
    "AsyncTranslationWorker",
    "TranslationMetrics",
]
