"""Wraps `sounddevice` for input device discovery and stream management.

Keeps backend-specific import and stream failures behind the audio boundary.
"""

from time import sleep
from typing import Any

from ..config import CaptureConfig
from .types import AudioBackendError, AudioDeviceInfo, ManagedInputStream


def import_sounddevice() -> Any:
    """Import `sounddevice` lazily and re-raise import failures as `AudioBackendError`."""
    try:
        import sounddevice  # type: ignore[import-not-found]
    except (
        Exception
    ) as exc:  # pragma: no cover - exercised through command error handling
        raise AudioBackendError(f"Unable to import sounddevice: {exc}") from exc
    return sounddevice


class SoundDeviceBackend:
    """Adapt `sounddevice` behind the project's audio backend contract.

    Keep backend-specific imports and stream behavior behind a testable boundary.
    """

    def __init__(self, sounddevice_module: Any | None = None) -> None:
        """Initialize the backend with an optional injected `sounddevice` module."""
        self._sounddevice = sounddevice_module

    def _sd(self) -> Any:
        if self._sounddevice is None:
            self._sounddevice = import_sounddevice()
        return self._sounddevice

    def list_input_devices(self) -> list[AudioDeviceInfo]:
        """Return input-capable `sounddevice` devices with default-device metadata."""
        sd = self._sd()
        devices = sd.query_devices()
        default_input = self._default_input_index(sd)
        results: list[AudioDeviceInfo] = []
        for index, device in enumerate(devices):
            max_input_channels = int(device.get("max_input_channels", 0))
            if max_input_channels < 1:
                continue
            results.append(
                AudioDeviceInfo(
                    index=index,
                    name=str(device.get("name", f"Device {index}")),
                    max_input_channels=max_input_channels,
                    default_sample_rate=float(device.get("default_samplerate", 0.0)),
                    is_default=index == default_input,
                )
            )
        return results

    def resolve_input_device(self, device: int | str | None) -> AudioDeviceInfo:
        """Resolve a configured selector using default, exact, then partial matches.

        Args:
            device: Device selector from configuration. Accept ``None`` for
                the default device, a numeric index, or a case-insensitive
                device name fragment.

        Returns:
            The single input device selected by the configured selector.

        Raises:
            AudioBackendError: Raised when no input devices exist, the selector
                does not match any device, or a string selector matches more
                than one device.
        """
        devices = self.list_input_devices()
        if not devices:
            raise AudioBackendError("No input audio devices were found")

        if device is None:
            for info in devices:
                if info.is_default:
                    return info
            return devices[0]

        if isinstance(device, int):
            for info in devices:
                if info.index == device:
                    return info
            raise AudioBackendError(f"Input device index {device} was not found")

        normalized = device.casefold()
        exact_matches = [info for info in devices if info.name.casefold() == normalized]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise AudioBackendError(
                f"Multiple input devices match name '{device}'. Use the device index instead."
            )

        partial_matches = [
            info for info in devices if normalized in info.name.casefold()
        ]
        if len(partial_matches) == 1:
            return partial_matches[0]
        if len(partial_matches) > 1:
            raise AudioBackendError(
                f"Multiple input devices partially match '{device}'. Use the device index instead."
            )
        raise AudioBackendError(f"Input device '{device}' was not found")

    def open_input_stream(
        self,
        *,
        capture_config: CaptureConfig,
        device_index: int | None,
        callback: Any,
    ) -> ManagedInputStream:
        """Open a raw input stream configured for the runtime audio settings.

        Args:
            capture_config: Runtime capture settings to apply to the stream.
            device_index: Concrete input device index, or ``None`` to let
                `sounddevice` use its default input device.
            callback: Raw input callback invoked by `sounddevice`.

        Returns:
            A managed raw input stream that is not started yet.

        Raises:
            AudioBackendError: Raised when `sounddevice` cannot create the
                requested stream.
        """
        sd = self._sd()
        kwargs: dict[str, Any] = {
            "samplerate": capture_config.sample_rate,
            "channels": capture_config.channels,
            "dtype": capture_config.dtype,
            "blocksize": capture_config.frames_per_chunk,
            "callback": callback,
        }
        if device_index is not None:
            kwargs["device"] = device_index
        try:
            return sd.RawInputStream(**kwargs)
        except Exception as exc:
            raise AudioBackendError(f"Failed to open input stream: {exc}") from exc

    def probe_input_stream(
        self,
        *,
        capture_config: CaptureConfig,
        device_index: int | None,
        duration_seconds: float,
    ) -> None:
        """Run a temporary input stream probe for the requested duration.

        This call blocks until the duration elapses and always attempts to stop
        and close the probe stream before returning.

        Args:
            capture_config: Runtime capture settings to apply to the probe stream.
            device_index: Concrete input device index, or ``None`` to let
                `sounddevice` use its default input device.
            duration_seconds: Number of seconds to keep the probe stream open.

        Raises:
            AudioBackendError: Raised when the probe stream cannot be opened,
                started, or kept running for the requested duration.
        """
        stream = self.open_input_stream(
            capture_config=capture_config,
            device_index=device_index,
            callback=lambda *_args: None,
        )
        try:
            stream.start()
            sleep(duration_seconds)
        except Exception as exc:
            raise AudioBackendError(f"Failed to probe input stream: {exc}") from exc
        finally:
            try:
                stream.stop()
            finally:
                stream.close()

    @staticmethod
    def _default_input_index(sounddevice_module: Any) -> int | None:
        default_device = getattr(sounddevice_module, "default", None)
        if default_device is None:
            return None
        resolved = getattr(default_device, "device", None)
        if isinstance(resolved, (list, tuple)) and resolved:
            candidate = resolved[0]
        else:
            candidate = resolved
        return candidate if isinstance(candidate, int) and candidate >= 0 else None
