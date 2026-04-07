"""Load bundled fonts and expose cached shaping helpers."""

from __future__ import annotations

import io
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

import uharfbuzz as hb
from fontTools.ttLib import TTFont

from .model import (
    CJK_PRIMARY_FONT_NAME,
    EMOJI_FONT_NAME,
    FONT_RESOURCE_SPECS,
    FONT_SIZE_PX,
    PRIMARY_FONT_NAME,
)

_HB_FACE = getattr(hb, "Face")
_HB_FONT = getattr(hb, "Font")
_HB_BUFFER = getattr(hb, "Buffer")
_HB_CLUSTER_LEVEL = getattr(hb, "BufferClusterLevel")
_HB_SHAPE = getattr(hb, "shape")
_HB_OT_FONT_SET_FUNCS = getattr(hb, "ot_font_set_funcs")


@dataclass(frozen=True, slots=True)
class LoadedFont:
    """Store one loaded raw font plus the caches needed for shaping."""

    name: str
    file_name: str
    upem: int
    scale_to_px: float
    cmap: frozenset[int]
    hb_font: Any

    def supports_text(self, text: str) -> bool:
        """Return whether this font can reasonably shape the given text."""
        for char in text:
            if _is_font_agnostic(char):
                continue
            if ord(char) not in self.cmap:
                return False
        return True


class FontRepository:
    """Resolve bundled fonts and pick the best font for each cluster."""

    def __init__(self, fonts: dict[str, LoadedFont], order: tuple[str, ...]) -> None:
        self._fonts = fonts
        self._order = order

    @classmethod
    def build(cls) -> FontRepository:
        """Build the repository from bundled font resources."""
        root = files("vrc_live_caption.chatbox") / "fonts"
        loaded: dict[str, LoadedFont] = {}
        order: list[str] = []
        missing_required: list[str] = []

        for spec in FONT_RESOURCE_SPECS:
            resource = root / spec.file_name
            if not resource.is_file():
                if spec.required:
                    missing_required.append(spec.file_name)
                continue
            loaded[spec.name] = _load_font(
                spec.name, spec.file_name, resource.read_bytes()
            )
            order.append(spec.name)

        if missing_required:
            joined = ", ".join(sorted(missing_required))
            raise RuntimeError(f"Missing required chatbox fonts: {joined}")

        return cls(fonts=loaded, order=tuple(order))

    def font(self, name: str) -> LoadedFont:
        """Return one loaded font by name."""
        return self._fonts[name]

    def resolve_font_name(self, cluster: str) -> str:
        """Resolve the best font for one grapheme cluster."""
        if not cluster or cluster.isspace():
            return PRIMARY_FONT_NAME

        if _looks_like_emoji(cluster):
            font_name = self._first_supporting(cluster, (EMOJI_FONT_NAME,))
            if font_name is not None:
                return font_name

        if _looks_like_cjk(cluster):
            font_name = self._first_supporting(cluster, (CJK_PRIMARY_FONT_NAME,))
            if font_name is not None:
                return font_name

        font_name = self._first_supporting(cluster, (PRIMARY_FONT_NAME,))
        if font_name is not None:
            return font_name

        font_name = self._first_supporting(cluster, self._order)
        if font_name is not None:
            return font_name
        return PRIMARY_FONT_NAME

    def _first_supporting(
        self, cluster: str, candidates: tuple[str, ...]
    ) -> str | None:
        seen: set[str] = set()
        for name in candidates:
            if name in seen or name not in self._fonts:
                continue
            seen.add(name)
            if self._fonts[name].supports_text(cluster):
                return name
        for name in self._order:
            if name in seen:
                continue
            if self._fonts[name].supports_text(cluster):
                return name
        return None


@lru_cache(maxsize=1)
def get_font_repository() -> FontRepository:
    """Return the process-global bundled font repository."""
    return FontRepository.build()


@lru_cache(maxsize=16384)
def resolve_font_name_for_cluster(cluster: str) -> str:
    """Return the resolved font name for one cluster."""
    return get_font_repository().resolve_font_name(cluster)


@lru_cache(maxsize=16384)
def shape_cluster_run(font_name: str, clusters: tuple[str, ...]) -> tuple[float, ...]:
    """Shape one same-font cluster run and return per-cluster advances."""
    if not clusters:
        return ()

    font = get_font_repository().font(font_name)
    run_text = "".join(clusters)
    buffer = _HB_BUFFER()
    buffer.add_str(run_text)
    buffer.cluster_level = _HB_CLUSTER_LEVEL.MONOTONE_CHARACTERS
    buffer.guess_segment_properties()
    _HB_SHAPE(font.hb_font, buffer)

    offsets: list[int] = []
    offset = 0
    for cluster in clusters:
        offsets.append(offset)
        offset += len(cluster)

    advances = [0.0 for _ in clusters]
    for info, position in zip(buffer.glyph_infos, buffer.glyph_positions, strict=True):
        cluster_index = max(0, bisect_right(offsets, info.cluster) - 1)
        advances[cluster_index] += position.x_advance * font.scale_to_px
    return tuple(advances)


@lru_cache(maxsize=16384)
def measure_text(text: str, font_name: str) -> float:
    """Measure one text fragment with a fixed font."""
    return sum(shape_cluster_run(font_name, (text,)))


def split_cluster_prefix_to_width(
    cluster: str, font_name: str, max_width_px: float
) -> tuple[str, str]:
    """Split an oversized cluster by codepoint as a last resort."""
    if not cluster:
        return "", ""
    if measure_text(cluster, font_name) <= max_width_px:
        return cluster, ""

    best_index = 1
    for index in range(1, len(cluster) + 1):
        prefix = cluster[:index]
        if measure_text(prefix, font_name) <= max_width_px:
            best_index = index
            continue
        break
    return cluster[:best_index], cluster[best_index:]


def _load_font(name: str, file_name: str, data: bytes) -> LoadedFont:
    font_file = TTFont(io.BytesIO(data), lazy=True)
    cmap = frozenset((font_file.getBestCmap() or {}).keys())
    font_file.close()

    face = _HB_FACE(data)
    hb_font = _HB_FONT(face)
    _HB_OT_FONT_SET_FUNCS(hb_font)
    hb_font.scale = (face.upem, face.upem)
    return LoadedFont(
        name=name,
        file_name=file_name,
        upem=face.upem,
        scale_to_px=FONT_SIZE_PX / face.upem,
        cmap=cmap,
        hb_font=hb_font,
    )


def _is_font_agnostic(char: str) -> bool:
    if char.isspace():
        return True
    category = unicodedata.category(char)
    return category.startswith("M") or category == "Cf"


def _looks_like_emoji(text: str) -> bool:
    return any(ord(char) >= 0x1F000 or char in {"\u200d", "\ufe0f"} for char in text)


def _looks_like_cjk(text: str) -> bool:
    return any(
        not char.isspace() and unicodedata.east_asian_width(char) in {"W", "F"}
        for char in text
    )
