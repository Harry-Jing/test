"""Normalize, merge, and segment chatbox text."""

from functools import lru_cache

from .model import (
    ASCII_SENTENCE_SEPARATORS,
    CLOSING_PUNCTUATION,
    PRIMARY_SENTENCE_TERMINATORS,
)

_CONTROL_WHITESPACE_TRANSLATION = str.maketrans(
    {
        "\r": " ",
        "\n": " ",
        "\t": " ",
    }
)


def normalize_chatbox_text(text: str) -> str:
    """Normalize control whitespace so chatbox text stays chatbox-safe."""
    return text.translate(_CONTROL_WHITESPACE_TRANSLATION).strip()


def longest_common_prefix(left: str, right: str) -> str:
    """Return the shared prefix between two transcript revisions."""
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return left[:index]


def merge_chatbox_text(base: str, fragment: str) -> str:
    """Merge adjacent text while avoiding duplicated overlap."""
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


@lru_cache(maxsize=4096)
def split_sentences(text: str) -> tuple[str, ...]:
    """Split normalized finalized history into sentence-like fragments."""
    normalized = normalize_chatbox_text(text)
    if not normalized:
        return ()

    fragments: list[str] = []
    start = 0
    index = 0
    while index < len(normalized):
        if normalized[index] not in PRIMARY_SENTENCE_TERMINATORS:
            index += 1
            continue

        end = index + 1
        while end < len(normalized) and normalized[end] in CLOSING_PUNCTUATION:
            end += 1
        fragment = normalized[start:end].strip()
        if fragment:
            fragments.append(fragment)
        start = end
        index = end

    tail = normalized[start:].strip()
    if tail:
        fragments.append(tail)
    return tuple(fragments)


def join_display_fragments(fragments: list[str] | tuple[str, ...]) -> str:
    """Join display fragments using VRChat-friendly separator rules."""
    rendered = ""
    for fragment in fragments:
        piece = normalize_chatbox_text(fragment)
        if not piece:
            continue
        if not rendered:
            rendered = piece
            continue
        if _needs_display_separator(rendered, piece):
            rendered = f"{rendered} {piece}"
        else:
            rendered = f"{rendered}{piece}"
    return rendered.strip()


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


def _needs_display_separator(base: str, fragment: str) -> bool:
    left = base[-1]
    right = fragment[0]
    if left.isspace() or right.isspace():
        return False
    if not (left.isascii() and right.isascii() and right.isalnum()):
        return False
    if left in CLOSING_PUNCTUATION and len(base) >= 2:
        left = base[-2]
    return left.isalnum() or left in ASCII_SENTENCE_SEPARATORS
