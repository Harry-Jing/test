# UI

- The UI should stay a thin wrapper over the existing CLI, runtime, config, logging, and diagnostics surfaces.
- Prioritize configuration editing, run/stop controls, and clear visibility into current status and logs.
- Prefer incremental usability improvements over new UI-only architecture or feature branches.
- Do not introduce UI-specific pipeline behavior that diverges from the core runtime contract.
