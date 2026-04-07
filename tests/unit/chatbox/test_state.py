from vrc_live_caption.chatbox import ChatboxStateMachine, TranslatedChatboxStateMachine
from vrc_live_caption.config import TranslationChatboxLayoutConfig
from vrc_live_caption.stt import TranscriptRevisionEvent
from vrc_live_caption.translation import TranslationResult

MID = "\u4e2d"
IDEOGRAPHIC_PERIOD = "\u3002"


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
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text="hello world",
            is_final=True,
        ),
        translation_pending=True,
    )
    assert state.snapshot().text == "hello world\n\n"

    state.apply_translation_result(
        TranslationResult(
            utterance_id="utt-1",
            revision=1,
            source_text="hello world",
            translated_text="target world",
        )
    )
    assert state.snapshot().text == "hello world\n\ntarget world"


def test_translated_state_machine_keeps_previous_target_context_until_new_translation_arrives() -> (
    None
):
    state = TranslatedChatboxStateMachine(output_mode="source_target")

    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text="source one.",
            is_final=True,
        ),
        translation_pending=True,
    )
    state.apply_translation_result(
        TranslationResult(
            utterance_id="utt-1",
            revision=1,
            source_text="source one.",
            translated_text="target one.",
        )
    )
    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-2",
            revision=1,
            text="source two.",
            is_final=True,
        ),
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
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text=sentence,
            is_final=True,
        ),
        translation_pending=True,
    )
    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-2",
            revision=1,
            text=sentence,
            is_final=True,
        ),
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
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text="source one.",
            is_final=True,
        ),
        translation_pending=True,
    )
    state.apply_translation_result(
        TranslationResult(
            utterance_id="utt-1",
            revision=1,
            source_text="source one.",
            translated_text="target one.",
        )
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
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text=source_text,
            is_final=True,
        ),
        translation_pending=True,
    )
    state.apply_translation_result(
        TranslationResult(
            utterance_id="utt-1",
            revision=1,
            source_text=source_text,
            translated_text="z",
        )
    )

    snapshot = state.snapshot()
    assert snapshot.upper_text == "defghijklmnopqrst"
    assert snapshot.lower_text == "z"
