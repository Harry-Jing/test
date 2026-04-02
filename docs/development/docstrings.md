# Docstrings

Use PEP 257 docstring structure.
Use Google-style sections only when they add real information.
Do not mix Google and NumPy styles.

## Scope

- Prioritize exported or user-facing modules, classes, functions, methods, properties, and public attributes.
- Treat underscore-prefixed local helpers as private by default.
- Skip docstrings for private helpers unless the behavior is non-obvious.
- Test module docstrings are optional.

## Core Rules

- Write in English.
- Keep the summary short and end it with a period.
- Use one sentence when the symbol is easy to understand from its name and type.
- In multi-line docstrings, leave one blank line after the summary.
- Do not restate the function signature.
- Do not repeat obvious type information from annotations.
- Focus on behavior, constraints, side effects, and failure boundaries.

## Sections

Use `Args`, `Returns`, `Yields`, `Raises`, and `Attributes` only when they add meaningful contract detail.

- Add sections when parameter or return semantics are not obvious.
- Add sections when callers need to know about yielded values, exceptions, or public attributes.

## Avoid

- repeating parameter names without adding meaning
- repeating annotated types
- describing trivial implementation steps
- writing long docstrings for simple pass-through code
- adding extra detail to overrides that do not change behavior
