from types import SimpleNamespace
from typing import Any

from vrc_live_caption.audio import AudioBackendError, AudioDeviceInfo


class FakeStream:
    def __init__(
        self,
        callback,
        *,
        fail_on_start: bool = False,
    ) -> None:
        self._callback = callback
        self._fail_on_start = fail_on_start
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        if self._fail_on_start:
            raise RuntimeError("fake stream start failure")
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def emit(
        self, payload: bytes, *, frames: int | None = None, status: object = ""
    ) -> None:
        resolved_frames = frames if frames is not None else len(payload) // 2
        self._callback(payload, resolved_frames, None, status)


class FakeBackend:
    def __init__(
        self,
        *,
        devices: list[AudioDeviceInfo] | None = None,
        fail_on_start: bool = False,
        probe_error: str | None = None,
        list_error: str | None = None,
    ) -> None:
        self.devices = devices or [
            AudioDeviceInfo(
                index=1,
                name="Fake Microphone",
                max_input_channels=1,
                default_sample_rate=16_000.0,
                is_default=True,
            )
        ]
        self.fail_on_start = fail_on_start
        self.probe_error = probe_error
        self.list_error = list_error
        self.last_stream: FakeStream | None = None
        self.last_probe: tuple[int | None, float] | None = None

    def list_input_devices(self) -> list[AudioDeviceInfo]:
        if self.list_error:
            raise AudioBackendError(self.list_error)
        return list(self.devices)

    def resolve_input_device(self, device: int | str | None) -> AudioDeviceInfo:
        if not self.devices:
            raise AudioBackendError("No input audio devices were found")
        if device is None:
            for info in self.devices:
                if info.is_default:
                    return info
            return self.devices[0]
        if isinstance(device, int):
            for info in self.devices:
                if info.index == device:
                    return info
            raise AudioBackendError(f"Input device index {device} was not found")
        for info in self.devices:
            if info.name.casefold() == device.casefold():
                return info
        raise AudioBackendError(f"Input device '{device}' was not found")

    def open_input_stream(self, *, capture_config, device_index, callback):
        self.last_stream = FakeStream(callback, fail_on_start=self.fail_on_start)
        return self.last_stream

    def probe_input_stream(
        self, *, capture_config, device_index, duration_seconds: float
    ) -> None:
        self.last_probe = (device_index, duration_seconds)
        if self.probe_error:
            raise AudioBackendError(self.probe_error)


class FakeSounddeviceStream:
    def __init__(self, *, fail_on_start: bool = False) -> None:
        self.fail_on_start = fail_on_start
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        if self.fail_on_start:
            raise RuntimeError("fake sounddevice start failure")
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class FakeSounddeviceModule:
    def __init__(
        self,
        *,
        devices: list[dict[str, Any]] | None = None,
        default_device: Any = None,
        raw_input_stream: FakeSounddeviceStream | None = None,
        raw_input_stream_error: BaseException | None = None,
        query_devices_error: BaseException | None = None,
    ) -> None:
        self._devices = devices or []
        self.default = SimpleNamespace(device=default_device)
        self._raw_input_stream = raw_input_stream or FakeSounddeviceStream()
        self._raw_input_stream_error = raw_input_stream_error
        self._query_devices_error = query_devices_error
        self.raw_input_stream_calls: list[dict[str, Any]] = []

    def query_devices(self) -> list[dict[str, Any]]:
        if self._query_devices_error is not None:
            raise self._query_devices_error
        return list(self._devices)

    def RawInputStream(self, **kwargs: Any) -> FakeSounddeviceStream:
        self.raw_input_stream_calls.append(kwargs)
        if self._raw_input_stream_error is not None:
            raise self._raw_input_stream_error
        return self._raw_input_stream
