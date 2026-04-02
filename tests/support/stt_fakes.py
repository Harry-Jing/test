import logging
from collections import deque
from collections.abc import Callable

from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.runtime import AudioChunk
from vrc_live_caption.stt import (
    AttemptContext,
    ConnectionAttempt,
    SttBackend,
    SttEvent,
    SttStatus,
    SttStatusEvent,
    TranscriptRevisionEvent,
)


class FakeAttempt(ConnectionAttempt):
    def __init__(
        self,
        *,
        context: AttemptContext,
        events: list[SttEvent] | None = None,
        run_error: BaseException | None = None,
        ready_message: str = "fake ready",
        auto_stop: bool = False,
    ) -> None:
        self.context = context
        self.events = events or []
        self.run_error = run_error
        self.ready_message = ready_message
        self.auto_stop = auto_stop
        self.audio_chunks: list[AudioChunk] = []

    async def run(self) -> None:
        self.context.mark_ready(self.ready_message)
        if self.run_error is not None:
            raise self.run_error

        for event in self.events:
            self.context.publish_event(event)

        if self.auto_stop:
            await self.context.stop_requested.wait()
            return

        chunk = await self.context.audio_queue.get(timeout=0.01)
        self.audio_chunks.append(chunk)
        await self.context.stop_requested.wait()


class FakeBackend(SttBackend):
    name = "fake"

    def __init__(
        self,
        *,
        attempts: list[FakeAttempt] | None = None,
        attempt_factories: list[Callable[[AttemptContext], ConnectionAttempt]]
        | None = None,
        retriable_errors: tuple[type[BaseException], ...] = (OSError,),
        logger: logging.Logger | None = None,
    ) -> None:
        self._attempts = deque(attempts or [])
        self._attempt_factories = deque(attempt_factories or [])
        self._retriable_errors = retriable_errors
        self._logger = logger or logging.getLogger("test.stt.fake")

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def describe(self) -> str:
        return "fake backend"

    def connecting_message(self) -> str:
        return "connecting fake backend"

    def closing_message(self) -> str:
        return "closing fake backend"

    def closed_message(self) -> str:
        return "fake backend closed"

    def stop_timeout_message(self) -> str:
        return "Timed out waiting for fake backend to stop"

    def create_attempt(self, *, context: AttemptContext) -> ConnectionAttempt:
        if self._attempt_factories:
            factory = self._attempt_factories.popleft()
            return factory(context)
        if self._attempts:
            attempt = self._attempts.popleft()
            attempt.context = context
            return attempt
        return FakeAttempt(context=context, auto_stop=True)

    def is_retriable_error(self, exc: BaseException) -> bool:
        return isinstance(exc, self._retriable_errors)

    def retrying_message(
        self, exc: BaseException, attempt: int, backoff_seconds: float
    ) -> str:
        return f"transport error: {exc}; retrying in {backoff_seconds:.1f}s"

    def exhausted_error(self, exc: BaseException) -> BaseException:
        return SttSessionError("fake backend failed after retries")


def make_transcript_event(
    *,
    utterance_id: str = "utt-1",
    revision: int = 1,
    text: str = "hello",
    is_final: bool = False,
) -> TranscriptRevisionEvent:
    return TranscriptRevisionEvent(
        utterance_id=utterance_id,
        revision=revision,
        text=text,
        is_final=is_final,
    )


def make_status_event(
    *,
    status: SttStatus = SttStatus.READY,
    message: str | None = None,
    attempt: int | None = None,
) -> SttStatusEvent:
    return SttStatusEvent(status=status, message=message, attempt=attempt)
