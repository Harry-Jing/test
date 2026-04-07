from vrc_live_caption.chatbox.layout import (
    render_zone_text,
    select_tail_fragments_with_suffix,
    wrap_text,
    wrapped_line_count,
)

MID = "\u4e2d"
FULLWIDTH_COMMA = "\uff0c"
IDEOGRAPHIC_PERIOD = "\u3002"
FULLWIDTH_OPEN_PAREN = "\uff08"


def test_wrap_text_uses_report_anchor_for_x() -> None:
    assert wrap_text("x" * 29) == ("x" * 29,)
    assert wrap_text("x" * 30) == ("x" * 29, "x")


def test_wrap_text_uses_report_anchor_for_cjk() -> None:
    assert wrap_text(MID * 15) == (MID * 15,)
    assert wrap_text(MID * 16) == (MID * 15, MID)


def test_wrap_text_uses_report_anchor_for_ascii_punctuation() -> None:
    assert wrap_text("." * 58) == ("." * 58,)


def test_wrap_text_uses_cjk_width_for_fullwidth_punctuation() -> None:
    assert wrap_text((MID + FULLWIDTH_COMMA) * 7) == ((MID + FULLWIDTH_COMMA) * 7,)
    assert wrap_text((MID + FULLWIDTH_COMMA) * 8) == (
        (MID + FULLWIDTH_COMMA) * 7,
        MID + FULLWIDTH_COMMA,
    )
    assert wrap_text((MID + IDEOGRAPHIC_PERIOD) * 7) == (
        (MID + IDEOGRAPHIC_PERIOD) * 7,
    )
    assert wrap_text((MID + IDEOGRAPHIC_PERIOD) * 8) == (
        (MID + IDEOGRAPHIC_PERIOD) * 7,
        MID + IDEOGRAPHIC_PERIOD,
    )


def test_wrap_text_drops_space_from_new_line_after_break() -> None:
    lines = wrap_text(("x" * 20) + " " + ("x" * 20))

    assert lines == ("x" * 20, "x" * 20)
    assert not lines[1].startswith(" ")


def test_wrap_text_applies_tmp_leading_and_following_rules() -> None:
    leading_lines = wrap_text((MID * 14) + FULLWIDTH_OPEN_PAREN + MID)
    following_lines = wrap_text((MID * 15) + FULLWIDTH_COMMA)

    assert not leading_lines[0].endswith(FULLWIDTH_OPEN_PAREN)
    assert not following_lines[1].startswith(FULLWIDTH_COMMA)


def test_render_zone_text_drops_oldest_whole_sentence_first() -> None:
    first = (MID * 14) + IDEOGRAPHIC_PERIOD
    second = ("x" * 28) + "."

    assert render_zone_text([first, second], max_lines=1) == second


def test_render_zone_text_keeps_tail_of_oversize_single_sentence() -> None:
    assert render_zone_text([MID * 20], max_lines=1) == (MID * 15)


def test_render_zone_text_never_inserts_manual_newlines_inside_zone() -> None:
    rendered = render_zone_text(["hello world.", "next sentence."], max_lines=2)

    assert "\n" not in rendered


def test_wrapped_line_count_matches_visual_wrap() -> None:
    assert wrapped_line_count("") == 0
    assert wrapped_line_count("x" * 29) == 1
    assert wrapped_line_count("x" * 30) == 2


def test_select_tail_fragments_with_suffix_clips_only_candidate_portion() -> None:
    selected = select_tail_fragments_with_suffix(
        ["x" * 20],
        suffix_fragments=["x" * 10],
        max_lines=1,
    )

    assert selected == ["x" * 18]
