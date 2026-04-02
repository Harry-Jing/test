"""Build stabilized caption snapshots and dispatch VRChat chatbox output."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .stt import TranscriptRevisionEvent

MAX_CHATBOX_CHARS = 144
MAX_COMMITTED_HISTORY_CHARS = MAX_CHATBOX_CHARS * 4
MAX_RECENT_CLOSED_UTTERANCES = 2048
PARTIAL_MIN_INTERVAL_SECONDS = 1.5
FINAL_SEND_GUARD_SECONDS = 0.3
TYPING_IDLE_TIMEOUT_SECONDS = 1.5

_CONTROL_WHITESPACE_TRANSLATION = str.maketrans(
    {
        "\r": " ",
        "\n": " ",
        "\t": " ",
    }
)


def normalize_chatbox_text(text: str) -> str:
    """Normalize control whitespace so chatbox text stays single-line friendly."""
    return text.translate(_CONTROL_WHITESPACE_TRANSLATION).strip()


def longest_common_prefix(left: str, right: str) -> str:
    """Return the shared prefix between two transcript revisions."""
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return left[:index]


def merge_chatbox_text(base: str, fragment: str) -> str:
    """Merge adjacent chatbox segments while avoiding duplicate overlap."""
    if not base:
        return fragment
    if not fragment:
        return base

    overlap = _longest_suffix_prefix_overlap(base, fragment)
    if overlap:
        return base + fragment[overlap:]
    if _needs_ascii_separator(base, fragment):
        return f"{base} {fragment}"
    return f"{base}{fragment}"


def render_chatbox_text(*, upper: str, lower: str) -> str:
    """Render merged chatbox text clipped to the VRChat character budget."""
    merged = merge_chatbox_text(upper, lower)
    return merged[-MAX_CHATBOX_CHARS:]


def _longest_suffix_prefix_overlap(base: str, fragment: str) -> int:
    limit = min(len(base), len(fragment))
    for size in range(limit, 0, -1):
        if base[-size:] == fragment[:size]:
            return size
    return 0


def _needs_ascii_separator(base: str, fragment: str) -> bool:
    left = base[-1]
    right = fragment[0]
    if left.isspace() or right.isspace():
        return False
    return left.isascii() and left.isalnum() and right.isascii() and right.isalnum()


@dataclass(slots=True, frozen=True)
class ChatboxSnapshot:
    """Store the current committed, active, and rendered chatbox text state."""

    upper_text: str
    lower_text: str
    text: str
    has_active_utterance: bool


@dataclass(slots=True)
class _ActiveUtterance:
    utterance_id: str
    revision: int
    text: str
    stable_prefix: str


class ChatboxTransport(Protocol):
    """Define the transport operations needed by chatbox output."""

    def send_text(self, text: str) -> None:
        """Send rendered chatbox text to the output transport."""
        ...

    def send_typing(self, is_typing: bool) -> None:
        """Send the current typing indicator state to the output transport."""
        ...


class ChatboxStateMachine:
    """Convert transcript revisions into a stable chatbox snapshot."""

    def __init__(
        self,
        *,
        max_closed_utterances: int = MAX_RECENT_CLOSED_UTTERANCES,
        max_committed_history_chars: int = MAX_COMMITTED_HISTORY_CHARS,
    ) -> None:
        """Initialize empty committed history and active utterance state."""
        self._max_closed_utterances = max_closed_utterances
        self._max_committed_history_chars = max_committed_history_chars
        self._committed_history = ""
        self._active: _ActiveUtterance | None = None
        self._closed_utterance_ids: set[str] = set()
        self._closed_utterance_order: deque[str] = deque()

    def is_closed(self, utterance_id: str) -> bool:
        """Return whether the utterance has already been finalized or rolled over."""
        return utterance_id in self._closed_utterance_ids

    def apply_revision(self, event: TranscriptRevisionEvent) -> bool:
        """Apply one transcript revision and report whether the visible snapshot changed."""
        if event.utterance_id in self._closed_utterance_ids:
            return False

        text = normalize_chatbox_text(event.text)
        if self._active is not None and event.utterance_id != self._active.utterance_id:
            self._rollover_active_utterance()

        if not text:
            if event.is_final:
                self._mark_closed(event.utterance_id)
            return False

        if self._active is None:
            return self._apply_new_utterance(event=event, text=text)
        return self._apply_active_utterance(event=event, text=text)

    def snapshot(self) -> ChatboxSnapshot:
        """Build the current committed, active, and rendered chatbox snapshot."""
        upper_text = self._committed_history
        lower_text = ""

        if self._active is not None:
            if self._active.stable_prefix:
                upper_text = merge_chatbox_text(upper_text, self._active.stable_prefix)
            if self._active.text.startswith(self._active.stable_prefix):
                lower_text = self._active.text[len(self._active.stable_prefix) :]

        return ChatboxSnapshot(
            upper_text=upper_text,
            lower_text=lower_text,
            text=render_chatbox_text(upper=upper_text, lower=lower_text),
            has_active_utterance=self._active is not None,
        )

    def _apply_new_utterance(
        self, *, event: TranscriptRevisionEvent, text: str
    ) -> bool:
        if event.is_final:
            self._commit_text(text)
            self._mark_closed(event.utterance_id)
            return True

        self._active = _ActiveUtterance(
            utterance_id=event.utterance_id,
            revision=event.revision,
            text=text,
            stable_prefix="",
        )
        return True

    def _apply_active_utterance(
        self,
        *,
        event: TranscriptRevisionEvent,
        text: str,
    ) -> bool:
        assert self._active is not None
        if event.revision <= self._active.revision:
            return False

        if event.is_final:
            self._commit_text(text)
            self._mark_closed(event.utterance_id)
            self._active = None
            return True

        if text == self._active.text:
            self._active.revision = event.revision
            return False

        candidate = longest_common_prefix(self._active.text, text)
        if len(candidate) > len(self._active.stable_prefix):
            self._active.stable_prefix = candidate
        self._active.text = text
        self._active.revision = event.revision
        return True

    def _rollover_active_utterance(self) -> None:
        assert self._active is not None
        if self._active.stable_prefix:
            self._commit_text(self._active.stable_prefix)
        self._mark_closed(self._active.utterance_id)
        self._active = None

    def _mark_closed(self, utterance_id: str) -> None:
        if utterance_id in self._closed_utterance_ids:
            return
        self._closed_utterance_ids.add(utterance_id)
        self._closed_utterance_order.append(utterance_id)
        while len(self._closed_utterance_order) > self._max_closed_utterances:
            oldest = self._closed_utterance_order.popleft()
            self._closed_utterance_ids.discard(oldest)

    def _commit_text(self, text: str) -> None:
        normalized = normalize_chatbox_text(text)
        if not normalized:
            return
        committed = merge_chatbox_text(self._committed_history, normalized)
        self._committed_history = committed[-self._max_committed_history_chars :]


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
            0.0, (self._last_text_send_at + self._partial_min_interval_seconds) - now
        )

    def _typing_is_due(self, now: float) -> bool:
        return self._typing_due_in(now) <= 0.0

    def _typing_due_in(self, now: float) -> float:
        return self._guard_due_in(now)

    def _guard_due_in(self, now: float) -> float:
        if self._last_send_at is None:
            return 0.0
        return max(0.0, (self._last_send_at + self._final_send_guard_seconds) - now)


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
        state_machine: ChatboxStateMachine | None = None,
        rate_limiter: ChatboxRateLimiter | None = None,
    ) -> None:
        self._transport = transport
        self._emit_line = emit_line
        self._logger = logger
        self._now = now
        self._typing_idle_timeout_seconds = typing_idle_timeout_seconds
        self._state_machine = state_machine or ChatboxStateMachine()
        self._rate_limiter = rate_limiter or ChatboxRateLimiter(now=now)
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

        if not self._state_machine.apply_revision(event):
            self._notify_worker()
            return

        snapshot = self._state_machine.snapshot()
        if snapshot.text:
            self._rate_limiter.queue_text(snapshot.text, is_final=event.is_final)
        if event.is_final:
            self._last_partial_activity_at = None
            self._rate_limiter.request_typing(False)
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
        """Best-effort flush pending text and typing updates before shutdown."""
        snapshot = self._state_machine.snapshot()
        if snapshot.text:
            self._rate_limiter.queue_text(snapshot.text, is_final=True)
        self._rate_limiter.request_typing(False)
        self._last_partial_activity_at = None

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


__all__ = [
    "ChatboxAction",
    "ChatboxOutput",
    "ChatboxRateLimiter",
    "ChatboxSnapshot",
    "ChatboxStateMachine",
    "ChatboxTransport",
    "MAX_CHATBOX_CHARS",
    "merge_chatbox_text",
    "normalize_chatbox_text",
    "render_chatbox_text",
]
