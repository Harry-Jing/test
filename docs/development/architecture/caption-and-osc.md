# Caption And OSC

This document records the current caption stabilization and OSC output contract.
It remains the source of truth for VRChat-facing text behavior.

## Scope

- This document starts from normalized `TranscriptRevisionEvent` values emitted by `stt`.
- It owns caption stabilization, chatbox text shaping, pacing, typing-state handling, OSC output, and the related CLI diagnostics.
- STT providers remain revision-based and do not absorb VRChat-specific logic.

## Config Contract

- `osc.host` defaults to `127.0.0.1`
- `osc.port` defaults to `9000`
- `osc.notification_sfx` defaults to `false`
- `doctor` validates OSC config loading and prints the resolved OSC target
- `osc-test` sends a one-off chatbox message without requiring STT credentials

## Chatbox Constraints

- VRChat chatbox text is limited to `144` characters
- VRChat displays at most `9` lines, counting both explicit newlines and automatic wrapping
- Chatbox layout uses the fixed VRChat TMP model documented in `architecture/vrchat-chatbox-reference.md`: `280px` usable width, `fontSize = 18`, `NotoSans-Regular`, `NotoSansCJK-JP-Regular`, and TMP leading/following line-break tables
- `architecture/vrchat-chatbox-reference.md` is the canonical chatbox-wrap reference; `architecture/VRChat_ChatBox_Final_Report.md` remains the archival reverse-engineering record
- pacing tolerance beyond the documented text and line limits is treated as an empirical behavior that may need further tuning

## Stabilization And Layout

- revisions are accepted only when they advance the current utterance revision
- duplicate non-final text for the same utterance is ignored
- the active utterance keeps a monotonic stable prefix based on the longest common prefix between consecutive revisions
- when a new utterance arrives before the previous utterance is final, the previous utterance commits only its current stable prefix and drops the unstable tail
- final events commit the full final text and clear the active utterance
- output keeps VRChat auto-wrap in control inside each visible zone; it does not insert manual in-zone newlines
- visible history is sentence-aware:
  - finalized history is segmented by primary sentence terminators such as `。！？.!?`
  - if the next oldest whole sentence does not fit the visible zone, that sentence is dropped entirely
  - if one sentence alone exceeds the zone budget, the renderer keeps the sentence tail and prefers a legal TMP/UAX break before hard clipping
- when translated output is enabled:
  - pending translated finals stay source-only until translation completes
  - `source` mode renders one single top zone using `translation.chatbox_layout.source_visible_lines`
  - `target` mode also renders one single top zone and uses translated finals when they are available, while active partial preview stays source-language
  - `source_target` mode always reserves a strict `source_visible_lines + separator_blank_lines + target_visible_lines` layout and renders `source_paragraph\n\ntarget_paragraph`
  - `source_target` keeps untranslated source as a source-only tail in the upper zone, then selects completed translated utterance pairs together so the visible source and target history cover the same pair range
  - when the oldest visible pair does not fit, `source_target` may tail-clip that pair asymmetrically inside the pair, but it does not fill leftover space with unmatched older history from only one side
  - after line-based selection, `source_target` reserves character budget for the source-only tail and separator first, then lets the remaining pair budget flow between source and target if one side needs fewer characters

## Pacing And CLI Behavior

- pending partial text is coalesced to a single latest snapshot
- final snapshots replace pending partial snapshots
- partial text is paced to at most one send every `1.5s`
- final text can bypass the partial interval, but still waits for a short `0.3s` anti-burst guard since the previous OSC send
- typing state is best-effort and lower priority than text sends
- typing turns on when partial activity starts and turns off on final commit or after `1.5s` without new partial activity
- `run` emits `[chatbox] ...` preview lines for text that was actually sent to the OSC target
- `run` keeps `[status] ...` lines for STT lifecycle visibility
- shutdown performs a best-effort flush for up to `1s`, then tries to send `typing=false`
