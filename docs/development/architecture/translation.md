# Translation

This document records the current translation-layer contract.

## Scope

- Translation starts from normalized `TranscriptRevisionEvent` values emitted by `stt`.
- The first release is text-to-text only; it does not perform speech-to-translation transport work.
- The first release supports only `final_only` translation.

## Runtime Contract

- Partial transcript revisions continue to drive real-time source-language chatbox preview.
- Final transcript revisions can enqueue one translation request when `translation.enabled = true` and `translation.output_mode != "source"`.
- Translation runs in a separate bounded async queue and must not block microphone capture, STT, or OSC pacing.
- Queue pressure drops the oldest pending final translation request and keeps the newest request.
- Translation failures, timeouts, and dropped requests fall back to source-language final text with warning logs.

## Provider Contract

- Current text translation providers are:
  - `deepl`
  - `google_cloud`
  - `translategemma_local`
- DeepL credentials come from `DEEPL_AUTH_KEY`.
- Google Cloud Translation uses ADC plus `translation.providers.google_cloud.project_id`.
- Local TranslateGemma translation uses a repository-local websocket sidecar started by `vrc-live-caption local-translation serve`.
- Main-app config stores only sidecar connection settings under `[translation.providers.translategemma_local]`.
- Sidecar model selection, device policy, dtype policy, and generation settings live in `local-translation-translategemma.toml`.
- The sidecar preloads the configured model before it reports `ready`.
- `ready` includes sidecar model and resolved device metadata so `doctor` and runtime logs can distinguish cache, device, and dtype setup problems.

## Rendering Contract

- `translation.output_mode = "source"` renders source text only in one single top zone sized by `translation.chatbox_layout.source_visible_lines`.
- `translation.output_mode = "target"` shows source preview while speaking, then replaces finalized entries with target-language text when translation completes inside that same single top zone.
- `translation.output_mode = "source_target"` renders one stacked source-target snapshot shaped as:

```text
source paragraph

target paragraph
```

- The runtime sends `source_paragraph + "\n\n" + target_paragraph` and relies on VRChat auto-wrap instead of explicitly splitting source and target into multiple lines.
- Pending translated finals remain visible in the source paragraph until translation completes.
- The target paragraph keeps the most recent completed translation history; pending finals do not clear it.
- `source_target` keeps a strict wrapped-line budget even while translation is pending; source does not borrow the reserved target area.
- `source_target` builds the upper paragraph from two layers:
  - one source-only tail made from active partials plus finalized source entries that still have no translation result
  - one aligned bilingual history made from completed source/target utterance pairs
- Completed bilingual history is selected pair-aware:
  - the renderer greedily keeps the newest translated utterance pairs together
  - if the oldest kept pair does not fit, only that pair may be tail-clipped, and source/target may clip to different depths while still representing the same pair
  - the renderer does not fill leftover space with unmatched older source-only or target-only history
- `translation.chatbox_layout` controls only the stacked bilingual line split:
  - `source_visible_lines`
  - `separator_blank_lines`
  - `target_visible_lines`
- Finalized history is rendered sentence-first:
  - whole sentences are preferred over partial tails
  - primary sentence terminators are `。！？.!?`
  - one sentence alone may be clipped only after the renderer fails to keep it whole, and the clipped result preserves the sentence tail
- Wrap simulation is driven by the fixed VRChat TMP/font model from `docs/development/architecture/vrchat-chatbox-reference.md`, not by a configurable heuristic width model.
- The renderer first decides the source-only tail and the aligned translated pairs by the real wrap simulator, then applies the final `144`-character hard limit:
  - source-only tail characters and the separator are reserved first
  - the remaining character budget is shared only by the aligned translated pairs, with unused budget still allowed to flow to the other side
