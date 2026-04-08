# Repository Guidance

This project is `VRC Live Caption`.

## Scope

- Applies repo-wide.
- All paths are repository-relative.

## Defaults

- Windows is the primary target platform.
- Use Python `3.14` and `uv`.

## Development Docs

- Before non-trivial changes, read `docs/development/README.md`.
- Treat `docs/development` as the source of truth for setup, testing, and architecture.
- If a change affects scope, architecture, milestones, or developer workflow, update the corresponding file in `docs/development`.

## Config

- Keep secrets in `.env`.
- Keep ordinary runtime configuration in `vrc-live-caption.toml`.
- Treat `vrc-live-caption.toml.example` as the user-facing config reference.

## User Requests

- If the user explicitly asks for analysis or an opinion before changes, do not modify files until asked to proceed.
