# M2 STT Backend Plan (Archived)

Archived M2 design intent for STT boundaries and abstraction. This document is
kept for historical context and layering guidance. It is not the source of
truth for the current wire protocol or current runtime defaults.

## Enduring Design Direction

- M2 should introduce a dedicated `stt` layer rather than pushing provider
  logic into `audio` or `runtime`.
- STT backends should be split by concrete protocol and product behavior, not
  only by vendor name.
- Provider adapters should own authentication, transport, audio submission, and
  provider-specific normalization.
- Upper layers should consume a provider-neutral session interface and a
  provider-neutral event model.

## Layer Boundaries

- `audio/` owns microphone capture and raw audio chunks
- `runtime/` owns queues, lifecycle, and stop behavior
- `stt/` owns backend registration, sessions, normalization, and status events
- `pipeline/` or app-level orchestration owns wiring runtime audio into an STT
  session

Rules:

- STT logic does not move into `audio/` or `runtime/`
- provider configuration and transport stay inside `stt/`
- upper layers depend on the unified STT contract rather than provider events

## Session Contract Direction

The STT abstraction should stay transport-neutral and cover:

- opening and starting a session
- continuously submitting audio
- polling normalized events
- health checks
- bounded shutdown

The public session contract should not expose provider transport details such as
WebSocket headers, gRPC types, or provider-specific request payloads.

## Event Model Direction

The normalized event model should continue to describe:

- utterance identity
- revision number
- full current text
- final vs non-final state
- status and error signals needed by higher layers

Adapters may absorb provider-specific delta, append, or replace semantics
internally, but upper layers should continue to consume revision-based partial
and final events.

## Milestone Boundaries

M2 is responsible for:

- cloud STT integration
- a unified session contract
- normalized partial and final transcript events
- basic status, error, and diagnostic signals

M3 is responsible for:

- stable and unstable text handling
- revision deduplication and flush rules
- VRChat-facing output behavior

M5 is responsible for:

- local STT backends
- local model initialization, cache, and degradation behavior

M5 should reuse the unified STT abstraction introduced in M2 rather than
introducing a separate local-only interface.

## Status

For current protocol conclusions and implementation facts, use
`./m2-stt-development-summary.md`.
