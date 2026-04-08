# Docstrings

Project docstrings follow PEP 257. Write them in English. Ruff checks `pydocstyle` with the Google convention, so use Google-style sections only when they add real contract detail and do not mix Google and NumPy styles.

## Scope

- This document covers docstring expectations for exported or user-facing Python APIs.
- Keep docstrings focused on contract, not implementation narration.

## Write docstrings for

- exported or user-facing modules, classes, functions, methods, properties, and public attributes
- private helpers only when the behavior is non-obvious

## Style rules

- Start with a short summary sentence and end it with a period.
- In multi-line docstrings, leave one blank line after the summary.
- Describe behavior, constraints, side effects, and failure boundaries.
- Use `Args`, `Returns`, `Yields`, `Raises`, and `Attributes` only when they add information the signature and types do not already make clear.
- Do not restate the function signature or obvious annotated types.

## Omit when

- the symbol is a trivial pass-through or override with unchanged behavior
- the helper is private and the behavior is already clear from the code
- the file is a test module; test module docstrings are optional
