"""Bridge transcript revisions to OSC chatbox output."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

from ..config import TranslationConfig
from ..stt import TranscriptRevisionEvent
from ..translation import (
    AsyncTranslationWorker,
    TranslationBackend,
    TranslationRequest,
    TranslationResult,
)
from .model import TYPING_IDLE_TIMEOUT_SECONDS
from .pacing import ChatboxAction, ChatboxRateLimiter
from .state import TranslatedChatboxStateMachine, _ChatboxStateMachineProtocol
from .text import normalize_chatbox_text


class ChatboxTransport(Protocol):
    """Define the transport operations needed by chatbox output."""

    def send_text(self, text: str) -> None:
        """Send rendered chatbox text to the output transport."""

    def send_typing(self, is_typing: bool) -> None:
        """Send the current typing indicator state to the output transport."""


class ChatboxOutput:
    """Bridge transcript revisions to OSC transport with stabilization and pacing."""

    def __init__(
        self,
        *,
        transport: ChatboxTransport,
        emit_line: Callable[[str], None],
        logger: logging.Logger,
        now: Callable[[], float] = time.monotonic,
        typing_idle_timeout_seconds: float = TYPING_IDLE_TIMEOUT_SECONDS,
        translation_config: TranslationConfig | None = None,
        translation_backend: TranslationBackend | None = None,
        state_machine: _ChatboxStateMachineProtocol | None = None,
        rate_limiter: ChatboxRateLimiter | None = None,
    ) -> None:
        self._transport = transport
        self._emit_line = emit_line
        self._logger = logger
        self._now = now
        self._typing_idle_timeout_seconds = typing_idle_timeout_seconds
        self._translation_config = translation_config or TranslationConfig()
        self._output_mode = (
            self._translation_config.output_mode
            if self._translation_config.enabled
            else "source"
        )
        self._translation_backend = translation_backend
        self._state_machine = state_machine or self._build_state_machine()
        self._rate_limiter = rate_limiter or ChatboxRateLimiter(now=now)
        self._translation_worker = self._build_translation_worker()
        self._last_partial_activity_at: float | None = None
        self._stop_requested = False
        self._started = False
        self._task: asyncio.Task[None] | None = None
        self._wakeup: asyncio.Event | None = None

    async def start(self) -> None:
        """Start the async dispatch worker that owns paced OSC sends."""
        if self._started:
            return
        self._stop_requested = False
        self._wakeup = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="vrc-live-caption-chatbox")
        if self._translation_worker is not None:
            await self._translation_worker.start()
        self._started = True

    def handle_revision(self, event: TranscriptRevisionEvent) -> None:
        """Apply one transcript revision, update typing state, and queue output."""
        normalized_text = normalize_chatbox_text(event.text)
        if (
            normalized_text
            and not event.is_final
            and not self._state_machine.is_closed(event.utterance_id)
        ):
            self._last_partial_activity_at = self._now()
            self._rate_limiter.request_typing(True)

        translation_pending = self._should_translate(event=event, text=normalized_text)
        if not self._state_machine.apply_revision(
            event,
            translation_pending=translation_pending,
        ):
            self._notify_worker()
            return

        snapshot = self._state_machine.snapshot()
        if snapshot.text:
            self._rate_limiter.queue_text(snapshot.text, is_final=event.is_final)
        if event.is_final:
            self._last_partial_activity_at = None
            self._rate_limiter.request_typing(False)
            if translation_pending and self._translation_worker is not None:
                assert self._translation_config.target_language is not None
                self._translation_worker.submit(
                    TranslationRequest(
                        utterance_id=event.utterance_id,
                        revision=event.revision,
                        text=normalized_text,
                        source_language=self._translation_config.source_language,
                        target_language=self._translation_config.target_language,
                    )
                )
        self._notify_worker()

    def tick(self) -> None:
        """Send one due chatbox action if pacing allows it."""
        if self._typing_idle_expired():
            self._last_partial_activity_at = None
            self._rate_limiter.request_typing(False)
        action = self._rate_limiter.tick()
        if action is None:
            return
        self._dispatch(action)

    async def shutdown(self, *, timeout_seconds: float = 1.0) -> None:
        """Best-effort flush pending text, translation, and typing updates before shutdown."""
        snapshot = self._state_machine.snapshot()
        if snapshot.text:
            self._rate_limiter.queue_text(snapshot.text, is_final=True)
        self._rate_limiter.request_typing(False)
        self._last_partial_activity_at = None

        if self._translation_worker is not None:
            await self._translation_worker.shutdown(timeout_seconds=timeout_seconds)

        self._stop_requested = True
        self._notify_worker()

        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout_seconds)
        finally:
            self._task = None
            self._wakeup = None
            self._started = False

    def diagnostics_snapshot(self) -> dict[str, int]:
        """Return translation diagnostics that should appear in heartbeat logs."""
        if self._translation_worker is None:
            return {}
        metrics = self._translation_worker.metrics()
        return {
            "translation_pending": metrics.pending_requests,
            "translation_dropped": metrics.dropped_requests,
            "translation_failed": metrics.failed_requests,
            "translation_stale": metrics.stale_results,
        }

    async def _run(self) -> None:
        while True:
            self.tick()
            if self._stop_requested and not self._rate_limiter.has_pending():
                return
            delay = self._next_wakeup_delay()
            await self._wait_for_signal(delay)

    async def _wait_for_signal(self, delay: float | None) -> None:
        wakeup = self._wakeup
        if wakeup is None:
            return
        try:
            if delay is None:
                await wakeup.wait()
            else:
                await asyncio.wait_for(wakeup.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return
        finally:
            wakeup.clear()

    def _notify_worker(self) -> None:
        if self._wakeup is not None:
            self._wakeup.set()

    def _next_wakeup_delay(self) -> float | None:
        delays: list[float] = []
        if self._rate_limiter.has_pending():
            delays.append(self._rate_limiter.next_send_delay())
        if self._last_partial_activity_at is not None:
            idle_delay = (
                self._last_partial_activity_at + self._typing_idle_timeout_seconds
            ) - self._now()
            delays.append(max(0.0, idle_delay))
        if not delays:
            return None
        return max(0.0, min(delays))

    def _typing_idle_expired(self) -> bool:
        if self._last_partial_activity_at is None:
            return False
        return (
            self._now() - self._last_partial_activity_at
        ) >= self._typing_idle_timeout_seconds

    def _dispatch(self, action: ChatboxAction) -> None:
        try:
            if action.kind == "text":
                assert action.text is not None
                self._transport.send_text(action.text)
                self._emit_line(f"[chatbox] {action.text}")
                return
            assert action.typing is not None
            self._transport.send_typing(action.typing)
        except Exception as exc:
            self._logger.error("OSC output failed for %s: %s", action.kind, exc)

    def _build_state_machine(self) -> _ChatboxStateMachineProtocol:
        return TranslatedChatboxStateMachine(
            output_mode=self._output_mode,
            chatbox_layout=self._translation_config.chatbox_layout,
        )

    def _build_translation_worker(self) -> AsyncTranslationWorker | None:
        if not self._translation_config.enabled or self._output_mode == "source":
            return None
        if self._translation_backend is None:
            raise RuntimeError("Translation backend is required for translated output")
        return AsyncTranslationWorker(
            backend=self._translation_backend,
            request_timeout_seconds=self._translation_config.request_timeout_seconds,
            max_pending_requests=self._translation_config.max_pending_finals,
            logger=self._logger.getChild("translation"),
            on_result=self._handle_translation_result,
            on_failure=self._handle_translation_failure,
        )

    def _should_translate(self, *, event: TranscriptRevisionEvent, text: str) -> bool:
        if self._translation_worker is None:
            return False
        if not event.is_final or not text:
            return False
        return self._translation_config.strategy == "final_only"

    def _handle_translation_result(self, result: TranslationResult) -> bool:
        changed = self._state_machine.apply_translation_result(result)
        if changed:
            snapshot = self._state_machine.snapshot()
            if snapshot.text:
                self._rate_limiter.queue_text(snapshot.text, is_final=True)
            self._notify_worker()
        return changed

    def _handle_translation_failure(
        self,
        request: TranslationRequest,
        exc: BaseException,
    ) -> bool:
        self._logger.warning(
            "translation failed for utterance=%s revision=%s: %s",
            request.utterance_id,
            request.revision,
            exc,
        )
        changed = self._state_machine.mark_translation_failed(
            request.utterance_id,
            request.revision,
        )
        if changed:
            snapshot = self._state_machine.snapshot()
            if snapshot.text:
                self._rate_limiter.queue_text(snapshot.text, is_final=True)
            self._notify_worker()
        return changed
