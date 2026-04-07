import asyncio
import logging
from typing import Any

from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt.types import AttemptContext


def make_audio_queue() -> DropOldestAsyncQueue[AudioChunk]:
    return DropOldestAsyncQueue(
        max_items=4,
        logger=logging.getLogger("test.funasr_local.audio"),
        label="audio queue",
    )


class FakeConnection:
    def __init__(self, responses: list[bytes | str | BaseException]) -> None:
        self._responses = list(responses)
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes | str:
        from websockets.exceptions import ConnectionClosedOK
        from websockets.frames import Close

        if not self._responses:
            raise ConnectionClosedOK(Close(1000, "done"), Close(1000, "done"))
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


def make_attempt_context(
    *,
    audio_queue: DropOldestAsyncQueue[AudioChunk] | None = None,
) -> tuple[AttemptContext, list[Any], list[str]]:
    events: list[Any] = []
    ready_messages: list[str] = []
    context = AttemptContext(
        audio_queue=audio_queue or make_audio_queue(),
        publish_event=events.append,
        mark_ready=ready_messages.append,
        stop_requested=asyncio.Event(),
        connect_timeout_seconds=0.1,
        logger=logging.getLogger("test.funasr_local.attempt"),
    )
    return context, events, ready_messages
