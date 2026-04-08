# Caption and OSC

This document records the current caption stabilization and OSC output contract.

## Scope

- This document starts from normalized `TranscriptRevisionEvent` values emitted by `stt`.
- It owns caption stabilization, chatbox text shaping, pacing, typing-state handling, OSC output, and the related CLI diagnostics.
- STT providers remain revision-based and do not absorb VRChat-specific logic.

## Owns

- Chatbox-facing text limits, stabilization, pacing, typing-state toggles, and OSC sends belong to this layer.
- Translation-specific bilingual layout selection stays in `architecture/translation.md`.

## Chatbox constraints

- `osc.host` defaults to `127.0.0.1`, `osc.port` defaults to `9000`, and `osc.notification_sfx` defaults to `false`.
- `doctor` validates OSC config loading and prints the resolved OSC target.
- `osc-test` sends a one-off chatbox message without requiring STT credentials.
- VRChat chatbox output is limited to `144` characters and at most `9` visible lines.
- Wrapping and clipping follow the fixed model documented in [VRChat Chatbox Reference](./vrchat-chatbox-reference.md).
- Finalized history is sentence-aware:
  - it is segmented by primary sentence terminators such as `。！？.!?`
  - if the next oldest whole sentence does not fit the visible zone, that sentence is dropped entirely
  - if one sentence alone exceeds the zone budget, the renderer keeps the sentence tail and prefers a legal TMP/UAX break before hard clipping
- When translation is enabled, this layer renders the source-only or bilingual history selected by the translation layer.

## Stabilization contract

- Revisions are accepted only when they advance the current utterance revision.
- Duplicate non-final text for the same utterance is ignored.
- The active utterance keeps a monotonic stable prefix based on the longest common prefix between consecutive revisions.
- When a new utterance arrives before the previous utterance is final, the previous utterance commits only its current stable prefix and drops the unstable tail.
- Final events commit the full final text and clear the active utterance.
- Output keeps VRChat auto-wrap in control inside each visible zone; it does not insert manual in-zone newlines.

## Pacing and CLI behavior

- Pending partial text is coalesced to the latest snapshot.
- Final snapshots replace pending partial snapshots.
- Partial text is paced to at most one send every `1.5s`.
- Final text can bypass the partial interval, but it still waits for a short `0.3s` anti-burst guard since the previous OSC send.
- Typing state is best-effort and lower priority than text sends. It turns on during partial activity and turns off on final commit or after `1.5s` without new partial activity.
- `run` emits `[chatbox] ...` preview lines only for text that was actually sent to the OSC target.
- `run` keeps `[status] ...` lines for STT lifecycle visibility.
- Shutdown performs a best-effort flush for up to `1s`, then tries to send `typing=false`.
