# Repository Guidance

This project is `VRC Live Caption`.

Before making non-trivial changes, read the related project documents in `/docs/development`.

## Docs

- Start with `/docs/development/README.md` for the doc index.
- Use `/docs/development/environment.md` for setup and environment defaults.
- Use `/docs/development/testing.md` for checks and validation commands.
- Use `/docs/development/docstrings.md` for docstring rules.
- Check `/docs/development/architecture/` for current runtime behavior and `/docs/development/plans/` for active planning docs when relevant.

## Working Rules

- Treat `/docs/development` as the source of truth for product scope and technical direction.
- If a change affects scope, architecture, or milestones, update or create the corresponding file in `/docs/development`.
- MVP priority is CLI first, cloud STT first, and original-language captions before translation.
- Windows is the target platform; macOS is only a compatibility consideration.
- When the user explicitly asks for analysis or an opinion before changes, do not modify files until the user asks to proceed.
