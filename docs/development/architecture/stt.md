# STT

This document records the current STT contract.

## Scope

- This document covers backend selection, session runner lifecycle, retry behavior, connection-attempt boundaries, and normalized events.
- Capture ownership and CLI composition live in `architecture/runtime.md`.
- STT remains a dedicated layer between runtime audio and provider transport; provider auth, transport, pacing, and normalization stay inside `stt` rather than `audio` or `runtime`.

## Owns

- The CLI selects one configured `SttBackend`.
- `AsyncSttSessionRunner` owns the long-lived session lifecycle for one `run`.
- The runner creates a fresh provider-specific connection attempt for each connect or reconnect.
- Provider mutable state that belongs to one connection attempt must not be stored on the long-lived runner or backend object.

## Core abstractions

- `SttBackend` describes one configured provider and exposes backend description, status messages, retriable-error classification, attempt creation, and exhausted-error construction.
- `AttemptContext` provides the shared resources for one connection attempt: the single shared audio queue, event publishing callback, ready callback, stop event, and connect timeout.
- `ConnectionAttempt` runs one provider-specific transport attempt until stop, disconnect, or failure.

## Status and event contract

- Providers normalize transport-specific messages into the shared event surface:
  - `TranscriptRevisionEvent`: one utterance-based transcript revision with `utterance_id`, `revision`, `text`, and `is_final`
  - `SttStatusEvent`: one normalized transport or lifecycle update with a `status` plus human-readable `message`
- Provider raw websocket or protocol events must not leak into pipeline code.
- The runner emits one `connecting` status before the first attempt.
- The first successful ready edge emits `ready`.
- Retriable failures emit `retrying` with attempt count and backoff.
- Non-retriable failures surface as `error` and terminate the runner.
- Shutdown emits `closing` when the stop flow begins and `closed` after the attempt loop exits.
- Retry timing is controlled by `[stt.retry]`: `connect_timeout_seconds`, `max_attempts`, `initial_backoff_seconds`, and `max_backoff_seconds`.

## Provider boundaries

### OpenAI realtime

- `OpenAIRealtimeBackend` validates mono `int16` capture.
- Each attempt initializes transcription mode with `session.update` and does not publish `ready` before the provider acknowledges session configuration.
- The backend resamples internally to `24000 Hz` mono `pcm16` before sending audio.
- Attempt-scoped state includes session configuration, the PCM16 resampler, utterance bookkeeping, and any websocket lifecycle objects. Rebuild it on every reconnect.

### iFLYTEK RTASR

- `IflytekRtasrBackend` validates `16000 Hz`, mono, `int16` capture.
- Provider-specific authentication, paced chunk sending, and the final end-of-stream close message stay inside the backend.
- Attempt-scoped state includes the authenticated websocket request, paced audio chunker, session bookkeeping, end-of-stream flags, and close timing. Rebuild it on every reconnect.

### FunASR local sidecar

- `FunasrLocalBackend` validates `16000 Hz`, mono, `int16` capture and connects to a repository-local websocket sidecar started by `vrc-live-caption local-stt serve`.
- Main-app config stores sidecar connection settings under `[stt.providers.funasr_local]`. Model selection, device policy, chunking, VAD, punctuation, and runtime thread settings live under `[stt.providers.funasr_local.sidecar]`.
- `local-stt serve` prints the resolved websocket endpoint, configured model names, device policy, log file path, and an explicit ready line after the listener is accepting connections.
- The sidecar protocol is internal to the repository:
  - client sends `start`, raw PCM16 audio frames, then `stop`
  - server emits `ready`, `transcript`, and `error`
  - `ready` includes the resolved sidecar device metadata so `doctor`, runtime logs, and the CLI ready line can distinguish `cpu` from `cuda:0`
- Sidecar `online` and `offline` transcript phases normalize into the shared `TranscriptRevisionEvent` surface:
  - online phase emits `is_final = false`
  - offline phase emits the final revision with `is_final = true`
- Attempt-scoped state includes websocket connection objects, segment-to-revision tracking, and the online-to-offline finalization mapping. Rebuild it on every reconnect.

## Queue and shutdown rules

- STT does not own a second audio input queue.
- Connection attempts read directly from the single pipeline-owned audio queue.
- STT still owns its own bounded event queue for normalized events emitted back to the pipeline controller.
- Closing the runner sets the shared stop event and emits `closing`.
- The pipeline closes the shared audio queue after capture stops.
- Provider attempts must treat queue closure or stop-requested as the trigger for final flush or end-of-stream behavior.
- The runner emits `closed` after the attempt loop exits.
- External task cancellation during CLI interrupt shutdown is not emitted as an STT `error`.
