# Development Docs

Development documents for `VRC Live Caption`.

## Core References

- [Environment](./environment.md): setup, secrets, dependency rules, and key stack defaults
- [Testing](./testing.md): default checks, coverage, live-test commands, and manual validation
- [Docstrings](./docstrings.md): concise docstring rules for public APIs

## Architecture

- [Runtime](./architecture/runtime.md): current capture-to-pipeline contract, queue ownership, and CLI/runtime boundaries
- [STT](./architecture/stt.md): backend selection, async runner lifecycle, retry behavior, and connection-attempt boundaries
- [Caption And OSC](./architecture/caption-and-osc.md): caption stabilization, chatbox pacing, OSC output, and diagnostics behavior
- [Translation](./architecture/translation.md): final-only text translation flow, provider boundaries, and stacked source-target chatbox rendering
- [VRChat Chatbox Reference](./architecture/vrchat-chatbox-reference.md): concise canonical reference for the fixed VRChat chatbox wrap model

## Plans

- [Testing Refactor](./plans/testing-refactor.md): current direction for reorganizing and simplifying the test suite
- [UI Remaining](./plans/ui-remaining.md): minimal remaining GUI wrapper work
