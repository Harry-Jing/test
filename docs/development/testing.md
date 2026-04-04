# Testing

How to verify `VRC Live Caption` locally.

## Default Checks

Run these commands from the repository root:

```bash
pre-commit run --all-files
uv run pytest -q
uv run ruff check
uv run ruff format --check
uv run ty check
```

Notes:

- The default `uv run pytest -q` flow excludes tests marked `integration`, `openai_live`, and `iflytek_live`.
- `uv run pre-commit install --install-hooks` installs the local `pre-commit`, `commit-msg`, and `pre-push` hooks.
- Use `uv run ruff format` only when formatting is intentionally part of the change.

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
uv run pytest -q tests/integration -m "integration and iflytek_live"
```

- Requires `IFLYTEK_APP_ID`, `IFLYTEK_API_KEY`, `IFLYTEK_API_SECRET`, and outbound network access.
- Replays `tests/fixtures/audio/test.wav` through the async STT runner and asserts keyword-based transcript success.

### OpenAI

```bash
uv run pytest -q tests/integration -m "integration and openai_live"
```

- Requires `OPENAI_API_KEY` and outbound network access.
- Replays `tests/fixtures/audio/test.wav` through the async STT runner and asserts keyword-based transcript success.

### Local STT Sidecar

```bash
uv run pytest -q tests/integration/test_local_stt_sidecar.py -m integration
```

- Starts a local websocket sidecar in-process with fake inference models and replays `tests/fixtures/audio/test.wav` through the `funasr_local` runner path.
- This verifies the repository-local websocket protocol, transcript normalization, and shutdown behavior without loading real FunASR models.

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
