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

## Plans

- [开发计划（精简版）](./plans/开发计划（精简版）.md): product goals, MVP scope, milestones, and risks
- [本地 AI 服务规划（计划）](./plans/local-ai-services-plan.md): 本地 STT、本地翻译、依赖隔离与模型/运行时分离的长期方向
- [Testing Roadmap](./plans/testing-roadmap.md): fixture backlog and next coverage targets
- [VRChat Chatbox Width Investigation](./plans/vrchat-chatbox-width-investigation.md): empirical chatbox width findings, mixed-character wrap behavior, and next calibration tests

## Archive

- [M2 STT Backend Plan](./archive/m2-stt-backend-plan.md): archived design intent and enduring STT boundary notes
- [M2 STT Development Summary](./archive/m2-stt-development-summary.md): archived protocol findings and implementation notes confirmed during M2
