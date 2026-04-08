# STT

This document records the current STT contract.

## Scope

- This document covers backend selection, session runner lifecycle, retry behavior, connection-attempt boundaries, and normalized events.
- Capture ownership and CLI composition live in `architecture/runtime.md`.
- STT remains a dedicated layer between runtime audio and provider transport; provider auth, transport, pacing, and normalization stay inside `stt` rather than `audio` or `runtime`.

## Top-Level Model

- The CLI selects one configured `SttBackend`.
- `AsyncSttSessionRunner` owns the long-lived session lifecycle for one `run`.
- The runner creates a fresh provider-specific connection attempt for each connect or reconnect.
- Provider mutable state that only belongs to one connection attempt must never be stored on the long-lived runner or backend object.

## Core Types

- `SttBackend` describes one configured provider and exposes:
  - backend description
  - status messages
  - retriable-error classification
  - attempt creation
  - exhausted-error construction
- `AttemptContext` provides the shared resources for one connection attempt:
  - the single shared audio queue
  - event publishing callback
  - ready callback
  - stop event
  - connect timeout
- `ConnectionAttempt` runs one end-to-end transport attempt until stop, disconnect, or failure.

## Event Contract

- Providers normalize transport-specific messages into provider-neutral events.
- The current normalized event surface is:
  - `TranscriptRevisionEvent`
  - `SttStatusEvent`
- Transcript revisions remain utterance-based and revision-based.
- Provider raw websocket or protocol events must not leak into pipeline code.

## Retry Contract

- The runner emits one `connecting` status before the first attempt.
- Retriable failures emit `retrying` with attempt count and backoff.
- Non-retriable failures surface as `error` and terminate the runner.
- Retry timing is controlled by `[stt.retry]`:
  - `connect_timeout_seconds`
  - `max_attempts`
  - `initial_backoff_seconds`
  - `max_backoff_seconds`

## Provider Boundaries

### OpenAI Realtime

- `OpenAIRealtimeBackend` validates capture shape for mono `int16` audio.
- Each attempt initializes transcription mode with `session.update` and does not publish `ready` before the provider acknowledges the session configuration.
- Each attempt creates a fresh `OpenAIConnectionState`.
- Attempt-scoped state includes:
  - utterance map
  - PCM16 resampler
- The backend resamples internally to `24000 Hz` mono `pcm16` before sending audio.

### iFLYTEK RTASR

- `IflytekRtasrBackend` validates capture shape for `16000 Hz`, mono, `int16` audio.
- Each attempt creates a fresh `IflytekConnectionState`.
- Attempt-scoped state includes:
  - utterance map
  - audio chunker
  - session id
  - paced-send timing fields
  - end-of-stream flags
- Provider-specific authentication, paced chunk sending, and the final end-of-stream close message stay inside the backend.

### FunASR Local Sidecar

- `FunasrLocalBackend` validates capture shape for `16000 Hz`, mono, `int16` audio.
- The backend connects to a repository-local websocket sidecar started by `vrc-live-caption local-stt serve`.
- Main-app config stores sidecar connection settings under `[stt.providers.funasr_local]`.
- Model selection, device policy, chunking, VAD, punctuation, and runtime thread settings live under `[stt.providers.funasr_local.sidecar]`.
- Each attempt creates a fresh `FunasrLocalConnectionState`.
- Attempt-scoped state includes:
  - segment-to-revision tracking for normalized utterances
- The sidecar protocol is internal to the repository:
  - client sends `start`, raw PCM16 audio frames, then `stop`
  - server emits `ready`, `transcript`, and `error`
  - `ready` includes the resolved sidecar device metadata so `doctor` and runtime logs can distinguish `cpu` from `cuda:0`
- Sidecar `online` and `offline` transcript phases are normalized into the shared `TranscriptRevisionEvent` surface:
  - online phase emits `is_final = false`
  - offline phase emits the final revision with `is_final = true`

## Queue Ownership Rule

- STT no longer owns a second audio input queue.
- Connection attempts read directly from the single pipeline-owned audio queue.
- STT still owns its own bounded event queue for normalized events emitted back to the pipeline controller.

## Shutdown Rule

- Closing the runner sets the shared stop event and emits `closing`.
- The pipeline closes the shared audio queue after capture stops.
- Provider attempts must treat queue closure or stop-requested as the trigger for final flush / end-of-stream behavior.
- The runner emits `closed` after the attempt loop exits.
- External task cancellation during CLI interrupt shutdown is not emitted as STT `error`.
