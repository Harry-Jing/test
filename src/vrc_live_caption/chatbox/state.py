"""State machines that turn transcript revisions into chatbox snapshots."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from ..config import TranslationChatboxLayoutConfig
from ..stt import TranscriptRevisionEvent
from ..translation import TranslationResult
from .layout import (
    allocate_stacked_content_budgets,
    render_zone_text,
)
from .model import MAX_CHATBOX_CHARS, MAX_CHATBOX_LINES, MAX_RECENT_CLOSED_UTTERANCES
from .text import (
    longest_common_prefix,
    merge_chatbox_text,
    normalize_chatbox_text,
    split_sentences,
)


@dataclass(slots=True, frozen=True)
class ChatboxSnapshot:
    """Store the currently rendered chatbox snapshot."""

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
class _ClosedUtterance:
    utterance_id: str
    revision: int
    source_text: str
    target_text: str | None = None
    translation_pending: bool = False


class _ChatboxStateMachineProtocol(Protocol):
    def is_closed(self, utterance_id: str) -> bool:
        """Return whether the utterance has already been finalized or rolled over."""

    def apply_revision(
        self,
        event: TranscriptRevisionEvent,
        *,
        translation_pending: bool = False,
    ) -> bool:
        """Apply one transcript revision and report whether the snapshot changed."""

    def apply_translation_result(self, result: TranslationResult) -> bool:
        """Apply one completed translation result."""

    def mark_translation_failed(self, utterance_id: str, revision: int) -> bool:
        """Mark one translation attempt as failed or stale."""

    def snapshot(self) -> ChatboxSnapshot:
        """Return the current rendered snapshot."""


class TranslatedChatboxStateMachine:
    """Render source, target, and stacked source-target chatbox snapshots."""

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
        self._closed_entries: deque[_ClosedUtterance] = deque()
        self._closed_by_utterance_id: dict[str, _ClosedUtterance] = {}
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
        """Apply one transcript revision and report whether the snapshot changed."""
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
        if (
            entry is None
            or entry.revision != result.revision
            or not entry.translation_pending
        ):
            return False

        translated_text = normalize_chatbox_text(result.translated_text)
        entry.translation_pending = False
        if not translated_text:
            return False
        entry.target_text = translated_text
        return self._output_mode in {"target", "source_target"}

    def mark_translation_failed(self, utterance_id: str, revision: int) -> bool:
        """Mark one translation attempt as failed without changing visible text."""
        entry = self._closed_by_utterance_id.get(utterance_id)
        if entry is None or entry.revision != revision or not entry.translation_pending:
            return False
        entry.translation_pending = False
        return False

    def snapshot(self) -> ChatboxSnapshot:
        """Build the current translated chatbox snapshot."""
        if self._output_mode == "source_target":
            return self._snapshot_source_target()
        return self._snapshot_single_zone()

    def _snapshot_source_target(self) -> ChatboxSnapshot:
        layout = self._chatbox_layout
        source_fragments = self._source_fragments()
        target_fragments = self._target_fragments()
        source_text = render_zone_text(
            source_fragments,
            max_lines=layout.source_visible_lines,
        )
        target_text = render_zone_text(
            target_fragments,
            max_lines=layout.target_visible_lines,
        )
        separator = "\n" * (layout.separator_blank_lines + 1)
        source_budget, target_budget = allocate_stacked_content_budgets(
            source_text=source_text,
            target_text=target_text,
            separator=separator,
            max_chars=self._max_chatbox_chars,
            source_visible_lines=layout.source_visible_lines,
            target_visible_lines=layout.target_visible_lines,
        )
        source_text = render_zone_text(
            source_fragments,
            max_lines=layout.source_visible_lines,
            max_chars=source_budget,
        )
        target_text = render_zone_text(
            target_fragments,
            max_lines=layout.target_visible_lines,
            max_chars=target_budget,
        )
        rendered = ""
        if source_text or target_text:
            rendered = f"{source_text}{separator}{target_text}"
        return ChatboxSnapshot(
            upper_text=source_text,
            lower_text=target_text,
            text=rendered,
            has_active_utterance=self._active is not None,
        )

    def _snapshot_single_zone(self) -> ChatboxSnapshot:
        rendered = render_zone_text(
            self._single_zone_fragments(),
            max_lines=self._chatbox_layout.source_visible_lines,
            max_chars=self._max_chatbox_chars,
        )
        return ChatboxSnapshot(
            upper_text=rendered,
            lower_text="",
            text=rendered,
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
        entry = _ClosedUtterance(
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

    def _source_fragments(self) -> list[str]:
        fragments = list(split_sentences(self._merged_closed_history_text("source")))
        if self._active is not None and self._active.text:
            fragments.append(self._active.text)
        return fragments

    def _target_fragments(self) -> list[str]:
        return list(split_sentences(self._merged_closed_history_text("target")))

    def _single_zone_fragments(self) -> list[str]:
        history_key = "source" if self._output_mode == "source" else "visible"
        fragments = list(split_sentences(self._merged_closed_history_text(history_key)))
        if self._active is not None and self._active.text:
            fragments.append(self._active.text)
        return fragments

    def _merged_closed_history_text(self, mode: str) -> str:
        history = ""
        for entry in self._closed_entries:
            if mode == "source":
                fragment = entry.source_text
            elif mode == "target":
                fragment = entry.target_text or ""
            else:
                fragment = entry.target_text or entry.source_text
            if fragment:
                history = merge_chatbox_text(history, fragment)
        return history


class ChatboxStateMachine(TranslatedChatboxStateMachine):
    """Source-only state machine kept as the chatbox entrypoint export."""

    def __init__(
        self,
        *,
        chatbox_layout: TranslationChatboxLayoutConfig | None = None,
        max_closed_utterances: int = MAX_RECENT_CLOSED_UTTERANCES,
        max_chatbox_chars: int = MAX_CHATBOX_CHARS,
        max_chatbox_lines: int = MAX_CHATBOX_LINES,
        max_committed_history_chars: int | None = None,
    ) -> None:
        del max_committed_history_chars
        super().__init__(
            output_mode="source",
            chatbox_layout=chatbox_layout,
            max_closed_utterances=max_closed_utterances,
            max_chatbox_chars=max_chatbox_chars,
            max_chatbox_lines=max_chatbox_lines,
        )
