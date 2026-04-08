import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tests.support.harnesses.audio import iter_wav_chunks
from vrc_live_caption.config import (
    AppConfig,
    CaptureConfig,
    IflytekRtasrProviderConfig,
    OpenAIRealtimeProviderConfig,
    PipelineConfig,
    SttConfig,
    SttProvidersConfig,
    SttRetryConfig,
)
from vrc_live_caption.env import AppSecrets
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt import (
    AsyncSttSessionRunner,
    SttEvent,
    SttStatus,
    SttStatusEvent,
    TranscriptRevisionEvent,
    create_stt_backend,
)

_EVENT_POLL_TIMEOUT_SECONDS = 0.1
_TEST_WAV_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "audio" / "test.wav"

DEFAULT_LIVE_KEYWORD_GROUPS = (
    ("测试音频", "测试"),
    ("十秒", "10秒", "时长"),
)


@dataclass(slots=True, frozen=True)
class LiveSttHarnessResult:
    events: list[SttEvent]
    statuses: list[SttStatusEvent]
    finals: list[str]
    aggregated_text: str
    keyword_groups_matched: bool
    diagnostics: str
    start_error: str | None = None
    replay_error: str | None = None
    close_error: str | None = None

    @property
    def ready_seen(self) -> bool:
        return any(event.status == SttStatus.READY for event in self.statuses)

    @property
    def error_seen(self) -> bool:
        return any(event.status == SttStatus.ERROR for event in self.statuses)

    @property
    def closing_seen(self) -> bool:
        return any(event.status == SttStatus.CLOSING for event in self.statuses)

    @property
    def closed_seen(self) -> bool:
        return any(event.status == SttStatus.CLOSED for event in self.statuses)


def build_live_app_config(
    *,
    provider: Literal["openai_realtime", "iflytek_rtasr"],
    openai_realtime_overrides: dict[str, object] | None = None,
    iflytek_rtasr_overrides: dict[str, object] | None = None,
) -> AppConfig:
    providers = SttProvidersConfig(
        openai_realtime=OpenAIRealtimeProviderConfig().model_copy(
            update=openai_realtime_overrides or {}
        ),
        iflytek_rtasr=IflytekRtasrProviderConfig().model_copy(
            update=iflytek_rtasr_overrides or {}
        ),
    )
    return AppConfig(
        capture=CaptureConfig(
            sample_rate=16_000,
            channels=1,
            dtype="int16",
            block_duration_ms=100,
        ),
        pipeline=PipelineConfig(
            audio_buffer_max_chunks=200,
            event_buffer_max_items=400,
            shutdown_timeout_seconds=10.0,
        ),
        stt=SttConfig(
            provider=provider,
            retry=SttRetryConfig(
                connect_timeout_seconds=20.0,
                max_attempts=1,
                initial_backoff_seconds=0.5,
                max_backoff_seconds=0.5,
            ),
            providers=providers,
        ),
    )


def run_live_stt_fixture(
    *,
    app_config: AppConfig,
    secrets: AppSecrets,
    logger_name: str,
    keyword_groups: tuple[tuple[str, ...], ...] = DEFAULT_LIVE_KEYWORD_GROUPS,
    wav_path: Path = _TEST_WAV_PATH,
) -> LiveSttHarnessResult:
    return asyncio.run(
        _run_live_stt_fixture(
            app_config=app_config,
            secrets=secrets,
            logger_name=logger_name,
            keyword_groups=keyword_groups,
            wav_path=wav_path,
        )
    )


def assert_live_transcription_result(result: LiveSttHarnessResult) -> None:
    assert result.start_error is None, result.diagnostics
    assert result.replay_error is None, result.diagnostics
    assert result.close_error is None, result.diagnostics
    assert result.ready_seen is True, result.diagnostics
    assert result.error_seen is False, result.diagnostics
    assert result.finals, result.diagnostics
    assert result.keyword_groups_matched is True, result.diagnostics
    assert result.closing_seen is True, result.diagnostics
    assert result.closed_seen is True, result.diagnostics


async def _run_live_stt_fixture(
    *,
    app_config: AppConfig,
    secrets: AppSecrets,
    logger_name: str,
    keyword_groups: tuple[tuple[str, ...], ...],
    wav_path: Path,
) -> LiveSttHarnessResult:
    logger = logging.getLogger(logger_name)
    audio_queue = DropOldestAsyncQueue[AudioChunk](
        max_items=app_config.pipeline.audio_buffer_max_chunks,
        logger=logger.getChild("audio"),
        label="live STT audio queue",
    )
    backend = create_stt_backend(
        capture_config=app_config.capture,
        stt_config=app_config.stt,
        secrets=secrets,
        logger=logger.getChild("backend"),
    )
    runner = AsyncSttSessionRunner(
        backend=backend,
        retry_config=app_config.stt.retry,
        audio_queue=audio_queue,
        event_buffer_max_items=app_config.pipeline.event_buffer_max_items,
        logger=logger.getChild("runner"),
    )
    events: list[SttEvent] = []
    collector_stop = asyncio.Event()
    collector_task = asyncio.create_task(
        _collect_runner_events(
            runner=runner,
            events=events,
            stop_requested=collector_stop,
        ),
        name=f"{logger_name}.collector",
    )
    start_error: Exception | None = None
    replay_error: Exception | None = None
    close_error: Exception | None = None

    try:
        try:
            await runner.start()
        except Exception as exc:
            start_error = exc
        else:
            try:
                await _replay_wav_fixture(
                    queue=audio_queue,
                    capture_config=app_config.capture,
                    wav_path=wav_path,
                    runner=runner,
                )
            except Exception as exc:
                replay_error = exc
            try:
                await runner.close(
                    timeout_seconds=app_config.pipeline.shutdown_timeout_seconds
                )
            except Exception as exc:
                close_error = exc
    finally:
        collector_stop.set()
        await collector_task
        audio_queue.close()

    return _build_result(
        events=events,
        keyword_groups=keyword_groups,
        start_error=start_error,
        replay_error=replay_error,
        close_error=close_error,
    )


async def _collect_runner_events(
    *,
    runner: AsyncSttSessionRunner,
    events: list[SttEvent],
    stop_requested: asyncio.Event,
) -> None:
    while True:
        event = await runner.get_event(timeout=_EVENT_POLL_TIMEOUT_SECONDS)
        if event is not None:
            events.append(event)
            continue
        if stop_requested.is_set():
            return


async def _replay_wav_fixture(
    *,
    queue: DropOldestAsyncQueue[AudioChunk],
    capture_config: CaptureConfig,
    wav_path: Path,
    runner: AsyncSttSessionRunner,
) -> None:
    bytes_per_frame = capture_config.channels * 2
    next_send_at = time.monotonic()

    for sequence, pcm16 in enumerate(
        iter_wav_chunks(wav_path, frames_per_chunk=capture_config.frames_per_chunk),
        start=1,
    ):
        runner.check_health()
        frame_count = len(pcm16) // bytes_per_frame
        queue.put_nowait(
            AudioChunk(
                sequence=sequence,
                pcm16=pcm16,
                frame_count=frame_count,
                captured_at_monotonic=time.monotonic(),
            )
        )
        next_send_at += frame_count / capture_config.sample_rate
        delay = next_send_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)


def _build_result(
    *,
    events: list[SttEvent],
    keyword_groups: tuple[tuple[str, ...], ...],
    start_error: Exception | None,
    replay_error: Exception | None,
    close_error: Exception | None,
) -> LiveSttHarnessResult:
    statuses = [event for event in events if isinstance(event, SttStatusEvent)]
    finals = [
        event.text
        for event in events
        if isinstance(event, TranscriptRevisionEvent)
        and event.is_final
        and event.text.strip()
    ]
    aggregated_text = "".join(finals)
    keyword_groups_matched = _matches_keyword_groups(aggregated_text, keyword_groups)
    diagnostics = _format_diagnostics(
        statuses=statuses,
        finals=finals,
        aggregated_text=aggregated_text,
        keyword_groups=keyword_groups,
        keyword_groups_matched=keyword_groups_matched,
        start_error=start_error,
        replay_error=replay_error,
        close_error=close_error,
    )
    return LiveSttHarnessResult(
        events=events,
        statuses=statuses,
        finals=finals,
        aggregated_text=aggregated_text,
        keyword_groups_matched=keyword_groups_matched,
        diagnostics=diagnostics,
        start_error=str(start_error) if start_error is not None else None,
        replay_error=str(replay_error) if replay_error is not None else None,
        close_error=str(close_error) if close_error is not None else None,
    )


def _matches_keyword_groups(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    normalized = "".join(text.split())
    return all(any(keyword in normalized for keyword in group) for group in groups)


def _format_diagnostics(
    *,
    statuses: list[SttStatusEvent],
    finals: list[str],
    aggregated_text: str,
    keyword_groups: tuple[tuple[str, ...], ...],
    keyword_groups_matched: bool,
    start_error: Exception | None,
    replay_error: Exception | None,
    close_error: Exception | None,
) -> str:
    status_lines = [
        f"{status.status.value}: {status.message}"
        if status.message
        else status.status.value
        for status in statuses
    ]
    return (
        "Live STT transcription did not meet expectations.\n"
        f"start_error={start_error}\n"
        f"replay_error={replay_error}\n"
        f"close_error={close_error}\n"
        f"statuses={status_lines}\n"
        f"finals={finals}\n"
        f"aggregated_text={aggregated_text!r}\n"
        f"keyword_groups={keyword_groups}\n"
        f"keyword_groups_matched={keyword_groups_matched}"
    )


__all__ = [
    "DEFAULT_LIVE_KEYWORD_GROUPS",
    "LiveSttHarnessResult",
    "assert_live_transcription_result",
    "build_live_app_config",
    "run_live_stt_fixture",
]
