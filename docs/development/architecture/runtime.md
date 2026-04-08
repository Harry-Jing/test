# Runtime

This document records the current runtime contract for the async capture-to-caption pipeline.

## Scope

- Windows native execution remains the primary validation target.
- This document covers microphone capture, queue ownership, pipeline orchestration, diagnostics, and debug recording.
- Provider transport details live in `architecture/stt.md`.

## Owns

- Input capture uses `sounddevice.RawInputStream`.
- The sounddevice callback only copies bytes, timestamps the chunk, increments the sequence number, and bridges into the asyncio loop with `loop.call_soon_threadsafe(...)`.
- The pipeline owns the single bounded audio queue and drops the oldest chunk when the queue is full.
- The audio capture service does not own consumer lifecycle, STT transport lifecycle, or transcript output.
- `LivePipelineController` owns capture startup and shutdown, STT runner lifecycle, transcript event dispatch, translation lifecycle, chatbox output lifecycle, heartbeat logging, and shutdown ordering.
- The main application owns capture, runtime orchestration, caption flow, translation flow, and VRChat output composition. The GUI, when present, stays a thin wrapper over that same contract.

## CLI contract

- `vrc-live-caption devices` lists input devices with index, default marker, maximum input channels, and default sample rate.
- `vrc-live-caption doctor` validates config loading, resolves the configured input device, runs a short input-stream probe, checks OSC config, and verifies either backend secrets or repository-local sidecar reachability.
- `vrc-live-caption run` prints a startup summary before provider validation or sidecar connection work begins. That summary includes the config path, input device selector, OSC target, STT backend, translation summary, local sidecar endpoints when needed, and the log file path.
- `vrc-live-caption run` prints `[ok] Runtime ready: ...` only after the async pipeline is fully started, then continues with normalized STT status lines plus chatbox preview lines.
- `vrc-live-caption record-sample` records a WAV file using the same capture service as `run`, defaults to 10 seconds, and prints both the resolved config path and output WAV path before recording starts.
- `vrc-live-caption local-stt serve` prints config source, websocket endpoint, device policy, configured models, log file path, and an explicit sidecar-ready line once the websocket listener is bound and usable.
- `vrc-live-caption local-translation serve` prints config source, websocket endpoint, model, device and dtype policy, log file path, and an explicit sidecar-ready line once the websocket listener is bound and usable.
- `doctor`, `run`, and `record-sample` accept `--console-log-level` and `--file-log-level` overrides for one-off troubleshooting.

## Key defaults

- The default runtime config file is `vrc-live-caption.toml` in the repository root.
- The tracked template is `vrc-live-caption.toml.example`; the live config stays untracked.
- `vrc-live-caption.toml` is the only ordinary config file; `.env` remains secrets-only.
- Local sidecar runtime settings stay embedded in the main config rather than split into separate TOML files.
- Capture defaults are `16000 Hz`, `1` channel, `int16`, and `100 ms` blocks.
- Pipeline defaults are `50` audio chunks, `200` event items, `5.0 s` shutdown timeout, and `5 s` heartbeat interval.
- Logging defaults are console `WARNING`, file `INFO`, and `.runtime/logs/vrc-live-caption.log`.
- Debug recordings default to `.runtime/recordings/`.

## Boundaries

- Local model inference details stay behind provider or sidecar boundaries rather than leaking into runtime internals.
- Internal package boundaries remain:
  - `vrc_live_caption.audio`: device discovery, backend protocols, and the `sounddevice` adapter
  - `vrc_live_caption.runtime`: capture services, bounded async queues, and recording sinks
  - `vrc_live_caption.stt`: backend selection, runner lifecycle, provider connection attempts, and transcript normalization
  - `vrc_live_caption.translation`: final-only text translation providers, bounded request queues, and translation runtime validation
  - `vrc_live_caption.chatbox`: stabilization, pacing, and OSC-facing output state
  - `vrc_live_caption.cli`: config, secrets, logging, and top-level pipeline composition
- Expected application-level failures inherit from `VrcLiveCaptionError`.
- Common boundary exceptions include `ConfigError`, `SecretError`, `AudioBackendError`, `AudioRuntimeError`, `SttSessionError`, and `PipelineError`.

## Shutdown and diagnostics

- `run` executes under one `asyncio.run(...)` call.
- `Ctrl+C` requests bounded graceful shutdown: stop capture, close the shared audio queue, close the STT runner, drain remaining status events, then flush transcript output.
- The first `Ctrl+C` requests graceful shutdown, is not reported as a subsystem failure, and prints a hint that a second `Ctrl+C` will force exit.
- A second `Ctrl+C` while shutdown is already in progress aborts the wait and falls back to hard process exit.
- Heartbeat logging is emitted by the pipeline controller rather than by capture internals.
- Heartbeat entries include the resolved device label, audio queue depth, audio drops, and dropped STT event count. When translation is enabled they also include translation queue depth plus dropped, failed, and stale translation counts.
- Logs avoid printing raw audio content and focus on device, queue, state, and error context.
