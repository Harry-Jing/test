# Integration Tests

This directory contains opt-in tests that exercise external services or
end-to-end runtime behavior.

Current policy:

- default `uv run pytest -q` excludes `integration`, `openai_live`, and
  `iflytek_live` tests
- tests here may require network access, real credentials, or billable API usage
- live tests stay out of the default unit-test flow

Current contents:

- `test_iflytek_rtasr_live.py`
  - replays `tests/fixtures/audio/test.wav`
  - drives the async STT runner against the real iFLYTEK RTASR API
  - asserts readiness, graceful shutdown, and keyword-based transcript success
- `test_openai_realtime_live.py`
  - replays `tests/fixtures/audio/test.wav`
  - drives the async STT runner against the real OpenAI realtime transcription API
  - asserts readiness, graceful shutdown, and keyword-based transcript success
- `test_translation_live.py`
  - sends short text requests to the real DeepL or Google Cloud Translation API
  - validates basic provider setup plus non-empty translated output
  - requires billable translation credentials and network access

For execution details, prerequisites, and the explicit command, use
`docs/development/testing.md`.
