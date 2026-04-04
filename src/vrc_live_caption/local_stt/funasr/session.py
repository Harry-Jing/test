"""Run one websocket session against the local FunASR sidecar models."""

import asyncio
import math
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from typing import Any, Protocol

from websockets.exceptions import ConnectionClosed

from ...errors import SttSessionError
from .chunking import StreamingPacketChunker, pcm_duration_ms
from .config import FunasrLocalServiceConfig
from .protocol import (
    CLIENT_START,
    CLIENT_STOP,
    LOCAL_STT_MODE,
    PCM16LE_FORMAT,
    build_error_message,
    build_ready_message,
    build_transcript_message,
    decode_json_message,
    encode_json_message,
)

_HISTORY_PACKET_LIMIT = 64
_HISTORY_TAIL_AFTER_FINAL = 20


class FunasrModelBundle(Protocol):
    """Describe the sync inference methods needed by one websocket session."""

    def detect_speech_boundary(
        self,
        *,
        audio: bytes,
        state: dict[str, Any],
    ) -> tuple[int, int]:
        """Return `(speech_start_ms, speech_end_ms)` for one streaming packet."""
        ...

    def transcribe_online(
        self,
        *,
        audio: bytes,
        state: dict[str, Any],
    ) -> str:
        """Return one streaming partial transcript."""
        ...

    def transcribe_offline(
        self,
        *,
        audio: bytes,
        state: dict[str, Any],
        punc_state: dict[str, Any],
    ) -> str:
        """Return one stabilized offline transcript."""
        ...


@dataclass(slots=True)
class SessionRuntimeState:
    """Store mutable per-connection state for the local websocket session."""

    started: bool = False
    packet_chunker: StreamingPacketChunker | None = None
    online_state: dict[str, Any] = field(default_factory=dict)
    vad_state: dict[str, Any] = field(default_factory=dict)
    punc_state: dict[str, Any] = field(default_factory=dict)
    history_packets: deque[bytes] = field(
        default_factory=lambda: deque(maxlen=_HISTORY_PACKET_LIMIT)
    )
    online_packets: list[bytes] = field(default_factory=list)
    offline_packets: list[bytes] = field(default_factory=list)
    speech_active: bool = False
    vad_elapsed_ms: int = 0
    current_segment_id: int | None = None
    next_segment_id: int = 1
    last_online_text: str = ""

    def ensure_segment_id(self) -> int:
        """Return the current segment id, creating one if needed."""
        if self.current_segment_id is None:
            self.current_segment_id = self.next_segment_id
            self.next_segment_id += 1
            self.last_online_text = ""
        return self.current_segment_id

    def reset_after_final(self) -> None:
        """Reset per-segment buffers after one offline final is emitted."""
        self.offline_packets.clear()
        self.online_packets.clear()
        self.speech_active = False
        self.current_segment_id = None
        self.last_online_text = ""
        recent_packets = list(self.history_packets)[-_HISTORY_TAIL_AFTER_FINAL:]
        self.history_packets.clear()
        self.history_packets.extend(recent_packets)


class FunasrWebsocketSession:
    """Handle one sidecar websocket session using the repository-local protocol."""

    def __init__(
        self,
        *,
        websocket: Any,
        config: FunasrLocalServiceConfig,
        models: FunasrModelBundle,
        executor: Executor | None,
        logger,
    ) -> None:
        self._websocket = websocket
        self._config = config
        self._models = models
        self._executor = executor
        self._logger = logger
        self._state = SessionRuntimeState()
        self._closed = False

    async def run(self) -> None:
        """Run the websocket session until the client stops or disconnects."""
        try:
            async for message in self._websocket:
                if isinstance(message, bytes):
                    await self._handle_audio(message)
                    continue
                should_continue = await self._handle_json_message(message)
                if not should_continue:
                    return
        except ConnectionClosed:
            return
        except SttSessionError as exc:
            self._logger.error("Local STT websocket session failed: %s", exc)
            try:
                await self._send_error(str(exc), fatal=True)
            except Exception:
                return
        finally:
            if self._state.started and not self._closed:
                await self._finalize_pending_segment()

    async def _handle_json_message(self, raw_message: str) -> bool:
        try:
            message = decode_json_message(raw_message)
        except Exception as exc:
            await self._send_error(f"Invalid JSON control message: {exc}", fatal=True)
            return False

        message_type = _get_value(message, "type")
        if message_type == CLIENT_START:
            await self._handle_start(message)
            return True
        if message_type == CLIENT_STOP:
            await self._handle_stop()
            return False

        await self._send_error(
            f"Unsupported local STT control message type: {message_type}", fatal=True
        )
        return False

    async def _handle_start(self, message: dict[str, Any]) -> None:
        if self._state.started:
            raise SttSessionError("FunASR local session already started")

        mode = _coerce_text(_get_value(message, "mode"))
        sample_format = _coerce_text(_get_value(message, "sample_format"))
        sample_rate = int(_get_value(message, "sample_rate", 0))
        channels = int(_get_value(message, "channels", 0))

        if mode != LOCAL_STT_MODE:
            raise SttSessionError(f"Unsupported local STT mode: {mode}")
        if sample_format != PCM16LE_FORMAT:
            raise SttSessionError(
                f"Unsupported local STT sample format: {sample_format}"
            )
        if sample_rate != 16_000:
            raise SttSessionError(
                "FunASR local sidecar currently requires sample_rate = 16000"
            )
        if channels != 1:
            raise SttSessionError(
                "FunASR local sidecar currently requires channels = 1"
            )

        self._state.started = True
        self._state.packet_chunker = StreamingPacketChunker(
            sample_rate=sample_rate,
            channels=channels,
            packet_duration_ms=self._config.packet_duration_ms,
        )
        self._state.online_state = {
            "cache": {},
            "is_final": False,
            "chunk_size": self._config.chunk_size_list,
            "encoder_chunk_look_back": self._config.encoder_chunk_look_back,
            "decoder_chunk_look_back": self._config.decoder_chunk_look_back,
        }
        self._state.vad_state = {
            "cache": {},
            "is_final": False,
            "chunk_size": self._config.packet_duration_ms,
        }
        self._state.punc_state = {"cache": {}}
        await self._send_json(
            build_ready_message("FunASR local 2-pass sidecar ready")
        )

    async def _handle_stop(self) -> None:
        await self._finalize_pending_segment()
        self._closed = True

    async def _handle_audio(self, audio: bytes) -> None:
        if not self._state.started or self._state.packet_chunker is None:
            message = "Audio received before the local STT session was started"
            await self._send_error(message, fatal=True)
            self._closed = True
            raise SttSessionError(message)

        for packet in self._state.packet_chunker.append(audio):
            await self._process_packet(packet)

    async def _process_packet(self, packet: bytes) -> None:
        packet_duration = pcm_duration_ms(packet, sample_rate=16_000)
        self._state.history_packets.append(packet)
        self._state.vad_elapsed_ms += packet_duration
        self._state.online_packets.append(packet)
        if self._state.speech_active:
            self._state.offline_packets.append(packet)

        if len(self._state.online_packets) >= self._config.chunk_interval:
            await self._emit_online_partial()

        speech_start_ms, speech_end_ms = await self._run_blocking(
            self._models.detect_speech_boundary,
            audio=packet,
            state=self._state.vad_state,
        )

        if speech_start_ms != -1:
            self._state.ensure_segment_id()
            self._state.speech_active = True
            lookback_packets = max(
                1,
                math.ceil(
                    max(0, self._state.vad_elapsed_ms - speech_start_ms)
                    / max(packet_duration, 1)
                ),
            )
            self._state.offline_packets = list(self._state.history_packets)[
                -lookback_packets:
            ]

        if speech_end_ms != -1:
            await self._emit_offline_final()

    async def _finalize_pending_segment(self) -> None:
        if self._state.packet_chunker is not None:
            for packet in self._state.packet_chunker.flush():
                await self._process_packet(packet)
        await self._emit_offline_final(force=True)

    async def _emit_online_partial(self) -> None:
        if not self._state.online_packets:
            return
        audio = b"".join(self._state.online_packets)
        self._state.online_packets = []
        text = _coerce_text(
            await self._run_blocking(
                self._models.transcribe_online,
                audio=audio,
                state=self._state.online_state,
            )
        ).strip()
        if not text or text == self._state.last_online_text:
            return
        segment_id = self._state.ensure_segment_id()
        self._state.last_online_text = text
        await self._send_json(
            build_transcript_message(
                phase="online",
                segment_id=segment_id,
                text=text,
                is_final=False,
            )
        )

    async def _emit_offline_final(self, *, force: bool = False) -> None:
        audio_packets = list(self._state.offline_packets)
        if not audio_packets and force and self._state.current_segment_id is not None:
            audio_packets = list(self._state.history_packets)
        if not audio_packets:
            self._reset_online_state()
            self._state.reset_after_final()
            return

        text = _coerce_text(
            await self._run_blocking(
                self._models.transcribe_offline,
                audio=b"".join(audio_packets),
                state={},
                punc_state=self._state.punc_state,
            )
        ).strip()
        if text:
            await self._send_json(
                build_transcript_message(
                    phase="offline",
                    segment_id=self._state.ensure_segment_id(),
                    text=text,
                    is_final=True,
                )
            )
        self._reset_online_state()
        self._state.reset_after_final()

    def _reset_online_state(self) -> None:
        self._state.online_state["cache"] = {}
        self._state.online_state["is_final"] = False

    async def _send_json(self, payload: dict[str, Any]) -> None:
        await self._websocket.send(encode_json_message(payload))

    async def _send_error(self, message: str, *, fatal: bool) -> None:
        await self._send_json(build_error_message(message, fatal=fatal))

    async def _run_blocking(
        self,
        func: Callable[..., Any],
        /,
        **kwargs: Any,
    ) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: func(**kwargs),
        )


def _get_value(value: dict[str, Any], key: str, default: Any = None) -> Any:
    return value.get(key, default)


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


__all__ = [
    "FunasrModelBundle",
    "FunasrWebsocketSession",
    "SessionRuntimeState",
]
