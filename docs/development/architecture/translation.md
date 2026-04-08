# Translation

This document records the current translation-layer contract.

## Scope

- Translation starts from normalized `TranscriptRevisionEvent` values emitted by `stt`.
- The current release is text-to-text only; it does not perform speech-to-translation transport work.
- The current strategy is `final_only` translation.

## Owns

- Translation owns final-only request queueing, provider validation, worker lifecycle, and the source-versus-target history selection used by bilingual chatbox rendering.
- Translation must not block microphone capture, STT retry behavior, or OSC pacing.

## Runtime contract

- Partial transcript revisions continue to drive real-time source-language chatbox preview.
- Final transcript revisions can enqueue one translation request when `translation.enabled = true` and `translation.output_mode != "source"`.
- Translation runs in a separate bounded async queue.
- Queue pressure drops the oldest pending final translation request and keeps the newest request.
- Translation failures, timeouts, and dropped requests fall back to source-language final text with warning logs.

## Provider boundaries

- Current providers are `deepl`, `google_cloud`, and `translategemma_local`.
- DeepL credentials come from `DEEPL_AUTH_KEY`.
- Google Cloud Translation uses ADC plus `translation.providers.google_cloud.project_id`.
- Local TranslateGemma translation uses a repository-local websocket sidecar started by `vrc-live-caption local-translation serve`.
- Main-app config stores sidecar connection settings under `[translation.providers.translategemma_local]`. Sidecar model selection, device policy, dtype policy, and generation settings live under `[translation.providers.translategemma_local.sidecar]`.
- `local-translation serve` prints the resolved websocket endpoint, model, device and dtype policy, log file path, and an explicit ready line after the listener is accepting connections.
- The sidecar preloads the configured model before it reports `ready`, and `ready` includes model plus resolved device metadata so `doctor`, runtime logs, and the CLI ready line can distinguish cache, device, and dtype setup problems.

## Output modes

- `translation.output_mode = "source"` renders source text only in one single top zone sized by `translation.chatbox_layout.source_visible_lines`.
- `translation.output_mode = "target"` shows source preview while speaking, then replaces finalized entries with target-language text when translation completes inside that same single top zone.
- `translation.output_mode = "source_target"` renders one stacked source-target snapshot shaped as:

```text
source paragraph

target paragraph
```

- `translation.chatbox_layout` controls the stacked bilingual split with `source_visible_lines`, `separator_blank_lines`, and `target_visible_lines`. Their combined visible-line budget must stay within VRChat's `9`-line limit.

## `source_target` rendering contract

- The runtime sends `source_paragraph + "\n\n" + target_paragraph` and relies on VRChat auto-wrap instead of explicitly splitting source and target into multiple lines.
- Pending translated finals remain visible in the source paragraph until translation completes.
- The target paragraph keeps the most recent completed translation history; pending finals do not clear it.
- `source_target` keeps a strict wrapped-line budget even while translation is pending. Source does not borrow the reserved target area.
- The upper paragraph is built from two layers:
  - one source-only tail made from active partials plus finalized source entries that still have no translation result
  - one aligned bilingual history made from completed source/target utterance pairs
- Completed bilingual history is selected pair-aware:
  - keep the newest translated utterance pairs together
  - if the oldest kept pair does not fit, only that pair may be tail-clipped
  - source and target may clip to different depths while still representing the same pair
  - do not fill leftover space with unmatched older source-only or target-only history
- Finalized history is rendered sentence-first:
  - whole sentences are preferred over partial tails
  - primary sentence terminators are `。！？.!?`
  - one sentence alone may be clipped only after the renderer fails to keep it whole, and the clipped result preserves the sentence tail
- Wrap simulation is driven by the fixed VRChat TMP/font model from `docs/development/architecture/vrchat-chatbox-reference.md`, not by a configurable heuristic width model.
- After line-based selection, the final `144`-character budget is applied in this order:
  - reserve characters for the source-only tail and the separator first
  - share the remaining content budget only across the aligned translated pairs
  - allow unused budget to flow from one side to the other when one paragraph needs fewer characters
