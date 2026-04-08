import asyncio
import logging

from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt import AttemptContext, ConnectionAttempt, SttStatusEvent


def make_audio_queue() -> DropOldestAsyncQueue[AudioChunk]:
    return DropOldestAsyncQueue(
        max_items=4,
        logger=logging.getLogger("test.stt.audio"),
        label="audio queue",
    )


def require_status_event(event: object) -> SttStatusEvent:
    assert isinstance(event, SttStatusEvent)
    return event


class ManualAttempt(ConnectionAttempt):
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
