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

For local inference, choose one shared extra before manual validation:

```bash
uv sync --extra local-cpu
uv sync --extra local-cu130
```

- Use `local-cpu` for CPU-only local STT and local TranslateGemma validation.
- Use `local-cu130` on Windows with an NVIDIA GPU when you want local inference to resolve `device = "auto"` to `cuda:0` and TranslateGemma `dtype = "auto"` to `bfloat16`.

## Secrets And Config

- Secrets are loaded through `pydantic-settings`.
- Copy `.env.example` to `.env` when local secrets are needed.
- Copy `vrc-live-caption.toml.example` to `vrc-live-caption.toml` for ordinary runtime config.
- The default OpenAI backend requires `OPENAI_API_KEY`.
- The optional iFLYTEK backend requires `IFLYTEK_APP_ID`, `IFLYTEK_API_KEY`, and `IFLYTEK_API_SECRET`.
- The optional DeepL translation backend requires `DEEPL_AUTH_KEY`.
- The optional Google Cloud Translation backend uses ADC plus `translation.providers.google_cloud.project_id`.
- The optional local TranslateGemma translation backend does not require an app secret, but the sidecar may need Hugging Face authentication plus accepted Gemma license terms when the configured model is not already cached locally.
- Process environment variables override `.env`.
- Use `.env` for secrets only.
- Ordinary runtime configuration comes from `vrc-live-caption.toml`.
- Local FunASR and local TranslateGemma sidecar runtime settings are nested inside that same file under `[stt.providers.funasr_local.sidecar]` and `[translation.providers.translategemma_local.sidecar]`.
- `vrc-live-caption.toml.example` is the user-facing setup reference.

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
