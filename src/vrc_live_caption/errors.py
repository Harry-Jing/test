"""Shared exception hierarchy for VRC Live Caption."""


class VrcLiveCaptionError(RuntimeError):
    """Base exception for expected application-level failures."""


class ConfigError(VrcLiveCaptionError):
    """Raised when the application configuration is invalid."""


class SecretError(VrcLiveCaptionError):
    """Raised when a required secret is missing."""


class OscError(VrcLiveCaptionError):
    """Raised when OSC output cannot be configured or sent."""


class AudioError(VrcLiveCaptionError):
    """Base exception for audio subsystem failures."""


class AudioBackendError(AudioError):
    """Raised when an audio backend cannot be used."""


class AudioRuntimeError(AudioError):
    """Raised when the audio runtime cannot start or continue."""


class SttError(VrcLiveCaptionError):
    """Base exception for STT subsystem failures."""


class SttSessionError(SttError):
    """Raised when an STT session cannot start or continue."""


class SttProviderFatalError(SttError):
    """Raised when an STT provider reports a non-retriable failure."""


class SttProviderRetriableError(SttError):
    """Raised when an STT provider reports a retriable failure."""


class PipelineError(VrcLiveCaptionError):
    """Raised when the transcription pipeline cannot continue cleanly."""


class RunnerStateError(PipelineError):
    """Raised when the transcription runner is used in an invalid state."""


__all__ = [
    "AudioBackendError",
    "AudioError",
    "AudioRuntimeError",
    "ConfigError",
    "OscError",
    "PipelineError",
    "RunnerStateError",
    "SecretError",
    "SttError",
    "SttProviderFatalError",
    "SttProviderRetriableError",
    "SttSessionError",
    "VrcLiveCaptionError",
]
