from vrc_live_caption.audio import AudioBackendError
from vrc_live_caption.config import ConfigError
from vrc_live_caption.env import SecretError
from vrc_live_caption.errors import (
    AudioError,
    AudioRuntimeError,
    OscError,
    PipelineError,
    RunnerStateError,
    SttError,
    SttProviderFatalError,
    SttProviderRetriableError,
    SttSessionError,
    VrcLiveCaptionError,
)
from vrc_live_caption.runtime import AudioRuntimeError as ExportedAudioRuntimeError
from vrc_live_caption.stt.iflytek_rtasr import (
    FatalIflytekServerError,
    RetriableIflytekServerError,
)
from vrc_live_caption.stt.openai_realtime import FatalRealtimeServerError


def test_shared_exception_hierarchy_groups_domain_errors() -> None:
    assert issubclass(ConfigError, VrcLiveCaptionError)
    assert issubclass(SecretError, VrcLiveCaptionError)
    assert issubclass(OscError, VrcLiveCaptionError)
    assert issubclass(AudioError, VrcLiveCaptionError)
    assert issubclass(AudioBackendError, AudioError)
    assert issubclass(AudioRuntimeError, AudioError)
    assert issubclass(ExportedAudioRuntimeError, AudioError)
    assert issubclass(SttError, VrcLiveCaptionError)
    assert issubclass(SttSessionError, SttError)
    assert issubclass(PipelineError, VrcLiveCaptionError)
    assert issubclass(RunnerStateError, PipelineError)


def test_provider_specific_exceptions_inherit_shared_stt_bases() -> None:
    assert issubclass(FatalIflytekServerError, SttProviderFatalError)
    assert issubclass(RetriableIflytekServerError, SttProviderRetriableError)
    assert issubclass(FatalRealtimeServerError, SttProviderFatalError)
