"""Simulate VRChat chatbox wrapping and zone clipping."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from uniseg import graphemecluster, linebreak

from .fonts import (
    resolve_font_name_for_cluster,
    shape_cluster_run,
    split_cluster_prefix_to_width,
)
from .model import (
    MAX_CHATBOX_CHARS,
    TMP_FOLLOWING_CHARACTERS,
    TMP_LEADING_CHARACTERS,
    USABLE_WIDTH_PX,
)
from .text import join_display_fragments, normalize_chatbox_text


@dataclass(frozen=True, slots=True)
class LayoutCluster:
    """Describe one measured grapheme cluster."""

    text: str
    start: int
    end: int
    font_name: str
    width_px: float
    break_after: bool


def wrap_text(
    text: str,
    *,
    width_px: float = USABLE_WIDTH_PX,
    stop_after_lines: int | None = None,
) -> tuple[str, ...]:
    """Wrap text with VRChat width rules and optional early stopping."""
    normalized = normalize_chatbox_text(text)
    if not normalized:
        return ()

    clusters = list(_build_clusters(normalized))
    lines: list[str] = []
    line_start = 0
    index = 0
    current_width = 0.0
    last_break_index: int | None = None

    while index < len(clusters):
        cluster = clusters[index]
        if current_width == 0.0 and cluster.width_px > width_px:
            head, tail = split_cluster_prefix_to_width(
                cluster.text, cluster.font_name, width_px
            )
            lines.append(head.rstrip())
            if stop_after_lines is not None and len(lines) >= stop_after_lines:
                return tuple(lines)
            if tail:
                clusters[index : index + 1] = list(_build_clusters(tail))
            else:
                index += 1
            line_start = index
            current_width = 0.0
            last_break_index = None
            continue

        if current_width == 0.0 or current_width + cluster.width_px <= width_px:
            current_width += cluster.width_px
            if cluster.break_after:
                last_break_index = index + 1
            index += 1
            continue

        break_index = (
            last_break_index
            if last_break_index is not None and last_break_index > line_start
            else index
        )
        line_text = _clusters_to_text(clusters[line_start:break_index]).rstrip()
        if line_text:
            lines.append(line_text)
            if stop_after_lines is not None and len(lines) >= stop_after_lines:
                return tuple(lines)

        line_start = break_index
        while line_start < len(clusters) and clusters[line_start].text.isspace():
            line_start += 1
        index = line_start
        current_width = 0.0
        last_break_index = None

    tail_line = _clusters_to_text(clusters[line_start:]).rstrip()
    if tail_line:
        lines.append(tail_line)
    return tuple(lines)


def text_fits(text: str, *, max_lines: int, max_chars: int | None = None) -> bool:
    """Return whether text fits the requested visual and character budget."""
    normalized = normalize_chatbox_text(text)
    if not normalized:
        return True
    if max_chars is not None and len(normalized) > max_chars:
        return False
    return len(wrap_text(normalized, stop_after_lines=max_lines + 1)) <= max_lines


def render_zone_text(
    fragments: list[str] | tuple[str, ...],
    *,
    max_lines: int,
    max_chars: int | None = None,
) -> str:
    """Render the newest tail of one visible zone."""
    if max_lines <= 0:
        return ""
    if max_chars is not None and max_chars <= 0:
        return ""

    selected = select_tail_fragments(
        fragments,
        max_lines=max_lines,
        max_chars=max_chars,
    )
    return join_display_fragments(selected)


def select_tail_fragments(
    fragments: list[str] | tuple[str, ...],
    *,
    max_lines: int,
    max_chars: int | None = None,
) -> list[str]:
    """Select the newest sentence-like fragments that still fit the zone."""
    normalized_fragments = [
        normalized
        for fragment in fragments
        if (normalized := normalize_chatbox_text(fragment))
    ]
    if not normalized_fragments:
        return []

    selected_reversed: list[str] = []
    for fragment in reversed(normalized_fragments):
        suffix_fragments = list(reversed(selected_reversed))
        candidate_text = join_display_fragments([fragment, *suffix_fragments])
        if text_fits(candidate_text, max_lines=max_lines, max_chars=max_chars):
            selected_reversed.append(fragment)
            continue

        clipped = clip_fragment_tail_to_context(
            fragment,
            suffix_fragments=suffix_fragments,
            max_lines=max_lines,
            max_chars=max_chars,
        )
        if clipped:
            selected_reversed.append(clipped)
        break

    return list(reversed(selected_reversed))


def clip_fragment_tail_to_context(
    fragment: str,
    *,
    suffix_fragments: list[str],
    max_lines: int,
    max_chars: int | None,
) -> str:
    """Clip one oldest fragment from the head while preserving its tail."""
    normalized = normalize_chatbox_text(fragment)
    if not normalized:
        return ""

    for offsets in (
        _legal_tail_start_offsets(normalized),
        _cluster_tail_start_offsets(normalized),
        _codepoint_tail_start_offsets(normalized),
    ):
        seen: set[int] = set()
        for start in offsets:
            if start in seen:
                continue
            seen.add(start)
            candidate = normalized[start:].lstrip()
            if not candidate:
                continue
            candidate_text = join_display_fragments([candidate, *suffix_fragments])
            if text_fits(candidate_text, max_lines=max_lines, max_chars=max_chars):
                return candidate
    return ""


def allocate_stacked_content_budgets(
    *,
    source_text: str,
    target_text: str,
    separator: str,
    max_chars: int = MAX_CHATBOX_CHARS,
    source_visible_lines: int,
    target_visible_lines: int,
) -> tuple[int, int]:
    """Allocate the shared 144-char content budget across source and target."""
    separator_chars = len(separator)
    content_budget = max(0, max_chars - separator_chars)
    source_length = len(source_text)
    target_length = len(target_text)
    if source_length + target_length <= content_budget:
        return source_length, target_length

    total_visible_lines = source_visible_lines + target_visible_lines
    source_budget = int(content_budget * (source_visible_lines / total_visible_lines))
    target_budget = content_budget - source_budget

    source_budget = min(source_budget, source_length)
    target_budget = min(target_budget, target_length)
    remaining = content_budget - source_budget - target_budget

    if remaining > 0 and source_length > source_budget:
        extra = min(remaining, source_length - source_budget)
        source_budget += extra
        remaining -= extra
    if remaining > 0 and target_length > target_budget:
        target_budget += min(remaining, target_length - target_budget)
    return source_budget, target_budget


@lru_cache(maxsize=4096)
def _build_clusters(text: str) -> tuple[LayoutCluster, ...]:
    normalized = normalize_chatbox_text(text)
    if not normalized:
        return ()

    cluster_texts = tuple(graphemecluster.grapheme_clusters(normalized))
    font_names = tuple(
        resolve_font_name_for_cluster(cluster) for cluster in cluster_texts
    )

    advances = [0.0 for _ in cluster_texts]
    start = 0
    while start < len(cluster_texts):
        font_name = font_names[start]
        end = start + 1
        while end < len(cluster_texts) and font_names[end] == font_name:
            end += 1
        run_advances = shape_cluster_run(font_name, cluster_texts[start:end])
        advances[start:end] = run_advances
        start = end

    uax_boundaries = frozenset(linebreak.line_break_boundaries(normalized))
    clusters: list[LayoutCluster] = []
    offset = 0
    for index, cluster_text in enumerate(cluster_texts):
        start_offset = offset
        end_offset = start_offset + len(cluster_text)
        clusters.append(
            LayoutCluster(
                text=cluster_text,
                start=start_offset,
                end=end_offset,
                font_name=font_names[index],
                width_px=advances[index],
                break_after=_is_legal_break_after(
                    cluster_index=index,
                    cluster_texts=cluster_texts,
                    boundary_offset=end_offset,
                    uax_boundaries=uax_boundaries,
                ),
            )
        )
        offset = end_offset
    return tuple(clusters)


def _is_legal_break_after(
    *,
    cluster_index: int,
    cluster_texts: tuple[str, ...],
    boundary_offset: int,
    uax_boundaries: frozenset[int],
) -> bool:
    if boundary_offset not in uax_boundaries:
        return False
    if cluster_index >= len(cluster_texts) - 1:
        return True

    left_index = cluster_index
    while left_index >= 0 and cluster_texts[left_index].isspace():
        left_index -= 1
    right_index = cluster_index + 1
    while right_index < len(cluster_texts) and cluster_texts[right_index].isspace():
        right_index += 1

    if left_index >= 0 and cluster_texts[left_index][-1] in TMP_LEADING_CHARACTERS:
        return False
    if right_index < len(cluster_texts) and (
        cluster_texts[right_index][0] in TMP_FOLLOWING_CHARACTERS
    ):
        return False
    return True


def _legal_tail_start_offsets(text: str) -> tuple[int, ...]:
    return tuple(
        cluster.end for cluster in _build_clusters(text)[:-1] if cluster.break_after
    )


def _cluster_tail_start_offsets(text: str) -> tuple[int, ...]:
    return tuple(cluster.start for cluster in _build_clusters(text)[1:])


def _codepoint_tail_start_offsets(text: str) -> tuple[int, ...]:
    return tuple(range(1, len(text)))


def _clusters_to_text(clusters: list[LayoutCluster] | tuple[LayoutCluster, ...]) -> str:
    return "".join(cluster.text for cluster in clusters)
