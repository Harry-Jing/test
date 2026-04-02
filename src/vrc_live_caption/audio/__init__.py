"""Expose audio backend protocols and the default `sounddevice` adapter."""

from .sounddevice import SoundDeviceBackend, import_sounddevice
from .types import AudioBackend, AudioBackendError, AudioDeviceInfo, ManagedInputStream

__all__ = [
    "AudioBackend",
    "AudioBackendError",
    "AudioDeviceInfo",
    "ManagedInputStream",
    "SoundDeviceBackend",
    "import_sounddevice",
]
