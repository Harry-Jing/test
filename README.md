# VRC Live Caption

VRC Live Caption is a real-time caption and translation tool for VRChat.

It captures microphone speech, converts it into text, and sends live captions to the VRChat Chatbox through OSC. It is designed for Chinese-first recognition and mixed Chinese-English speech.

## Current Scope

- Real-time microphone -> cloud STT -> VRChat Chatbox captions
- Default provider: `openai_realtime`
- Optional provider: `iflytek_rtasr`
- Optional text translation providers: `deepl`, `google_cloud`, `translategemma_local`

## Requirements

- Windows
- Python `3.14`
- `uv`
- VRChat with OSC enabled
- `OPENAI_API_KEY`

## Quick Start

```shell
cp .env.example .env
cp vrc-live-caption.toml.example vrc-live-caption.toml
uv sync
uv run vrc-live-caption doctor
uv run vrc-live-caption osc-test "OSC test"
uv run vrc-live-caption run
```

Set `OPENAI_API_KEY` in `.env` before `doctor` or `run`.

Ordinary configuration lives in one file: `vrc-live-caption.toml`. See [docs/configuration.md](./docs/configuration.md) for scenario-based setup, including local FunASR and local TranslateGemma sidecars.

For local inference dependencies, use one shared extra:
- CPU: `uv sync --extra local-cpu`
- CUDA 12.8: `uv sync --extra local-cu128`

## Common Commands

```shell
uv run vrc-live-caption devices
uv run vrc-live-caption doctor
uv run vrc-live-caption local-stt serve
uv run vrc-live-caption local-translation serve
uv run vrc-live-caption osc-test "OSC test"
uv run vrc-live-caption record-sample --seconds 10
uv run vrc-live-caption run
```

OpenAI is the default backend. To use iFLYTEK, FunASR, DeepL, Google Cloud, or TranslateGemma instead, keep using the same `vrc-live-caption.toml` file and follow [docs/configuration.md](./docs/configuration.md).
