# Contributing

Minimal contributor workflow for `VRC Live Caption`.

## Read First

- [Environment](./docs/development/environment.md)
- [Testing](./docs/development/testing.md)
- [Docstrings](./docs/development/docstrings.md)

## Setup

```shell
uv sync
uv run pre-commit install --install-hooks
```

## Before Opening a PR

```shell
pre-commit run --all-files
uv run pytest -q
uv run ruff check
uv run ruff format --check
uv run ty check
```

## Commit Messages

Use [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) and add an emoji after `: `.

```text
<type>[optional scope][!]: <emoji> <description>
```

Types:

- `feat`: ✨
- `fix`: 🐛
- `docs`: 📝
- `refactor`: ♻️
- `perf`: ⚡️
- `test`: ✅
- `build`: 📦
- `ci`: 👷
- `chore`: 🔧
- `revert`: ⏪️

Example commit messages:
```text
feat(api): ✨ add batch endpoint
fix(config): 🐛 handle missing .env file
feat(api)!: 💥 remove legacy auth
```

## Pull Requests

- Keep PRs small and focused.
- Explain why the change is needed.
- Make sure checks pass.
