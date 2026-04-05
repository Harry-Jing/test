# Caption And OSC

This document records the current caption stabilization and OSC output contract.
It originated in M3 and remains the source of truth for VRChat-facing text behavior.

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
- This document does not try to predict VRChat word wrap; it only controls explicit newlines and character budgets
- pacing tolerance beyond the documented text and line limits is treated as an empirical behavior that may need further tuning

## Stabilization And Layout

- revisions are accepted only when they advance the current utterance revision
- duplicate non-final text for the same utterance is ignored
- the active utterance keeps a monotonic stable prefix based on the longest common prefix between consecutive revisions
- when a new utterance arrives before the previous utterance is final, the previous utterance commits only its current stable prefix and drops the unstable tail
- final events commit the full final text and clear the active utterance
- output uses a single-line rolling layout:
  - committed history stays on the left
  - the active utterance continues on the right in the same line
- This document does not emit explicit newlines for layout control
- the merged chatbox text is clipped from the left to stay within `144` characters, preserving the newest tail of the line
- when translated output is enabled:
  - pending translated finals stay source-only until translation completes
  - `target` mode replaces finalized entries with translated text once translation completes
  - `source_target` mode renders `source_paragraph\n\ntarget_paragraph`
  - `source_target` uses a stacked two-zone heuristic: source and target are clipped independently by visual-width estimates, the blank separator line is explicit, and the final OSC payload still stays within `144` characters

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
