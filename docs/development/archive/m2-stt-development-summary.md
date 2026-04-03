# M2 STT Development Summary (Archived)

Archived protocol and implementation findings confirmed during M2 development.
This document is kept for historical reference and is not the source of truth for current runtime defaults.

## Protocol Findings

- iFLYTEK RTASR currently connects to
  `wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1`
- the iFLYTEK websocket handshake is authenticated with query parameters rather
  than headers
- the iFLYTEK signature is built by sorting all non-signature query parameters,
  URL-encoding each key and value, applying `HMAC-SHA1`, and Base64-encoding the
  digest
- iFLYTEK realtime audio currently requires `16kHz` mono `pcm16`
- iFLYTEK expects audio pacing close to `40ms / 1280 bytes` and an explicit
  `{"end": true, "sessionId": ...}` close message
- iFLYTEK result payloads are normalized from `data.seg_id` plus
  `data.cn.st.rt[*].ws[*]`, selecting one non-empty candidate word per `ws`
- OpenAI realtime transcription currently connects to
  `wss://api.openai.com/v1/realtime?intent=transcription`
- the server first returns `session.created`
- the client must initialize with `session.update`, not
  `transcription_session.update`
- the update payload must include `session.type = "transcription"`
- input audio format must be configured at `session.audio.input.format` as an
  object such as `{"type": "audio/pcm", "rate": 24000}`
- transcription model settings must be configured at
  `session.audio.input.transcription`
- the current default transcription model is `gpt-4o-transcribe`
- the session should not publish `ready` before `session.updated`

## Runtime Findings

- the project runtime continues to emit `16kHz` mono `pcm16`
- the iFLYTEK backend keeps runtime audio at `16kHz` and performs provider-side
  frame pacing plus `100ms -> 40ms` chunk splitting internally
- the OpenAI backend resamples internally to `24kHz` mono `pcm16` before
  sending audio to the realtime API
- normalized output stays revision-based and exposes partial and final events
  without leaking raw provider events
- the iFLYTEK backend publishes `ready` only after a provider session id is
  available and cached for the final `end` message
- when `server_vad` is enabled, shutdown must not force an
  `input_audio_buffer.commit` on an empty buffer, or the server returns
  `buffer too small`
- the cloud providers now share an internal threaded session scaffold for
  lifecycle management, error propagation, and close semantics
- reconnect handling plus `connecting` / `retrying` status publication now
  flows through a shared helper, while provider-specific `ready` conditions stay
  local to each backend

## Verification Status

- the protocol details above were confirmed by wire-level validation, unit
  tests, and provider-specific session tests
- `tests/fixtures/audio/test.wav` has been validated through an opt-in
  integration test path for both cloud providers
- related checks currently pass in unit tests plus the default non-live
  verification flow; live tests remain opt-in
