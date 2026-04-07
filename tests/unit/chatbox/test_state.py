from vrc_live_caption.chatbox import ChatboxStateMachine, TranslatedChatboxStateMachine
from vrc_live_caption.config import TranslationChatboxLayoutConfig
from vrc_live_caption.stt import TranscriptRevisionEvent
from vrc_live_caption.translation import TranslationResult

MID = "\u4e2d"
IDEOGRAPHIC_PERIOD = "\u3002"


def _final_event(
    utterance_id: str, text: str, *, revision: int = 1
) -> TranscriptRevisionEvent:
    return TranscriptRevisionEvent(
        utterance_id=utterance_id,
        revision=revision,
        text=text,
        is_final=True,
    )


def _translation_result(
    utterance_id: str,
    source_text: str,
    translated_text: str,
    *,
    revision: int = 1,
) -> TranslationResult:
    return TranslationResult(
        utterance_id=utterance_id,
        revision=revision,
        source_text=source_text,
        translated_text=translated_text,
    )


def test_state_machine_rollover_commits_only_stable_prefix() -> None:
    state = ChatboxStateMachine()

    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text="hello world",
            is_final=False,
        )
    )
    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=2,
            text="hello there",
            is_final=False,
        )
    )
    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-2",
            revision=1,
            text="next",
            is_final=False,
        )
    )

    snapshot = state.snapshot()
    assert snapshot.text == "hello next"
    assert snapshot.has_active_utterance is True


def test_state_machine_final_commits_full_text_and_dedupes_duplicate_final() -> None:
    state = ChatboxStateMachine()

    assert state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text="hello",
            is_final=False,
        )
    )
    assert state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=2,
            text="hello world!",
            is_final=True,
        )
    )

    snapshot = state.snapshot()
    assert snapshot.text == "hello world!"
    assert snapshot.has_active_utterance is False

    assert (
        state.apply_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=3,
                text="should be ignored",
                is_final=True,
            )
        )
        is False
    )
    assert state.snapshot().text == "hello world!"


def test_state_machine_bounds_closed_ids() -> None:
    state = ChatboxStateMachine(max_closed_utterances=2)

    for index in range(3):
        state.apply_revision(
            TranscriptRevisionEvent(
                utterance_id=f"utt-{index}",
                revision=1,
                text=f"word{index}",
                is_final=True,
            )
        )

    assert state.is_closed("utt-0") is False
    assert state.is_closed("utt-1") is True
    assert state.is_closed("utt-2") is True


def test_translated_state_machine_keeps_source_visible_until_translation_arrives() -> (
    None
):
    state = TranslatedChatboxStateMachine(output_mode="source_target")

    state.apply_revision(
        _final_event("utt-1", "hello world"),
        translation_pending=True,
    )
    assert state.snapshot().text == "hello world\n\n"

    state.apply_translation_result(
        _translation_result("utt-1", "hello world", "target world")
    )
    assert state.snapshot().text == "hello world\n\ntarget world"


def test_translated_state_machine_keeps_previous_target_context_until_new_translation_arrives() -> (
    None
):
    state = TranslatedChatboxStateMachine(output_mode="source_target")

    state.apply_revision(
        _final_event("utt-1", "source one."),
        translation_pending=True,
    )
    state.apply_translation_result(
        _translation_result("utt-1", "source one.", "target one.")
    )
    state.apply_revision(
        _final_event("utt-2", "source two."),
        translation_pending=True,
    )

    snapshot = state.snapshot()
    assert snapshot.upper_text == "source one. source two."
    assert snapshot.lower_text == "target one."


def test_translated_state_machine_source_target_pending_stays_within_source_zone() -> (
    None
):
    state = TranslatedChatboxStateMachine(
        output_mode="source_target",
        chatbox_layout=TranslationChatboxLayoutConfig(
            source_visible_lines=1,
            separator_blank_lines=1,
            target_visible_lines=1,
        ),
    )
    sentence = (MID * 14) + IDEOGRAPHIC_PERIOD

    state.apply_revision(
        _final_event("utt-1", sentence),
        translation_pending=True,
    )
    state.apply_revision(
        _final_event("utt-2", sentence),
        translation_pending=True,
    )

    snapshot = state.snapshot()
    assert snapshot.upper_text == sentence
    assert snapshot.lower_text == ""
    assert snapshot.text == f"{sentence}\n\n"


def test_translated_state_machine_target_mode_uses_single_top_zone() -> None:
    state = TranslatedChatboxStateMachine(
        output_mode="target",
        chatbox_layout=TranslationChatboxLayoutConfig(source_visible_lines=4),
    )

    state.apply_revision(
        _final_event("utt-1", "source one."),
        translation_pending=True,
    )
    state.apply_translation_result(
        _translation_result("utt-1", "source one.", "target one.")
    )
    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-2",
            revision=1,
            text="live source",
            is_final=False,
        )
    )

    snapshot = state.snapshot()
    assert "\n" not in snapshot.text
    assert snapshot.text == "target one. live source"


def test_translated_state_machine_source_target_char_budget_can_borrow() -> None:
    state = TranslatedChatboxStateMachine(
        output_mode="source_target",
        chatbox_layout=TranslationChatboxLayoutConfig(
            source_visible_lines=1,
            separator_blank_lines=1,
            target_visible_lines=1,
        ),
        max_chatbox_chars=20,
    )
    source_text = "abcdefghijklmnopqrst"

    state.apply_revision(
        _final_event("utt-1", source_text),
        translation_pending=True,
    )
    state.apply_translation_result(_translation_result("utt-1", source_text, "z"))

    snapshot = state.snapshot()
    assert snapshot.upper_text == "defghijklmnopqrst"
    assert snapshot.lower_text == "z"


def test_translated_state_machine_source_target_keeps_same_pair_range() -> None:
    state = TranslatedChatboxStateMachine(
        output_mode="source_target",
        chatbox_layout=TranslationChatboxLayoutConfig(
            source_visible_lines=1,
            separator_blank_lines=1,
            target_visible_lines=1,
        ),
    )

    state.apply_revision(_final_event("utt-1", "甲。"), translation_pending=True)
    state.apply_translation_result(_translation_result("utt-1", "甲。", "older target"))
    state.apply_revision(_final_event("utt-2", "乙。"), translation_pending=True)
    state.apply_translation_result(_translation_result("utt-2", "乙。", "x" * 29))

    snapshot = state.snapshot()
    assert snapshot.upper_text == "乙。"
    assert snapshot.lower_text == "x" * 29


def test_translated_state_machine_source_target_allows_asymmetric_clip_inside_pair() -> (
    None
):
    state = TranslatedChatboxStateMachine(
        output_mode="source_target",
        chatbox_layout=TranslationChatboxLayoutConfig(
            source_visible_lines=1,
            separator_blank_lines=1,
            target_visible_lines=1,
        ),
    )

    state.apply_revision(_final_event("utt-1", "旧。"), translation_pending=True)
    state.apply_translation_result(_translation_result("utt-1", "旧。", "old."))
    state.apply_revision(_final_event("utt-2", "新。"), translation_pending=True)
    state.apply_translation_result(_translation_result("utt-2", "新。", "x" * 35))

    snapshot = state.snapshot()
    assert snapshot.upper_text == "新。"
    assert snapshot.lower_text == "x" * 29


def test_translated_state_machine_source_target_failure_keeps_source_tail_visible() -> (
    None
):
    state = TranslatedChatboxStateMachine(output_mode="source_target")

    state.apply_revision(_final_event("utt-1", "source one."), translation_pending=True)
    state.apply_translation_result(
        _translation_result("utt-1", "source one.", "target one.")
    )
    state.apply_revision(_final_event("utt-2", "source two."), translation_pending=True)

    before_failure = state.snapshot()
    assert before_failure.upper_text == "source one. source two."
    assert before_failure.lower_text == "target one."

    assert state.mark_translation_failed("utt-2", 1) is False

    after_failure = state.snapshot()
    assert after_failure.upper_text == "source one. source two."
    assert after_failure.lower_text == "target one."


def test_translated_state_machine_source_only_tail_has_char_priority_over_pairs() -> (
    None
):
    state = TranslatedChatboxStateMachine(
        output_mode="source_target",
        chatbox_layout=TranslationChatboxLayoutConfig(
            source_visible_lines=2,
            separator_blank_lines=1,
            target_visible_lines=1,
        ),
        max_chatbox_chars=20,
    )

    state.apply_revision(
        _final_event("utt-1", "older source"), translation_pending=True
    )
    state.apply_translation_result(
        _translation_result("utt-1", "older source", "older target")
    )
    state.apply_revision(_final_event("utt-2", "." * 30), translation_pending=True)

    snapshot = state.snapshot()
    assert snapshot.upper_text == "." * 18
    assert snapshot.lower_text == ""
