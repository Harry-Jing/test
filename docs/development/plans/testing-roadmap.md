# Testing Roadmap

This document tracks test backlog and useful next coverage additions.
Use `../testing.md` for execution commands and prerequisites.

## Current Layout

```text
tests/
  conftest.py
  fixtures/
    audio/
      test.wav
  support/
    audio_fakes.py
    stt_fakes.py
    config_helpers.py
    replay.py
  unit/
    ...
  integration/
    README.md
    test_iflytek_rtasr_live.py
    test_openai_realtime_live.py
```

Conventions:

- `tests/fixtures/` stores static test assets
- `tests/support/` stores fakes, factories, and helpers
- `tests/conftest.py` stores cross-module fixtures
- `tests/integration/` stores explicit live or external-service tests

## Existing Live Coverage

- `tests/integration/test_iflytek_rtasr_live.py` replays `tests/fixtures/audio/test.wav` against the real iFLYTEK RTASR API.
- `tests/integration/test_openai_realtime_live.py` replays `tests/fixtures/audio/test.wav` against the real OpenAI realtime transcription API.

## Fixture Backlog

Recommended additions under `tests/fixtures/audio/`:

- `silence.wav`
- `short_under_100ms.wav`
- `mixed_zh_en.wav`
- `stereo_48k.wav`

Optional:

- `corrupt.wav`

Useful helper backlog:

- `fake_clock`
- shared log-capture helper
- reusable `AudioChunk` factory

## Next Coverage Targets

After the missing fixtures land, the most valuable follow-up tests are:

- iFLYTEK live behavior for silence and very short clips
- OpenAI realtime behavior for silence and very short clips
- mixed Chinese-English transcript regression coverage
- chatbox rollover behavior under longer real-world utterance sequences
- VRChat-facing manual validation for line-wrap-heavy text and fast partial bursts
- heartbeat and warning-throttle logging behavior
- finer shutdown-order regression coverage

## Current Priority

The next fixture priorities remain:

1. `silence.wav`
2. `short_under_100ms.wav`
3. `mixed_zh_en.wav`
4. `stereo_48k.wav`
