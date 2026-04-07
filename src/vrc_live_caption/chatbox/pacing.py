"""Pace chatbox text and typing state updates."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .model import FINAL_SEND_GUARD_SECONDS, PARTIAL_MIN_INTERVAL_SECONDS


@dataclass(slots=True, frozen=True)
class ChatboxAction:
    """Describe one queued chatbox send action for text or typing state."""

    kind: str
    text: str | None = None
    typing: bool | None = None
    is_final: bool = False


class ChatboxRateLimiter:
    """Coalesce chatbox text and typing edges behind pacing rules."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        partial_min_interval_seconds: float = PARTIAL_MIN_INTERVAL_SECONDS,
        final_send_guard_seconds: float = FINAL_SEND_GUARD_SECONDS,
    ) -> None:
        self._now = now
        self._partial_min_interval_seconds = partial_min_interval_seconds
        self._final_send_guard_seconds = final_send_guard_seconds
        self._pending_text: str | None = None
        self._pending_text_is_final = False
        self._pending_typing: bool | None = None
        self._last_sent_text: str | None = None
        self._last_sent_typing: bool | None = None
        self._last_send_at: float | None = None
        self._last_text_send_at: float | None = None

    def queue_text(self, text: str, *, is_final: bool) -> None:
        """Queue the latest rendered text snapshot for paced delivery."""
        if not text:
            return
        if self._pending_text == text:
            if is_final:
                self._pending_text_is_final = True
            return
        if self._pending_text_is_final and not is_final:
            self._pending_text = text
            return
        if self._pending_text is None and text == self._last_sent_text:
            return
        self._pending_text = text
        self._pending_text_is_final = is_final

    def request_typing(self, is_typing: bool) -> None:
        """Queue a typing-state edge unless it matches the last sent state."""
        if self._pending_typing is None and self._last_sent_typing == is_typing:
            return
        self._pending_typing = is_typing

    def tick(self) -> ChatboxAction | None:
        """Return the next due chatbox action, or `None` if nothing can send yet."""
        now = self._now()
        if self._pending_text is not None:
            if self._pending_text == self._last_sent_text:
                self._pending_text = None
                self._pending_text_is_final = False
            else:
                text_is_final = self._pending_text_is_final
                if not self._text_is_due(now, is_final=text_is_final):
                    return None
                text = self._pending_text
                self._pending_text = None
                self._pending_text_is_final = False
                self._last_sent_text = text
                self._last_send_at = now
                self._last_text_send_at = now
                return ChatboxAction(kind="text", text=text, is_final=text_is_final)

        if self._pending_typing is not None:
            if self._pending_typing == self._last_sent_typing:
                self._pending_typing = None
            else:
                if not self._typing_is_due(now):
                    return None
                typing = self._pending_typing
                self._pending_typing = None
                self._last_sent_typing = typing
                self._last_send_at = now
                return ChatboxAction(kind="typing", typing=typing)
        return None

    def has_pending(self) -> bool:
        """Return whether unsent text or typing work remains after deduplication."""
        if self._pending_text == self._last_sent_text:
            self._pending_text = None
            self._pending_text_is_final = False
        if self._pending_typing == self._last_sent_typing:
            self._pending_typing = None
        return self._pending_text is not None or self._pending_typing is not None

    def next_send_delay(self) -> float:
        """Return the time until the next pending action becomes sendable."""
        now = self._now()
        delays: list[float] = []
        if (
            self._pending_text is not None
            and self._pending_text != self._last_sent_text
        ):
            delays.append(self._text_due_in(now, is_final=self._pending_text_is_final))
        if (
            self._pending_typing is not None
            and self._pending_typing != self._last_sent_typing
        ):
            delays.append(self._typing_due_in(now))
        if not delays:
            return 0.0
        return max(0.0, min(delays))

    def _text_is_due(self, now: float, *, is_final: bool) -> bool:
        return self._text_due_in(now, is_final=is_final) <= 0.0

    def _text_due_in(self, now: float, *, is_final: bool) -> float:
        if is_final:
            return self._guard_due_in(now)
        if self._last_text_send_at is None:
            return 0.0
        return max(
            0.0,
            (self._last_text_send_at + self._partial_min_interval_seconds) - now,
        )

    def _typing_is_due(self, now: float) -> bool:
        return self._typing_due_in(now) <= 0.0

    def _typing_due_in(self, now: float) -> float:
        return self._guard_due_in(now)

    def _guard_due_in(self, now: float) -> float:
        if self._last_send_at is None:
            return 0.0
        return max(0.0, (self._last_send_at + self._final_send_guard_seconds) - now)
