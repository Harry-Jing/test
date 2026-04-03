# VRChat Chatbox Width Investigation

This brief summarizes the current findings about VRChat chatbox visible capacity
for Chinese and mixed-language text. It is meant to be shared with another AI or
engineer for further analysis.

## Scope

- Date of summary: `2026-04-01`
- Context: `VRC Live Caption` currently sends chatbox text over OSC and clips
  by character count only.
- Goal: understand why pure Chinese text appears to show fewer visible
  characters than mixed Chinese-English text.

## Confirmed Facts

- VRChat official docs state:
  - chatbox text is limited to `144` characters
  - chatbox displays at most `9` lines
  - automatic word wrap counts toward the `9` line limit
- Current repo implementation clips merged chatbox text to the newest `144`
  Python characters and does not estimate visual width or wrap.
- Relevant repo files:
  - `docs/official/VRC/OSC as Input Controller.md`
  - `docs/development/architecture/caption-and-osc.md`
  - `src/vrc_live_caption/chatbox.py`

## Problem Statement

- Pure Chinese text does not appear to use the full visible `144` characters in
  VRChat chatbox.
- Mixed English text can show more visible characters.
- This suggests the practical display limit is driven by automatic wrapping and
  character width, not only by the OSC transport limit.

## User Measurements

Manual testing produced these practical one-line capacities:

- `15` repetitions of `U+4E2D` per line
- `14` Chinese characters + `2` lowercase `x`
- `13` Chinese characters + `4` lowercase `x`
- `26` uppercase `X`
- `29` lowercase `x`

Derived observation:

- Pure Chinese text without punctuation is likely limited to about `9 * 15 =
  135` visible characters before the `9`-line display cap is reached, even
  though VRChat still accepts up to `144` characters.

Additional screenshot-based observations:

- Lowercase `i` is much narrower than lowercase `x`
- Uppercase `X` is wider than lowercase `x`
- Mixed Chinese-English lines roughly follow an additive width model
- Spaces and punctuation affect wrapping behavior and must be measured

## Working Interpretation

The current evidence fits a proportional-width line budget model better than a
simple "Chinese counts as 2, English counts as 1" rule.

If lowercase `x` is treated as width `1.0`, a rough first-pass estimate is:

- `U+4E2D ~= 29 / 15 ~= 1.93`
- `X ~= 29 / 26 ~= 1.12`

This suggests one visual line is approximately equal to the width of about `29`
lowercase `x` characters.

## Recommended Modeling Direction

- Keep the hard transport limit at `<= 144` characters.
- Add a second, visual-width-based safety layer that estimates wrap and tries to
  stay within `<= 9` lines.
- Continue using black-box testing inside VRChat as the primary source of truth.
- Do not hardcode the final rule as:
  - "all Chinese -> 135"
  - "otherwise -> 144"
- A short-term fallback could expose a configurable safe budget for CJK-heavy
  text, but that should be treated as a temporary workaround.

## Font File Question

Finding the VRChat font file may help extract glyph advance widths, but it is
not sufficient on its own because practical layout also depends on:

- the real chatbox container width
- possible fallback fonts for CJK glyphs
- the final text rendering and wrapping behavior used by VRChat or Unity

Conclusion:

- font inspection is useful as a helper
- in-client measurement is still required

## Highest-Value Next Tests

Measure one-line capacity for:

- `i`, `l`, `1`
- `m`, `w`, `M`, `W`
- `0`, `8`
- ASCII punctuation: `, . : ; - _`
- full-width punctuation such as comma, period, exclamation mark, question
  mark, colon, and parentheses
- half-width space and full-width space

Measure mixed-pair behavior for:

- `U+4E2D + i`
- `U+4E2D + m`
- `U+4E2D + X`
- `U+4E2D + 1`
- `U+4E2D + full-width comma`
- `U+4E2D + period`

For every test, record:

- exact sent string
- maximum visible characters per line
- total visible characters before the chatbox hits `9` lines
- whether the text used half-width or full-width punctuation or spaces

## Summary For Another AI

Please analyze VRChat chatbox layout as a two-layer constraint problem:

1. Hard send limit: `144` characters
2. Practical display limit: `9` wrapped lines with proportional character widths

Current evidence strongly suggests the implementation should move from pure
character-count clipping to a visual-width estimator calibrated by VRChat
black-box tests.
