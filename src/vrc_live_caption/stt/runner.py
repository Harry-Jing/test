"""Run configured STT backends inside the shared asyncio-driven session shell."""

import asyncio
import logging

from ..config import SttRetryConfig
from ..errors import SttSessionError, VrcLiveCaptionError
from ..runtime import AudioChunk, DropOldestAsyncQueue, QueueClosedError
from .types import AttemptContext, SttBackend, SttEvent, SttStatus, SttStatusEvent

_INTERRUPT_EXCEPTIONS = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)


class AsyncSttSessionRunner:
    """Run one configured STT backend with retry and shutdown orchestration."""

    def __init__(
        self,
        *,
        backend: SttBackend,
        retry_config: SttRetryConfig,
        audio_queue: DropOldestAsyncQueue[AudioChunk],
        event_buffer_max_items: int,
        logger: logging.Logger,
    ) -> None:
        self._backend = backend
        self._retry_config = retry_config
        self._audio_queue = audio_queue
        self._event_queue = DropOldestAsyncQueue[SttEvent](
            max_items=event_buffer_max_items,
            logger=logger,
            label="STT event queue",
        )
        self._logger = logger
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = asyncio.Event()
        self._first_ready = asyncio.Event()
        self._error: BaseException | None = None
        self._started = False

    @property
    def backend_description(self) -> str:
        """Return the CLI-friendly backend description."""
        return self._backend.describe()

    @property
    def event_dropped_items(self) -> int:
        """Return the total number of dropped STT events."""
        return self._event_queue.dropped_items

    async def start(self) -> None:
        """Start the session runner and wait until the first ready edge or failure."""
        if self._started:
            return
        self._task = asyncio.create_task(
            self._run(), name=f"vrc-live-caption-stt-{self._backend.name}"
        )
        self._started = True

        ready_waiter = asyncio.create_task(self._first_ready.wait())
        done, pending = await asyncio.wait(
            {ready_waiter, self._task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for waiter in pending:
            if waiter is ready_waiter:
                waiter.cancel()
        if ready_waiter in done and self._first_ready.is_set():
            return
        self.check_health()
        raise SttSessionError("STT runner stopped before becoming ready")

    async def get_event(self, timeout: float = 0.0) -> SttEvent | None:
        """Return the next event or `None` after the timeout or queue closure."""
        try:
            return await self._event_queue.get(timeout=timeout)
        except asyncio.TimeoutError:
            return None
        except QueueClosedError:
            return None

    def check_health(self) -> None:
        """Raise when the runner task previously recorded a failure."""
        if self._error is None:
            return
        raise SttSessionError(str(self._error)) from self._error

    async def close(self, *, timeout_seconds: float) -> None:
        """Request shutdown, wait for the session loop, and close the event queue."""
        if self._task is None:
            self._event_queue.close()
            return

        if not self._stop_requested.is_set():
            self._stop_requested.set()
            self._publish(
                SttStatusEvent(
                    status=SttStatus.CLOSING,
                    message=self._backend.closing_message(),
                )
            )

        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise SttSessionError(self._backend.stop_timeout_message()) from exc
        finally:
            self._event_queue.close()

    async def _run(self) -> None:
        try:
            await self._run_attempts()
        except _INTERRUPT_EXCEPTIONS:
            raise
        except Exception as exc:
            self._error = exc
            self._log_failure("STT session runner failed", exc)
            self._publish(SttStatusEvent(status=SttStatus.ERROR, message=str(exc)))
        finally:
            self._publish(
                SttStatusEvent(
                    status=SttStatus.CLOSED,
                    message=self._backend.closed_message(),
                )
            )

    async def _run_attempts(self) -> None:
        self._publish(
            SttStatusEvent(
                status=SttStatus.CONNECTING,
                message=self._backend.connecting_message(),
            )
        )

        retry_attempt = 0
        while not self._stop_requested.is_set():
            context = AttemptContext(
                audio_queue=self._audio_queue,
                publish_event=self._publish,
                mark_ready=self._mark_ready,
                stop_requested=self._stop_requested,
                connect_timeout_seconds=self._retry_config.connect_timeout_seconds,
                logger=self._backend.logger,
            )
            attempt = self._backend.create_attempt(context=context)
            try:
                await attempt.run()
                if self._stop_requested.is_set():
                    return
                raise SttSessionError(
                    f"{self._backend.describe()} connection closed unexpectedly"
                )
            except _INTERRUPT_EXCEPTIONS:
                raise
            except Exception as exc:
                if self._stop_requested.is_set():
                    return
                if not self._backend.is_retriable_error(exc):
                    raise
                if retry_attempt >= self._retry_config.max_attempts:
                    raise self._backend.exhausted_error(exc) from exc

                retry_attempt += 1
                backoff_seconds = min(
                    self._retry_config.max_backoff_seconds,
                    self._retry_config.initial_backoff_seconds
                    * (2 ** (retry_attempt - 1)),
                )
                self._publish(
                    SttStatusEvent(
                        status=SttStatus.RETRYING,
                        message=self._backend.retrying_message(
                            exc, retry_attempt, backoff_seconds
                        ),
                        attempt=retry_attempt,
                    )
                )
                try:
                    await asyncio.wait_for(
                        self._stop_requested.wait(), timeout=backoff_seconds
                    )
                    return
                except asyncio.TimeoutError:
                    continue

    def _publish(self, event: SttEvent) -> None:
        try:
            self._event_queue.put_nowait(event)
        except QueueClosedError:
            return

    def _mark_ready(self, message: str) -> None:
        if not self._first_ready.is_set():
            self._first_ready.set()
        self._publish(SttStatusEvent(status=SttStatus.READY, message=message))

    def _log_failure(self, message: str, exc: BaseException) -> None:
        if isinstance(exc, VrcLiveCaptionError):
            self._logger.error("%s: %s", message, exc)
            return
        self._logger.exception(message)


__all__ = ["AsyncSttSessionRunner"]
