"""Define audio backend protocols and device metadata contracts."""

from dataclasses import dataclass
from typing import Any, Protocol

from ..config import CaptureConfig
from ..errors import AudioBackendError


@dataclass(slots=True, frozen=True)
class AudioDeviceInfo:
    """Describe an input device exposed by an audio backend."""

    index: int
    name: str
    max_input_channels: int
    default_sample_rate: float
    is_default: bool = False

    @property
    def label(self) -> str:
        """Return a user-facing label with the device index and default marker."""
        default_marker = " [default]" if self.is_default else ""
        return f"#{self.index} {self.name}{default_marker}"


class ManagedInputStream(Protocol):
    """Define the minimal stream lifecycle used by the audio runtime."""

    def start(self) -> None:
        """Begin audio capture for the stream."""
        ...

    def stop(self) -> None:
        """Stop audio capture without releasing stream resources."""
        ...

    def close(self) -> None:
        """Release the underlying stream resources."""
        ...


class AudioBackend(Protocol):
    """Define device discovery and input stream operations for audio capture."""

    def list_input_devices(self) -> list[AudioDeviceInfo]:
        """Return available devices that can capture audio."""
        ...

    def resolve_input_device(self, device: int | str | None) -> AudioDeviceInfo:
        """Resolve a configured selector to one concrete input device.

        Args:
            device: Device selector from configuration. Backends should accept
                ``None`` for the default device, numeric indexes, and backend-
                specific string selectors.

        Returns:
            The resolved input device metadata.

        Raises:
            AudioBackendError: Raised when no matching device is available or
                the selector is ambiguous.
        """
        ...

    def open_input_stream(
        self,
        *,
        capture_config: CaptureConfig,
        device_index: int | None,
        callback: Any,
    ) -> ManagedInputStream:
        """Open an input stream for the resolved device and callback.

        Args:
            capture_config: Runtime capture settings to apply to the stream.
            device_index: Concrete device index to open, or ``None`` to let
                the backend choose its default input device.
            callback: Backend-specific audio callback invoked for captured
                chunks.

        Returns:
            A managed input stream that supports start, stop, and close.

        Raises:
            AudioBackendError: Raised when the backend cannot create the input
                stream.
        """
        ...

    def probe_input_stream(
        self,
        *,
        capture_config: CaptureConfig,
        device_index: int | None,
        duration_seconds: float,
    ) -> None:
        """Open and briefly run an input stream for diagnostics.

        Args:
            capture_config: Runtime capture settings to apply to the probe stream.
            device_index: Concrete device index to probe, or ``None`` to let
                the backend choose its default input device.
            duration_seconds: Time to keep the probe stream running before it
                is stopped and closed.

        Raises:
            AudioBackendError: Raised when the probe stream cannot be opened,
                started, or completed successfully.
        """
        ...


__all__ = [
    "AudioBackend",
    "AudioBackendError",
    "AudioDeviceInfo",
    "ManagedInputStream",
]
