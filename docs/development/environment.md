# Environment

Developer setup and environment defaults for `VRC Live Caption`.

## Baseline

- Python: `3.14`
- Use `uv` for environment setup and project commands
- Run commands from the repository root
- Windows is the primary validation platform

## Setup

```bash
uv sync
```

This creates or updates the project environment from `pyproject.toml` and `uv.lock`.

## Secrets And Config

- Secrets are loaded through `pydantic-settings`.
- Copy `.env.example` to `.env` when local secrets are needed.
- The default OpenAI backend requires `OPENAI_API_KEY`.
- The optional iFLYTEK backend requires `IFLYTEK_APP_ID`, `IFLYTEK_API_KEY`, and `IFLYTEK_API_SECRET`.
- Process environment variables override `.env`.
- Use `.env` for secrets only.
- Ordinary runtime configuration still comes from `vrc-live-caption.toml`.

## Dependency Changes

- Use `uv add <package>` for runtime dependencies.
- Use `uv add --dev <package>` for development dependencies.
- Commit both `pyproject.toml` and `uv.lock` after dependency changes.

## Key Stack

- CLI: `Typer`
- GUI: `PySide6`
- Audio capture: `sounddevice`
- OSC client: `python-osc`
- Configuration: `TOML` + `pydantic v2`
