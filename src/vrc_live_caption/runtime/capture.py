"""Implement microphone capture as an async-loop-owned bridge around `sounddevice`."""

import asyncio
import logging
import time
from typing import Any

from ..audio import (
    AudioBackend,
    AudioDeviceInfo,
    ManagedInputStream,
    SoundDeviceBackend,
)
from ..config import CaptureConfig
from ..errors import AudioRuntimeError
from .queue import DropOldestAsyncQueue
from .types import AudioChunk


class MicrophoneCapture:
    """Capture microphone audio into a bounded async queue."""

    def __init__(
        self,
        *,
        capture_config: CaptureConfig,
        queue: DropOldestAsyncQueue[AudioChunk],
        backend: AudioBackend | None = None,
        logger: logging.Logger | None = None,
        now=time.monotonic,
    ) -> None:
        self.capture_config = capture_config
        self.queue = queue
        self.backend = backend or SoundDeviceBackend()
        self.logger = logger or logging.getLogger("vrc_live_caption.capture")
        self._now = now
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: ManagedInputStream | None = None
        self._resolved_device: AudioDeviceInfo | None = None
        self._error: BaseException | None = None
        self._sequence = 0
        self._started = False

    @property
    def resolved_device(self) -> AudioDeviceInfo | None:
        """Return the device resolved during startup, if available."""
        return self._resolved_device

    async def start(self) -> None:
        """Resolve the device, open the stream, and start audio capture."""
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        try:
            self._resolved_device = self.backend.resolve_input_device(
                self.capture_config.device
            )
            self._stream = self.backend.open_input_stream(
                capture_config=self.capture_config,
                device_index=self._resolved_device.index,
                callback=self._stream_callback,
            )
            self._stream.start()
        except Exception as exc:
            self._error = exc
            raise AudioRuntimeError(f"Failed to start audio capture: {exc}") from exc

        self._started = True
        self.logger.info(
            "Audio capture started: device=%s sample_rate=%sHz block_ms=%s buffer_max=%s",
            self._resolved_device.label,
            self.capture_config.sample_rate,
            self.capture_config.block_duration_ms,
            self.queue.max_items,
        )

    async def stop(self) -> None:
        """Stop and close the underlying input stream."""
        if self._stream is None:
            return
        try:
            self._stream.stop()
        except Exception as exc:
            if self._error is None:
                self._error = exc
            self.logger.error("Failed to stop audio stream: %s", exc)
        try:
            self._stream.close()
        except Exception as exc:
            if self._error is None:
                self._error = exc
            self.logger.error("Failed to close audio stream: %s", exc)
        self._stream = None
        self._started = False

    def check_health(self) -> None:
        """Raise when the capture service previously recorded an error."""
        if self._error is None:
            return
        raise AudioRuntimeError(str(self._error)) from self._error

    def _stream_callback(
        self, indata: Any, frames: int, _time_info: object, status: object
    ) -> None:
        if self._loop is None:
            return
        if status:
            self.logger.warning("Audio input status: %s", status)
        self._sequence += 1
        chunk = AudioChunk(
            sequence=self._sequence,
            pcm16=bytes(indata),
            frame_count=frames,
            captured_at_monotonic=self._now(),
        )
        self.queue.put_from_thread(chunk, self._loop)


__all__ = ["MicrophoneCapture"]
