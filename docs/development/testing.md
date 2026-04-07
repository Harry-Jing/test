# Testing

How to verify `VRC Live Caption` locally.

## Structure And Naming

- Keep the top-level test split to `tests/unit`, `tests/integration`, and explicit live markers.
- Prefer fine-grained test files named `test_<subject>_<behavior>.py`.
- Group related tests under `Test...` classes whose names describe the subject or behavior under test.
- Test methods should use `test_when_<condition>__then_<result>` naming.
- Test names must state the subject, triggering condition, and expected result without filler words such as `works`, `handles_case`, or `returns_expected_result`.
- When one test name starts describing multiple unrelated branches or outcomes, split it into separate tests.
- Parameterized tests must include explicit `id=` values that make the collected case readable.
- Within a file, keep tests ordered as: success path, input or config validation, failure path, shutdown or cleanup, then regression coverage.

## Shared Test Support

- Shared Python test support lives under `tests/support/`.
- Use `tests/support/builders/` for reusable config or object builders.
- Use `tests/support/fakes/` for reusable test doubles such as audio, OSC, and STT fakes.
- Use `tests/support/harnesses/` for async drivers, fixture replay helpers, and live-test orchestration.
- Keep `tests/conftest.py` thin and limited to widely shared fixtures.
- Prefer local helpers inside one test module or one subsystem package when a helper is not reused broadly enough to justify shared support.
- `tests/fixtures/` remains for static files and sample data only; do not place Python helpers there.

## Default Checks

Run these commands from the repository root:

```bash
uv run pre-commit run --all-files
uv run pytest -q
uv run ruff check
uv run ruff format --check
uv run ty check
```

Notes:

- The default `uv run pytest -q` flow excludes tests marked `integration` and `live`.
- Provider-specific live markers such as `openai_live`, `iflytek_live`, `deepl_live`, and `google_translate_live` are intended for targeted runs on top of the shared `live` marker.
- Use `uv run pytest -q -m "<marker expression>"` to run marker-filtered subsets, for example `-m "live and openai_live"` or `-m "integration and not live"`.
- `uv run pre-commit install --install-hooks` installs the local `pre-commit`, `commit-msg`, and `pre-push` hooks.
- Use `uv run ruff format` only when formatting is intentionally part of the change.
- When refactoring tests, run the smallest affected pytest subset first, then rerun `uv run pytest -q`.

## Coverage

Run coverage when you need to inspect gaps or after changing coverage-related behavior:

```bash
uv run coverage run -m pytest -q
uv run coverage report -m
uv run coverage html
```

- Coverage is package-scoped for `vrc_live_caption` with branch coverage enabled.
- The total coverage floor is `80%`.
- `htmlcov/index.html` is the generated HTML report.
- GitHub Actions `Checks` is the authoritative CI entrypoint.

## Live Integration Tests

Live tests are opt-in and billable.

### iFLYTEK

```bash
uv run pytest -q tests/integration -m "live and iflytek_live"
```

- Requires `IFLYTEK_APP_ID`, `IFLYTEK_API_KEY`, `IFLYTEK_API_SECRET`, and outbound network access.
- Replays `tests/fixtures/audio/test.wav` through the async STT runner and asserts keyword-based transcript success.

### OpenAI

```bash
uv run pytest -q tests/integration -m "live and openai_live"
```

- Requires `OPENAI_API_KEY` and outbound network access.
- Replays `tests/fixtures/audio/test.wav` through the async STT runner and asserts keyword-based transcript success.

### Local STT Sidecar

```bash
uv run pytest -q tests/integration/test_local_stt_sidecar.py -m integration
```

- Starts a local websocket sidecar in-process with fake inference models and replays `tests/fixtures/audio/test.wav` through the `funasr_local` runner path.
- This verifies the repository-local websocket protocol, transcript normalization, and shutdown behavior without loading real FunASR models.

### Cloud Translation

```bash
uv run pytest -q tests/integration/test_translation_live.py -m "live and deepl_live"
uv run pytest -q tests/integration/test_translation_live.py -m "live and google_translate_live"
```

- DeepL live tests require `DEEPL_AUTH_KEY` and outbound network access.
- Google Cloud live tests require ADC, `GOOGLE_TRANSLATE_PROJECT_ID`, and outbound network access.
- Both translation test paths are billable and excluded from default `pytest` runs.

## Manual Runtime Validation

Use a native Windows terminal for microphone and audio-device validation:

```bash
uv run vrc-live-caption devices
uv run vrc-live-caption doctor
uv run vrc-live-caption local-stt serve
uv run vrc-live-caption osc-test "OSC test"
uv run vrc-live-caption record-sample --seconds 10
uv run vrc-live-caption run
```

- Before `osc-test` or `run`, ensure VRChat has OSC enabled and is listening on the configured host and port.
- Before `doctor` or `run`, ensure the credentials required by the selected `stt.provider` are available, or start `vrc-live-caption local-stt serve` when using `funasr_local`.
- `osc-test` does not require STT credentials.
- `local-stt serve` uses `local-stt-funasr.toml` when present and otherwise falls back to built-in defaults.
- Install `uv sync --extra funasr-cpu` for CPU-only local STT validation, or `uv sync --extra funasr-cu128` on Windows/NVIDIA machines for GPU validation.
- `local-stt-funasr.toml` now defaults to `device = "auto"`, which prefers `cuda:0` when `torch.cuda.is_available()` is true and otherwise falls back to `cpu`.

## Config Notes

- Capture settings live under `[capture]`.
- Pipeline queue and shutdown settings live under `[pipeline]`.
- Retry policy lives under `[stt.retry]`.
- Provider-specific blocks live under `[stt.providers.<provider>]`.
- Translation settings live under `[translation]`.
- Google Cloud Translation provider settings live under `[translation.providers.google_cloud]`.
