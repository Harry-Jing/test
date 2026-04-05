# Translation

This document records the current translation-layer contract introduced in M6.

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

- Current cloud text translation providers are:
  - `deepl`
  - `google_cloud`
- DeepL credentials come from `DEEPL_AUTH_KEY`.
- Google Cloud Translation uses ADC plus `translation.providers.google_cloud.project_id`.
- LLM-based translation APIs are intentionally out of scope for this release.

## Rendering Contract

- `translation.output_mode = "source"` preserves the existing source-only chatbox behavior.
- `translation.output_mode = "target"` shows source preview while speaking, then replaces finalized entries with target-language text when translation completes.
- `translation.output_mode = "source_target"` renders one stacked source-target snapshot shaped as:

```text
source paragraph

target paragraph
```

- The runtime sends `source_paragraph + "\n\n" + target_paragraph` and relies on VRChat auto-wrap instead of explicitly splitting source and target into multiple lines.
- Pending translated finals remain visible in the source paragraph until translation completes.
- The target paragraph keeps the most recent completed translation history; pending finals do not clear it.
- `translation.chatbox_layout` controls the stacked bilingual heuristic:
  - `source_visible_lines`
  - `separator_blank_lines`
  - `target_visible_lines`
  - `visual_line_width_units`
  - `width_model`
  - `widths.*`
- The renderer first clips source and target paragraphs independently by visual-width budget, then applies a final `144`-character hard limit while keeping source and target budgets independent.
