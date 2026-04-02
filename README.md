# VRC Live Caption

VRC Live Caption is a real-time caption and translation tool for VRChat.

It captures microphone speech, converts it into text, and sends live captions to the VRChat Chatbox through OSC. It is designed for Chinese-first recognition and mixed Chinese-English speech.

## Current Scope

- Real-time microphone -> cloud STT -> VRChat Chatbox captions
- Default provider: `openai_realtime`
- Optional provider: `iflytek_rtasr`
- Translation is not part of the current CLI scope

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

## Common Commands

```shell
uv run vrc-live-caption devices
uv run vrc-live-caption doctor
uv run vrc-live-caption osc-test "OSC test"
uv run vrc-live-caption record-sample --seconds 10
uv run vrc-live-caption run
```

OpenAI is the default backend. To use iFLYTEK instead, switch `[stt].provider` in `vrc-live-caption.toml` and set the required `IFLYTEK_*` secrets in `.env`.

