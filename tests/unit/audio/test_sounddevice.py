import builtins

import pytest

from tests.support.fakes.audio import FakeSounddeviceModule, FakeSounddeviceStream
from vrc_live_caption.audio import (
    AudioBackendError,
    SoundDeviceBackend,
    import_sounddevice,
)
from vrc_live_caption.config import CaptureConfig


def test_list_input_devices_filters_output_only_devices() -> None:
    backend = SoundDeviceBackend(
        sounddevice_module=FakeSounddeviceModule(
            devices=[
                {
                    "name": "Speakers",
                    "max_input_channels": 0,
                    "default_samplerate": 48_000,
                },
                {
                    "name": "USB Mic",
                    "max_input_channels": 1,
                    "default_samplerate": 16_000,
                },
                {
                    "name": "Interface",
                    "max_input_channels": 2,
                    "default_samplerate": 48_000,
                },
            ],
            default_device=(2, 0),
        )
    )

    devices = backend.list_input_devices()

    assert [device.name for device in devices] == ["USB Mic", "Interface"]
    assert devices[1].is_default is True


def test_resolve_input_device_prefers_default_device() -> None:
    backend = SoundDeviceBackend(
        sounddevice_module=FakeSounddeviceModule(
            devices=[
                {
                    "name": "Mic A",
                    "max_input_channels": 1,
                    "default_samplerate": 16_000,
                },
                {
                    "name": "Mic B",
                    "max_input_channels": 1,
                    "default_samplerate": 48_000,
                },
            ],
            default_device=(1, 0),
        )
    )

    device = backend.resolve_input_device(None)

    assert device.index == 1
    assert device.label == "#1 Mic B [default]"


def test_resolve_input_device_supports_index_exact_and_partial_name() -> None:
    backend = SoundDeviceBackend(
        sounddevice_module=FakeSounddeviceModule(
            devices=[
                {
                    "name": "USB Mic",
                    "max_input_channels": 1,
                    "default_samplerate": 16_000,
                },
                {
                    "name": "Desk Microphone",
                    "max_input_channels": 1,
                    "default_samplerate": 48_000,
                },
            ]
        )
    )

    assert backend.resolve_input_device(0).name == "USB Mic"
    assert backend.resolve_input_device("Desk Microphone").index == 1
    assert backend.resolve_input_device("desk").index == 1


def test_resolve_input_device_rejects_ambiguous_partial_name() -> None:
    backend = SoundDeviceBackend(
        sounddevice_module=FakeSounddeviceModule(
            devices=[
                {
                    "name": "USB Mic A",
                    "max_input_channels": 1,
                    "default_samplerate": 16_000,
                },
                {
                    "name": "USB Mic B",
                    "max_input_channels": 1,
                    "default_samplerate": 16_000,
                },
            ]
        )
    )

    with pytest.raises(
        AudioBackendError, match="Multiple input devices partially match 'usb'"
    ):
        backend.resolve_input_device("usb")


def test_resolve_input_device_rejects_missing_device() -> None:
    backend = SoundDeviceBackend(
        sounddevice_module=FakeSounddeviceModule(
            devices=[
                {
                    "name": "USB Mic",
                    "max_input_channels": 1,
                    "default_samplerate": 16_000,
                },
            ]
        )
    )

    with pytest.raises(AudioBackendError, match="Input device 'missing' was not found"):
        backend.resolve_input_device("missing")


def test_open_input_stream_wraps_backend_errors() -> None:
    backend = SoundDeviceBackend(
        sounddevice_module=FakeSounddeviceModule(
            raw_input_stream_error=RuntimeError("stream open failure")
        )
    )

    with pytest.raises(
        AudioBackendError, match="Failed to open input stream: stream open failure"
    ):
        backend.open_input_stream(
            capture_config=CaptureConfig(),
            device_index=1,
            callback=lambda *_args: None,
        )


def test_probe_input_stream_starts_and_closes_stream() -> None:
    stream = FakeSounddeviceStream()
    backend = SoundDeviceBackend(
        sounddevice_module=FakeSounddeviceModule(raw_input_stream=stream)
    )

    backend.probe_input_stream(
        capture_config=CaptureConfig(),
        device_index=1,
        duration_seconds=0.0,
    )

    assert stream.started is True
    assert stream.stopped is True
    assert stream.closed is True


def test_probe_input_stream_wraps_start_failures() -> None:
    backend = SoundDeviceBackend(
        sounddevice_module=FakeSounddeviceModule(
            raw_input_stream=FakeSounddeviceStream(fail_on_start=True)
        )
    )

    with pytest.raises(
        AudioBackendError,
        match="Failed to probe input stream: fake sounddevice start failure",
    ):
        backend.probe_input_stream(
            capture_config=CaptureConfig(),
            device_index=1,
            duration_seconds=0.0,
        )


def test_import_sounddevice_wraps_import_failures(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sounddevice":
            raise ImportError("boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(AudioBackendError, match="Unable to import sounddevice: boom"):
        import_sounddevice()
