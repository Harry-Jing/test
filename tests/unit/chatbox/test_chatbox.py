import asyncio
import logging
from typing import cast

from vrc_live_caption.chatbox import (
    ChatboxOutput,
    ChatboxRateLimiter,
    ChatboxStateMachine,
    TranslatedChatboxStateMachine,
    render_chatbox_text,
)
from vrc_live_caption.config import (
    TranslationChatboxLayoutConfig,
    TranslationChatboxLayoutWidthsConfig,
    TranslationConfig,
)
from vrc_live_caption.stt import TranscriptRevisionEvent
from vrc_live_caption.translation import (
    TranslationBackend,
    TranslationRequest,
    TranslationResult,
)


class _FakeClock:
    def __init__(self) -> None:
        self.current = 0.0

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


class _FakeTransport:
    def __init__(self) -> None:
        self.text_messages: list[str] = []
        self.typing_messages: list[bool] = []

    def send_text(self, text: str) -> None:
        self.text_messages.append(text)

    def send_typing(self, is_typing: bool) -> None:
        self.typing_messages.append(is_typing)


class _FakeTranslationBackend:
    def __init__(self) -> None:
        self.requests: list[TranslationRequest] = []
        self.release = asyncio.Event()

    def validate_environment(self) -> None:
        return None

    def describe(self) -> str:
        return "fake"

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self.requests.append(request)
        await self.release.wait()
        return TranslationResult(
            utterance_id=request.utterance_id,
            revision=request.revision,
            source_text=request.text,
            translated_text="你好世界",
        )


def test_state_machine_builds_single_line_layout_and_commits_final() -> None:
    state = ChatboxStateMachine()

    assert (
        state.apply_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=1,
                text="hello",
                is_final=False,
            )
        )
        is True
    )
    assert state.snapshot().text == "hello"

    assert (
        state.apply_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=2,
                text="hello world",
                is_final=False,
            )
        )
        is True
    )
    snapshot = state.snapshot()
    assert snapshot.upper_text == "hello"
    assert snapshot.lower_text == " world"
    assert snapshot.text == "hello world"
    assert snapshot.has_active_utterance is True

    assert (
        state.apply_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=3,
                text="hello world!",
                is_final=True,
            )
        )
        is True
    )
    snapshot = state.snapshot()
    assert snapshot.text == "hello world!"
    assert snapshot.has_active_utterance is False


def test_state_machine_bounds_closed_ids_and_committed_history() -> None:
    state = ChatboxStateMachine(max_closed_utterances=2, max_committed_history_chars=10)

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
    assert len(state.snapshot().upper_text) <= 10


def test_render_chatbox_text_merges_segments_into_single_line() -> None:
    rendered = render_chatbox_text(upper="hello", lower=" world")

    assert rendered == "hello world"


def test_render_chatbox_text_trims_merged_line_to_144_chars() -> None:
    rendered = render_chatbox_text(upper="a" * 100, lower="b" * 100)

    assert rendered == ("a" * 43) + " " + ("b" * 100)


def test_rate_limiter_replaces_pending_partial_with_final_and_sends_typing_after_text() -> (
    None
):
    clock = _FakeClock()
    limiter = ChatboxRateLimiter(now=clock.now)

    limiter.queue_text("partial-1", is_final=False)
    limiter.request_typing(True)
    first_action = limiter.tick()
    assert first_action is not None
    assert first_action.kind == "text"
    assert first_action.text == "partial-1"
    assert first_action.is_final is False

    limiter.queue_text("partial-2", is_final=False)
    limiter.queue_text("final-2", is_final=True)
    assert limiter.tick() is None

    clock.advance(0.3)
    second_action = limiter.tick()
    assert second_action is not None
    assert second_action.kind == "text"
    assert second_action.text == "final-2"
    assert second_action.is_final is True

    assert limiter.tick() is None

    clock.advance(0.3)
    typing_action = limiter.tick()
    assert typing_action is not None
    assert typing_action.kind == "typing"
    assert typing_action.typing is True


def test_chatbox_output_async_worker_sends_text_and_typing_edges() -> None:
    async def scenario() -> None:
        transport = _FakeTransport()
        emitted_lines: list[str] = []
        output = ChatboxOutput(
            transport=transport,
            emit_line=emitted_lines.append,
            logger=logging.getLogger("test.chatbox.output"),
            typing_idle_timeout_seconds=0.2,
            rate_limiter=ChatboxRateLimiter(
                partial_min_interval_seconds=0.01,
                final_send_guard_seconds=0.01,
            ),
        )

        await output.start()
        output.handle_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=1,
                text="hello",
                is_final=False,
            )
        )
        await asyncio.sleep(0)

        assert transport.text_messages == ["hello"]
        assert emitted_lines == ["[chatbox] hello"]

        await asyncio.sleep(0.02)
        assert transport.typing_messages[0] is True

        output.handle_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=2,
                text="hello world",
                is_final=True,
            )
        )
        await asyncio.sleep(0.05)
        assert False in transport.typing_messages

        await output.shutdown(timeout_seconds=1.0)

    asyncio.run(scenario())


def test_chatbox_output_shutdown_flushes_pending_snapshot() -> None:
    async def scenario() -> None:
        transport = _FakeTransport()
        emitted_lines: list[str] = []
        output = ChatboxOutput(
            transport=transport,
            emit_line=emitted_lines.append,
            logger=logging.getLogger("test.chatbox.shutdown"),
            rate_limiter=ChatboxRateLimiter(
                partial_min_interval_seconds=0.05,
                final_send_guard_seconds=0.01,
            ),
        )

        await output.start()
        output.handle_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=1,
                text="hello",
                is_final=False,
            )
        )
        output.handle_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=2,
                text="hello world",
                is_final=False,
            )
        )
        await asyncio.sleep(0.01)

        await output.shutdown(timeout_seconds=2.0)

        assert transport.text_messages[-1] == "hello world"
        assert transport.typing_messages[-1] is False
        assert emitted_lines[-1] == "[chatbox] hello world"

    asyncio.run(scenario())


def test_translated_state_machine_keeps_source_visible_until_translation_arrives() -> (
    None
):
    state = TranslatedChatboxStateMachine(output_mode="source_target")

    assert (
        state.apply_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=1,
                text="hello world",
                is_final=True,
            ),
            translation_pending=True,
        )
        is True
    )
    assert state.snapshot().text == "hello world\n\n"

    assert (
        state.apply_translation_result(
            TranslationResult(
                utterance_id="utt-1",
                revision=1,
                source_text="hello world",
                translated_text="你好世界",
            )
        )
        is True
    )
    assert state.snapshot().text == "hello world\n\n你好世界"


def test_translated_state_machine_keeps_previous_target_context_until_new_translation_arrives() -> (
    None
):
    state = TranslatedChatboxStateMachine(output_mode="source_target")

    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text="hello one",
            is_final=True,
        ),
        translation_pending=True,
    )
    state.apply_translation_result(
        TranslationResult(
            utterance_id="utt-1",
            revision=1,
            source_text="hello one",
            translated_text="first target",
        )
    )
    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-2",
            revision=1,
            text="hello two",
            is_final=True,
        ),
        translation_pending=True,
    )

    assert state.snapshot().text == "hello one hello two\n\nfirst target"


def test_translated_state_machine_source_target_clips_each_paragraph_by_visual_budget() -> (
    None
):
    state = TranslatedChatboxStateMachine(
        output_mode="source_target",
        chatbox_layout=TranslationChatboxLayoutConfig(
            source_visible_lines=1,
            separator_blank_lines=1,
            target_visible_lines=1,
            visual_line_width_units=5.0,
            widths=TranslationChatboxLayoutWidthsConfig(
                cjk=1.0,
                ascii_upper=1.0,
                ascii_lower=1.0,
                ascii_narrow=1.0,
                fallback=1.0,
            ),
        ),
    )

    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-1",
            revision=1,
            text="hello",
            is_final=True,
        ),
        translation_pending=True,
    )
    state.apply_translation_result(
        TranslationResult(
            utterance_id="utt-1",
            revision=1,
            source_text="hello",
            translated_text="earth",
        )
    )
    state.apply_revision(
        TranscriptRevisionEvent(
            utterance_id="utt-2",
            revision=1,
            text="world",
            is_final=True,
        ),
        translation_pending=True,
    )
    state.apply_translation_result(
        TranslationResult(
            utterance_id="utt-2",
            revision=1,
            source_text="world",
            translated_text="peace",
        )
    )

    snapshot = state.snapshot()
    assert snapshot.upper_text == "world"
    assert snapshot.lower_text == "peace"
    assert snapshot.text == "world\n\npeace"


def test_chatbox_output_sends_source_then_bilingual_final_when_translation_completes() -> (
    None
):
    async def scenario() -> None:
        transport = _FakeTransport()
        backend = _FakeTranslationBackend()
        emitted_lines: list[str] = []
        output = ChatboxOutput(
            transport=transport,
            emit_line=emitted_lines.append,
            logger=logging.getLogger("test.chatbox.translation"),
            translation_config=TranslationConfig(
                enabled=True,
                target_language="zh",
                output_mode="source_target",
            ),
            translation_backend=cast(TranslationBackend, backend),
            rate_limiter=ChatboxRateLimiter(
                partial_min_interval_seconds=0.01,
                final_send_guard_seconds=0.01,
            ),
        )

        await output.start()
        output.handle_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=1,
                text="hello",
                is_final=False,
            )
        )
        await asyncio.sleep(0.02)
        assert transport.text_messages == ["hello\n\n"]

        output.handle_revision(
            TranscriptRevisionEvent(
                utterance_id="utt-1",
                revision=2,
                text="hello world",
                is_final=True,
            )
        )
        await asyncio.sleep(0.02)
        assert transport.text_messages[-1] == "hello world\n\n"

        backend.release.set()
        await asyncio.sleep(0.05)
        await output.shutdown(timeout_seconds=1.0)

        assert backend.requests[0].text == "hello world"
        assert transport.text_messages[-1] == "hello world\n\n你好世界"
        assert emitted_lines[-1] == "[chatbox] hello world\n\n你好世界"

    asyncio.run(scenario())
