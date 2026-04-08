# Environment

Setup and baseline expectations for local development.

## Scope

- This document covers local setup, runtime config versus secrets, dependency workflow, and local inference extras.
- Use `vrc-live-caption.toml.example` as the user-facing config reference.

## Baseline

- Windows is the primary validation platform.
- Python: `3.14`
- Use `uv` for setup and project commands.
- Run commands from the repository root.

## Setup

```powershell
uv sync
uv run pre-commit install --install-hooks
```

When local secrets or runtime config are needed:

```powershell
Copy-Item .env.example .env
Copy-Item vrc-live-caption.toml.example vrc-live-caption.toml
```

## Config and secrets

- `.env` is for secrets loaded through `pydantic-settings`.
- Process environment variables override `.env`.
- `vrc-live-caption.toml` is the ordinary runtime config file.
- `vrc-live-caption.toml.example` is the user-facing config reference.
- Common cloud credentials are:
  - `OPENAI_API_KEY`
  - `IFLYTEK_APP_ID`, `IFLYTEK_API_KEY`, `IFLYTEK_API_SECRET`
  - `DEEPL_AUTH_KEY`
  - Google ADC plus a configured Google Cloud project id
- Local TranslateGemma validation may also require Hugging Face authentication and accepted Gemma license terms when the selected model is not already cached.

## Local inference extras

Choose one shared extra before local-model validation:

```powershell
uv sync --extra local-cpu
uv sync --extra local-cu130
```

- Use `local-cpu` for CPU-only local STT and local TranslateGemma validation.
- Use `local-cu130` on Windows with an NVIDIA GPU when you want local inference to prefer CUDA.

## Dependency workflow

- Use `uv add <package>` for runtime dependencies.
- Use `uv add --dev <package>` for development dependencies.
- Commit both `pyproject.toml` and `uv.lock` after dependency changes.

## Key stack

- CLI: `Typer`
- GUI: `PySide6`
- Audio capture: `sounddevice`
- OSC client: `python-osc`
- Config: `TOML` + `pydantic v2`
