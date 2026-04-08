# Development Docs

Development documents for `VRC Live Caption`.

## Core references

- [Environment](./environment.md): setup, secrets, dependency policy, and key stack defaults
- [Testing](./testing.md): default checks, opt-in integration runs, coverage inspection, and manual validation
- [Docstrings](./docstrings.md): concise docstring rules for public APIs

## Architecture

- [Runtime](./architecture/runtime.md): capture, pipeline orchestration, CLI lifecycle, shutdown, and diagnostics
- [STT](./architecture/stt.md): backend selection, retry lifecycle, provider boundaries, and normalized events
- [Translation](./architecture/translation.md): final-only translation flow, providers, output modes, and bilingual layout rules
- [Caption and OSC](./architecture/caption-and-osc.md): caption stabilization, pacing, typing state, and OSC output behavior
- [VRChat Chatbox Reference](./architecture/vrchat-chatbox-reference.md): canonical wrap model, line-break rules, and fixed layout facts

## Plans

- [UI](./plans/UI.md): minimal GUI guardrails
