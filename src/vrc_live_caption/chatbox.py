"""Build stabilized caption snapshots and dispatch VRChat chatbox output."""

import asyncio
import logging
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .config import (
    TranslationChatboxLayoutConfig,
    TranslationChatboxLayoutWidthsConfig,
    TranslationConfig,
)
from .stt import TranscriptRevisionEvent
from .translation import (
    AsyncTranslationWorker,
    TranslationBackend,
    TranslationRequest,
    TranslationResult,
)

MAX_CHATBOX_CHARS = 144
MAX_CHATBOX_LINES = 9
MAX_COMMITTED_HISTORY_CHARS = MAX_CHATBOX_CHARS * 4
MAX_RECENT_CLOSED_UTTERANCES = 2048
PARTIAL_MIN_INTERVAL_SECONDS = 1.5
FINAL_SEND_GUARD_SECONDS = 0.3
TYPING_IDLE_TIMEOUT_SECONDS = 1.5
ELLIPSIS = "..."
_NARROW_ASCII_CHARS = frozenset("ilI1")

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


def _clip_chatbox_line(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    normalized = normalize_chatbox_text(text)
    if len(normalized) <= budget:
        return normalized
    if budget <= len(ELLIPSIS):
        return normalized[:budget]
    return normalized[: budget - len(ELLIPSIS)] + ELLIPSIS


def _clip_bilingual_block(source: str, target: str, budget: int) -> tuple[str, str]:
    if budget <= 0:
        return "", ""
    if budget == 1:
        return _clip_chatbox_line(source, 1), ""

    source_text = normalize_chatbox_text(source)
    target_text = normalize_chatbox_text(target)
    content_budget = budget - 1
    if len(source_text) + len(target_text) <= content_budget:
        return source_text, target_text

    source_budget = max(1, content_budget // 2)
    target_budget = max(1, content_budget - source_budget)
    source_budget = min(source_budget, len(source_text))
    target_budget = min(target_budget, len(target_text))
    remaining = content_budget - source_budget - target_budget

    if remaining > 0 and len(source_text) > source_budget:
        extra = min(remaining, len(source_text) - source_budget)
        source_budget += extra
        remaining -= extra
    if remaining > 0 and len(target_text) > target_budget:
        target_budget += min(remaining, len(target_text) - target_budget)

    return (
        _clip_chatbox_line(source_text, source_budget),
        _clip_chatbox_line(target_text, target_budget),
    )


def _group_char_count(lines: tuple[str, ...]) -> int:
    if not lines:
        return 0
    return sum(len(line) for line in lines) + max(0, len(lines) - 1)


def _flatten_groups(groups: list[tuple[str, ...]]) -> list[str]:
    lines: list[str] = []
    for group in groups:
        lines.extend(group)
    return lines


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


def _merge_chatbox_segments(segments: list[str]) -> str:
    merged = ""
    for segment in segments:
        normalized = normalize_chatbox_text(segment)
        if normalized:
            merged = merge_chatbox_text(merged, normalized)
    return merged


def _chatbox_visual_width(
    char: str, widths: TranslationChatboxLayoutWidthsConfig
) -> float:
    if not char:
        return 0.0
    if char in _NARROW_ASCII_CHARS:
        return widths.ascii_narrow
    if char.isascii():
        if char.isupper():
            return widths.ascii_upper
        return widths.ascii_lower
    if unicodedata.east_asian_width(char) in {"W", "F"}:
        return widths.cjk
    return widths.fallback


def _clip_chatbox_tail_by_visual_width(
    text: str,
    *,
    max_visual_width: float,
    widths: TranslationChatboxLayoutWidthsConfig,
) -> str:
    if max_visual_width <= 0.0:
        return ""
    normalized = normalize_chatbox_text(text)
    if not normalized:
        return ""

    used_width = 0.0
    kept_reversed: list[str] = []
    for char in reversed(normalized):
        char_width = _chatbox_visual_width(char, widths)
        if kept_reversed and used_width + char_width > max_visual_width:
            break
        kept_reversed.append(char)
        used_width += char_width

    return "".join(reversed(kept_reversed)).lstrip()


def _clip_chatbox_tail_by_chars(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    normalized = normalize_chatbox_text(text)
    if len(normalized) <= budget:
        return normalized
    return normalized[-budget:].lstrip()


def _render_chatbox_paragraph(
    *,
    segments: list[str],
    max_visual_width: float,
    widths: TranslationChatboxLayoutWidthsConfig,
) -> str:
    merged = _merge_chatbox_segments(segments)
    return _clip_chatbox_tail_by_visual_width(
        merged,
        max_visual_width=max_visual_width,
        widths=widths,
    )


def _fit_stacked_two_zone_char_budget(
    *,
    source_text: str,
    target_text: str,
    separator: str,
    max_chatbox_chars: int,
    source_visible_lines: int,
    target_visible_lines: int,
) -> tuple[str, str]:
    separator_chars = len(separator)
    content_budget = max(0, max_chatbox_chars - separator_chars)
    if len(source_text) + len(target_text) <= content_budget:
        return source_text, target_text

    total_visible_lines = source_visible_lines + target_visible_lines
    source_budget = int(content_budget * (source_visible_lines / total_visible_lines))
    target_budget = content_budget - source_budget

    source_budget = min(source_budget, len(source_text))
    target_budget = min(target_budget, len(target_text))
    remaining = content_budget - source_budget - target_budget

    if remaining > 0 and len(source_text) > source_budget:
        extra = min(remaining, len(source_text) - source_budget)
        source_budget += extra
        remaining -= extra
    if remaining > 0 and len(target_text) > target_budget:
        target_budget += min(remaining, len(target_text) - target_budget)

    return (
        _clip_chatbox_tail_by_chars(source_text, source_budget),
        _clip_chatbox_tail_by_chars(target_text, target_budget),
    )


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


@dataclass(slots=True)
class _TranslatedClosedUtterance:
    utterance_id: str
    revision: int
    source_text: str
    target_text: str | None = None
    translation_pending: bool = False


class ChatboxTransport(Protocol):
    """Define the transport operations needed by chatbox output."""

    def send_text(self, text: str) -> None:
        """Send rendered chatbox text to the output transport."""
        ...

    def send_typing(self, is_typing: bool) -> None:
        """Send the current typing indicator state to the output transport."""
        ...


class _ChatboxStateMachineProtocol(Protocol):
    def is_closed(self, utterance_id: str) -> bool:
        """Return whether the utterance has already been finalized or rolled over."""
        ...

    def apply_revision(
        self,
        event: TranscriptRevisionEvent,
        *,
        translation_pending: bool = False,
    ) -> bool:
        """Apply one transcript revision and report whether the visible snapshot changed."""
        ...

    def apply_translation_result(self, result: TranslationResult) -> bool:
        """Update one finalized utterance with translated text."""
        ...

    def mark_translation_failed(self, utterance_id: str, revision: int) -> bool:
        """Mark one translation attempt as failed or stale."""
        ...

    def snapshot(self) -> ChatboxSnapshot:
        """Build the current committed, active, and rendered snapshot."""
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

    def apply_revision(
        self,
        event: TranscriptRevisionEvent,
        *,
        translation_pending: bool = False,
    ) -> bool:
        """Apply one transcript revision and report whether the visible snapshot changed."""
        del translation_pending
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

    def apply_translation_result(self, result: TranslationResult) -> bool:
        """Ignore translation updates in source-only mode."""
        del result
        return False

    def mark_translation_failed(self, utterance_id: str, revision: int) -> bool:
        """Ignore translation failures in source-only mode."""
        del utterance_id, revision
        return False

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


class TranslatedChatboxStateMachine:
    """Render translated chatbox output with target or stacked bilingual layouts."""

    def __init__(
        self,
        *,
        output_mode: str,
        chatbox_layout: TranslationChatboxLayoutConfig | None = None,
        max_closed_utterances: int = MAX_RECENT_CLOSED_UTTERANCES,
        max_chatbox_chars: int = MAX_CHATBOX_CHARS,
        max_chatbox_lines: int = MAX_CHATBOX_LINES,
    ) -> None:
        self._output_mode = output_mode
        self._chatbox_layout = chatbox_layout or TranslationChatboxLayoutConfig()
        self._max_closed_utterances = max_closed_utterances
        self._max_chatbox_chars = max_chatbox_chars
        self._max_chatbox_lines = max_chatbox_lines
        self._active: _ActiveUtterance | None = None
        self._closed_entries: deque[_TranslatedClosedUtterance] = deque()
        self._closed_by_utterance_id: dict[str, _TranslatedClosedUtterance] = {}
        self._closed_utterance_ids: set[str] = set()

    def is_closed(self, utterance_id: str) -> bool:
        """Return whether the utterance has already been finalized or rolled over."""
        return utterance_id in self._closed_utterance_ids

    def apply_revision(
        self,
        event: TranscriptRevisionEvent,
        *,
        translation_pending: bool = False,
    ) -> bool:
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
            return self._apply_new_utterance(
                event=event,
                text=text,
                translation_pending=translation_pending,
            )
        return self._apply_active_utterance(
            event=event,
            text=text,
            translation_pending=translation_pending,
        )

    def apply_translation_result(self, result: TranslationResult) -> bool:
        """Update one finalized utterance with translated text when still current."""
        entry = self._closed_by_utterance_id.get(result.utterance_id)
        if entry is None:
            return False
        if entry.revision != result.revision or not entry.translation_pending:
            return False
        translated_text = normalize_chatbox_text(result.translated_text)
        if not translated_text:
            entry.translation_pending = False
            return False
        entry.target_text = translated_text
        entry.translation_pending = False
        return self._output_mode in {"target", "source_target"}

    def mark_translation_failed(self, utterance_id: str, revision: int) -> bool:
        """Mark one translation attempt as failed without changing visible text."""
        entry = self._closed_by_utterance_id.get(utterance_id)
        if entry is None:
            return False
        if entry.revision != revision or not entry.translation_pending:
            return False
        entry.translation_pending = False
        return False

    def snapshot(self) -> ChatboxSnapshot:
        """Build the current translated chatbox snapshot."""
        if self._output_mode == "source_target":
            return self._snapshot_source_target()

        closed_groups = [
            self._render_closed_entry(entry) for entry in self._closed_entries
        ]
        active_group = self._render_active_group()

        groups: list[tuple[str, ...]] = [group for group in closed_groups if group]
        if active_group:
            groups.append(active_group)

        selected_groups = self._select_visible_groups(groups)
        rendered_lines = _flatten_groups(selected_groups)
        text = "\n".join(rendered_lines)

        if active_group and selected_groups and selected_groups[-1] == active_group:
            upper_text = "\n".join(_flatten_groups(selected_groups[:-1]))
            lower_text = "\n".join(active_group)
        else:
            upper_text = text
            lower_text = ""

        return ChatboxSnapshot(
            upper_text=upper_text,
            lower_text=lower_text,
            text=text,
            has_active_utterance=self._active is not None,
        )

    def _snapshot_source_target(self) -> ChatboxSnapshot:
        layout = self._chatbox_layout
        widths = layout.widths
        source_paragraph = _render_chatbox_paragraph(
            segments=self._source_segments(),
            max_visual_width=(
                layout.source_visible_lines * layout.visual_line_width_units
            ),
            widths=widths,
        )
        target_paragraph = _render_chatbox_paragraph(
            segments=self._target_segments(),
            max_visual_width=(
                layout.target_visible_lines * layout.visual_line_width_units
            ),
            widths=widths,
        )
        source_paragraph, target_paragraph, text = self._render_stacked_two_zone_text(
            source_text=source_paragraph,
            target_text=target_paragraph,
        )
        return ChatboxSnapshot(
            upper_text=source_paragraph,
            lower_text=target_paragraph,
            text=text,
            has_active_utterance=self._active is not None,
        )

    def _apply_new_utterance(
        self,
        *,
        event: TranscriptRevisionEvent,
        text: str,
        translation_pending: bool,
    ) -> bool:
        if event.is_final:
            self._append_closed_entry(
                utterance_id=event.utterance_id,
                revision=event.revision,
                source_text=text,
                translation_pending=translation_pending,
            )
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
        translation_pending: bool,
    ) -> bool:
        assert self._active is not None
        if event.revision <= self._active.revision:
            return False

        if event.is_final:
            self._append_closed_entry(
                utterance_id=event.utterance_id,
                revision=event.revision,
                source_text=text,
                translation_pending=translation_pending,
            )
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
            self._append_closed_entry(
                utterance_id=self._active.utterance_id,
                revision=self._active.revision,
                source_text=self._active.stable_prefix,
                translation_pending=False,
            )
        else:
            self._mark_closed(self._active.utterance_id)
        self._active = None

    def _append_closed_entry(
        self,
        *,
        utterance_id: str,
        revision: int,
        source_text: str,
        translation_pending: bool,
    ) -> None:
        entry = _TranslatedClosedUtterance(
            utterance_id=utterance_id,
            revision=revision,
            source_text=normalize_chatbox_text(source_text),
            translation_pending=translation_pending,
        )
        self._closed_entries.append(entry)
        self._closed_by_utterance_id[utterance_id] = entry
        self._mark_closed(utterance_id)
        while len(self._closed_entries) > self._max_closed_utterances:
            oldest = self._closed_entries.popleft()
            self._closed_by_utterance_id.pop(oldest.utterance_id, None)
            self._closed_utterance_ids.discard(oldest.utterance_id)

    def _mark_closed(self, utterance_id: str) -> None:
        self._closed_utterance_ids.add(utterance_id)

    def _render_closed_entry(
        self, entry: _TranslatedClosedUtterance
    ) -> tuple[str, ...]:
        if self._output_mode == "target" and entry.target_text:
            return (_clip_chatbox_line(entry.target_text, self._max_chatbox_chars),)
        return (_clip_chatbox_line(entry.source_text, self._max_chatbox_chars),)

    def _render_active_group(self) -> tuple[str, ...]:
        if self._active is None:
            return ()
        return (_clip_chatbox_line(self._active.text, self._max_chatbox_chars),)

    def _select_visible_groups(
        self, groups: list[tuple[str, ...]]
    ) -> list[tuple[str, ...]]:
        selected_reversed: list[tuple[str, ...]] = []
        used_lines = 0
        used_chars = 0

        for group in reversed(groups):
            group_lines = len(group)
            separator_chars = 1 if selected_reversed else 0
            group_chars = _group_char_count(group)

            if used_lines + group_lines > self._max_chatbox_lines:
                break
            if group_chars + separator_chars > (self._max_chatbox_chars - used_chars):
                if not selected_reversed:
                    selected_reversed.append(group)
                break

            selected_reversed.append(group)
            used_lines += group_lines
            used_chars += group_chars + separator_chars

        return list(reversed(selected_reversed))

    def _source_segments(self) -> list[str]:
        segments = [
            entry.source_text for entry in self._closed_entries if entry.source_text
        ]
        if self._active is not None and self._active.text:
            segments.append(self._active.text)
        return segments

    def _target_segments(self) -> list[str]:
        return [
            entry.target_text for entry in self._closed_entries if entry.target_text
        ]

    def _render_stacked_two_zone_text(
        self,
        *,
        source_text: str,
        target_text: str,
    ) -> tuple[str, str, str]:
        if not source_text and not target_text:
            return "", "", ""

        separator = "\n" * (self._chatbox_layout.separator_blank_lines + 1)
        clipped_source, clipped_target = _fit_stacked_two_zone_char_budget(
            source_text=source_text,
            target_text=target_text,
            separator=separator,
            max_chatbox_chars=self._max_chatbox_chars,
            source_visible_lines=self._chatbox_layout.source_visible_lines,
            target_visible_lines=self._chatbox_layout.target_visible_lines,
        )
        return (
            clipped_source,
            clipped_target,
            f"{clipped_source}{separator}{clipped_target}",
        )


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
        if self._output_mode == "source":
            return ChatboxStateMachine()
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


__all__ = [
    "ChatboxAction",
    "ChatboxOutput",
    "ChatboxRateLimiter",
    "ChatboxSnapshot",
    "ChatboxStateMachine",
    "ChatboxTransport",
    "MAX_CHATBOX_CHARS",
    "MAX_CHATBOX_LINES",
    "TranslatedChatboxStateMachine",
    "merge_chatbox_text",
    "normalize_chatbox_text",
    "render_chatbox_text",
]
