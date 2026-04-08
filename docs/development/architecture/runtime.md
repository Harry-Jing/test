# Runtime

This document records the current runtime contract for the async capture-to-caption pipeline.

## Scope

- Windows native execution remains the primary validation target.
- This document covers microphone capture, queue ownership, pipeline orchestration, diagnostics, and debug recording.
- Provider transport details live in `architecture/stt.md`.

## CLI Contract

- `vrc-live-caption devices` lists input devices with index, default marker, maximum input channels, and default sample rate.
- `vrc-live-caption doctor` validates config loading, resolves the configured input device, and runs a short stream probe.
- `vrc-live-caption run` starts the async live pipeline and prints normalized STT status plus chatbox preview lines.
- `vrc-live-caption record-sample` records a WAV file using the same capture service and defaults to 10 seconds.
- `doctor`, `run`, and `record-sample` accept `--console-log-level` and `--file-log-level` overrides for one-off troubleshooting.

## Configuration Contract

- The default runtime config file is `vrc-live-caption.toml` in the repository root.
- The tracked template is `vrc-live-caption.toml.example`; the live config stays untracked.
- `vrc-live-caption.toml` is the only ordinary config file; `.env` remains secrets-only.
- Local sidecar runtime settings are embedded in the main config rather than split into separate TOML files.
- Capture defaults are:
  - Sample rate: `16000`
  - Channels: `1`
  - Dtype: `int16`
  - Block duration: `100 ms`
- Pipeline defaults are:
  - Audio buffer size: `50`
  - Event buffer size: `200`
  - Shutdown timeout: `5.0 s`
  - Heartbeat interval: `5 s`
- Logging defaults are:
  - Console level: `WARNING`
  - File level: `INFO`
- Runtime logs default to `.runtime/logs/vrc-live-caption.log`.
- Debug recordings default to `.runtime/recordings/`.

## Capture And Queue Contract

- Input capture uses `sounddevice.RawInputStream`.
- The sounddevice callback only copies bytes, timestamps the chunk, increments the sequence number, and bridges into the asyncio loop with `loop.call_soon_threadsafe(...)`.
- The pipeline owns the single bounded audio queue.
- When the queue is full, the pipeline drops the oldest chunk and keeps the newest chunk.
- The audio capture service does not own consumer lifecycle, STT transport lifecycle, or transcript output.

## Pipeline Ownership

- `LivePipelineController` is the only owner of:
  - capture startup and shutdown
  - STT runner startup, retry lifecycle, and shutdown
  - transcript event dispatch
  - chatbox output startup and flush
  - heartbeat logging and shutdown ordering
- `run` executes under one `asyncio.run(...)` call.
- `Ctrl+C` is handled as a bounded graceful shutdown: stop capture, close the shared audio queue, close the STT runner, drain remaining status events, then flush transcript output.
- The first `Ctrl+C` requests graceful shutdown and is not reported as a subsystem failure.
- The first `Ctrl+C` immediately prints a CLI hint that shutdown has started and that a second `Ctrl+C` will force exit.
- A second `Ctrl+C` while shutdown is already in progress aborts the wait immediately and falls back to hard process exit.

## Application Boundary

- The main application owns capture, runtime orchestration, caption flow, translation flow, and VRChat output composition.
- Local model inference details stay behind provider or sidecar boundaries rather than moving into runtime internals.
- The GUI, when present, wraps the same config, logging, diagnostics, and runtime controls; it does not replace the core pipeline contract.

## Internal Package Boundaries

- `vrc_live_caption.audio` owns device discovery, backend protocols, and the `sounddevice` adapter.
- `vrc_live_caption.runtime` owns capture services, bounded async queues, and recording sinks.
- `vrc_live_caption.stt` owns backend selection, runner lifecycle, provider connection attempts, and transcript normalization.
- `vrc_live_caption.translation` owns final-only text translation providers, bounded request queues, and translation environment validation.
- `vrc_live_caption.chatbox` owns stabilization, pacing, and OSC-facing output state.
- `vrc_live_caption.cli` composes validated config, secrets, logging, and the top-level pipeline controller; it does not own runtime internals.

## Failure Boundaries

- Expected application-level failures inherit from `VrcLiveCaptionError`.
- Each subsystem surfaces boundary exceptions such as `ConfigError`, `SecretError`, `AudioBackendError`, `AudioRuntimeError`, `SttSessionError`, and `PipelineError`.
- `cli` reports those boundary exceptions as concise user-facing errors; unexpected exceptions are treated as unhandled faults and keep full logs.

## Diagnostics

- Heartbeat logs are emitted by the pipeline controller rather than by capture internals.
- Heartbeat entries include the resolved device label, audio queue depth, audio drops, and dropped STT event count.
- When translated output is enabled, heartbeat entries also include translation queue depth plus dropped, failed, and stale translation counts.
- Logs avoid printing raw audio content and focus on device, queue, state, and error context.
