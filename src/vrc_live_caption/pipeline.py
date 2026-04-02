"""Own the async audio capture, STT, and output pipeline for live transcription."""

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .audio import AudioDeviceInfo
from .errors import PipelineError, VrcLiveCaptionError
from .runtime import (
    AudioChunk,
    DropOldestAsyncQueue,
    MicrophoneCapture,
    WaveFileAudioSink,
)
from .stt import (
    AsyncSttSessionRunner,
    SttStatus,
    SttStatusEvent,
    TranscriptRevisionEvent,
)

_INTERRUPT_EXCEPTIONS = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)


class TranscriptOutput(Protocol):
    """Define the transcript output hooks used by the live pipeline."""

    async def start(self) -> None:
        """Start any background output tasks."""
        ...

    def handle_revision(self, event: TranscriptRevisionEvent) -> None:
        """Handle one transcript revision emitted by the STT runner."""
        ...

    async def shutdown(self, *, timeout_seconds: float = 1.0) -> None:
        """Flush pending output during pipeline shutdown."""
        ...


class CaptureService(Protocol):
    """Define the capture operations owned by the live pipeline."""

    queue: DropOldestAsyncQueue[AudioChunk]
    resolved_device: AudioDeviceInfo | None

    async def start(self) -> None:
        """Start audio capture and resolve the active device."""
        ...

    async def stop(self) -> None:
        """Stop audio capture and release any owned resources."""
        ...

    def check_health(self) -> None:
        """Raise when capture previously recorded a runtime failure."""
        ...


class ConsoleTranscriptOutput:
    """Render transcript revisions directly to CLI output lines."""

    def __init__(self, emit_line: Callable[[str], None]) -> None:
        self._emit_line = emit_line

    async def start(self) -> None:
        """Console output has no async startup work."""
        return None

    def handle_revision(self, event: TranscriptRevisionEvent) -> None:
        """Emit one partial or final transcript line when text is present."""
        if not event.text:
            return
        label = "final" if event.is_final else "partial"
        self._emit_line(f"[{label}] {event.text}")

    async def shutdown(self, *, timeout_seconds: float = 1.0) -> None:
        """Console output has nothing buffered."""
        return None


class LivePipelineController:
    """Coordinate capture, STT, and transcript output within one asyncio loop."""

    def __init__(
        self,
        *,
        capture: CaptureService,
        session_runner: AsyncSttSessionRunner,
        transcript_output: TranscriptOutput,
        emit_line: Callable[[str], None],
        heartbeat_seconds: float,
        shutdown_timeout_seconds: float,
        logger: logging.Logger,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capture = capture
        self.session_runner = session_runner
        self.transcript_output = transcript_output
        self._audio_queue = capture.queue
        self._emit_line = emit_line
        self._heartbeat_seconds = heartbeat_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._logger = logger
        self._now = now
        self._started = False
        self._next_heartbeat_at = 0.0

    @property
    def resolved_device(self):
        """Return the device resolved by the underlying audio capture service."""
        return self.capture.resolved_device

    @property
    def backend_description(self) -> str:
        """Return the CLI-friendly description of the configured STT backend."""
        return self.session_runner.backend_description

    async def start(self) -> None:
        """Start transcript output, STT transport, and microphone capture."""
        if self._started:
            return
        await self.transcript_output.start()
        await self.session_runner.start()
        await self.capture.start()
        self._next_heartbeat_at = self._now() + self._heartbeat_seconds
        self._started = True

    async def run_forever(self, *, event_timeout: float = 0.2) -> None:
        """Run the pipeline until the task is cancelled or a subsystem fails."""
        if not self._started:
            await self.start()

        while True:
            timeout = min(event_timeout, self._time_until_heartbeat())
            event = await self.session_runner.get_event(timeout=max(0.0, timeout))
            if event is not None:
                self._emit_event(event)

            now = self._now()
            if now >= self._next_heartbeat_at:
                self._log_heartbeat()
                self._next_heartbeat_at = now + self._heartbeat_seconds

            self.capture.check_health()
            self.session_runner.check_health()

    async def stop(self) -> None:
        """Stop capture, close queues, drain events, and flush transcript output."""
        shutdown_error: Exception | None = None

        try:
            await self.capture.stop()
        except _INTERRUPT_EXCEPTIONS:
            raise
        except Exception as exc:
            shutdown_error = exc
            self._log_shutdown_failure("Failed to stop audio capture cleanly", exc)

        self._audio_queue.close()

        try:
            await self.session_runner.close(
                timeout_seconds=self._shutdown_timeout_seconds
            )
        except _INTERRUPT_EXCEPTIONS:
            raise
        except Exception as exc:
            if shutdown_error is None:
                shutdown_error = exc
            self._log_shutdown_failure("Failed to close STT runner cleanly", exc)

        await self._drain_events(timeout=0.0)

        try:
            await self.transcript_output.shutdown(
                timeout_seconds=self._shutdown_timeout_seconds
            )
        except _INTERRUPT_EXCEPTIONS:
            raise
        except Exception as exc:
            if shutdown_error is None:
                shutdown_error = exc
            self._log_shutdown_failure("Failed to flush transcript output cleanly", exc)

        self._started = False
        if shutdown_error is not None:
            if isinstance(shutdown_error, VrcLiveCaptionError):
                raise shutdown_error
            raise PipelineError(str(shutdown_error)) from shutdown_error

    async def _drain_events(self, *, timeout: float) -> None:
        event = await self.session_runner.get_event(timeout=timeout)
        if event is not None:
            self._emit_event(event)

        while True:
            event = await self.session_runner.get_event(timeout=0.0)
            if event is None:
                return
            self._emit_event(event)

    def _emit_event(self, event: TranscriptRevisionEvent | SttStatusEvent) -> None:
        if isinstance(event, TranscriptRevisionEvent):
            self.transcript_output.handle_revision(event)
            return

        self._log_status_event(event)
        attempt_suffix = (
            f" attempt={event.attempt}" if event.attempt is not None else ""
        )
        if event.message:
            self._emit_line(
                f"[status] {event.status.value}{attempt_suffix}: {event.message}"
            )
            return
        self._emit_line(f"[status] {event.status.value}{attempt_suffix}")

    def _log_status_event(self, event: SttStatusEvent) -> None:
        attempt_suffix = (
            f" attempt={event.attempt}" if event.attempt is not None else ""
        )
        message_suffix = f": {event.message}" if event.message else ""
        status_message = (
            f"STT status={event.status.value}{attempt_suffix}{message_suffix}"
        )

        if event.status in {
            SttStatus.CONNECTING,
            SttStatus.READY,
            SttStatus.CLOSING,
            SttStatus.CLOSED,
        }:
            self._logger.info(status_message)
            return
        if event.status == SttStatus.RETRYING:
            self._logger.warning(status_message)
            return
        self._logger.error(status_message)

    def _log_heartbeat(self) -> None:
        device_label = (
            self.capture.resolved_device.label
            if self.capture.resolved_device is not None
            else "unresolved"
        )
        self._logger.info(
            "Pipeline heartbeat: device=%s audio_queue=%s/%s dropped_audio=%s dropped_events=%s",
            device_label,
            self._audio_queue.qsize(),
            self._audio_queue.max_items,
            self._audio_queue.dropped_items,
            self.session_runner.event_dropped_items,
        )

    def _time_until_heartbeat(self) -> float:
        return max(0.0, self._next_heartbeat_at - self._now())

    def _log_shutdown_failure(self, message: str, exc: BaseException) -> None:
        if isinstance(exc, VrcLiveCaptionError):
            self._logger.error("%s: %s", message, exc)
            return
        self._logger.exception(message)


async def record_audio_sample(
    *,
    capture: MicrophoneCapture,
    output_path: Path,
    duration_seconds: float,
    logger: logging.Logger,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """Record microphone audio to a WAV file using the shared capture service."""
    sink = WaveFileAudioSink(
        output_path,
        sample_rate=capture.capture_config.sample_rate,
        channels=capture.capture_config.channels,
    )
    queue: DropOldestAsyncQueue[AudioChunk] = capture.queue
    deadline = now() + duration_seconds

    try:
        await capture.start()
        while now() < deadline:
            timeout = min(0.2, max(0.0, deadline - now()))
            if timeout == 0.0:
                break
            try:
                chunk = await queue.get(timeout=timeout)
            except asyncio.TimeoutError:
                capture.check_health()
                continue
            sink.write(chunk)
            capture.check_health()
    finally:
        try:
            await capture.stop()
        finally:
            queue.close()
            sink.close()
            logger.info("Recorded sample written to %s", output_path)


__all__ = [
    "ConsoleTranscriptOutput",
    "LivePipelineController",
    "TranscriptOutput",
    "record_audio_sample",
]
