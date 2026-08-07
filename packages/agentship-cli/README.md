# agentship-cli

The `agentship` command-line interface. Owns the `agentship` console script; import as `agentship_cli`.

Commands:

- `agentship init [DIR]` — scaffold a new single-tenant project (`agents/assistant.yaml`, `.env.example`, `README.md`) in `DIR` (default `.`). Refuses to overwrite existing files.
- `agentship new-agent NAME [--engine langgraph] [--agents-dir agents]` — write one starter agent spec at `<agents-dir>/NAME.yaml`. Refuses to overwrite.
- `agentship doctor [--agents-dir DIR | FILE]` — validate agent specs against their engines' declared capabilities. Prints a per-agent `OK`/`✗` line and exits `1` if any agent is invalid. An engine whose package is not installed yields an actionable `pip install` hint (no traceback).
- `agentship run FILE --input ... [--stream] [--env-file ...]` — load a YAML spec, build the agent, run (or stream) one turn, and print the output.

Harness errors print as a clean `Error:`/status line with a non-zero exit code rather than a traceback; pass `--debug` (on `run`/`doctor`) to re-raise for the full trace.
