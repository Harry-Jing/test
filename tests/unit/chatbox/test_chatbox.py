import asyncio
import logging

from vrc_live_caption.chatbox import (
    ChatboxOutput,
    ChatboxRateLimiter,
    ChatboxStateMachine,
    render_chatbox_text,
)
from vrc_live_caption.stt import TranscriptRevisionEvent


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
