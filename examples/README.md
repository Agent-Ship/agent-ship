# Examples

## `hello.yaml` — the walking skeleton

A single agent on the zero-dependency `echo` engine. No API key, no model — it
just echoes your input, proving the whole spine (author → build → run/stream →
CLI) works end to end.

```bash
agentship run examples/hello.yaml --input "hi"
# echo: hi

agentship run examples/hello.yaml --input "hi" --stream
# echo: hi
```

Each later phase adds a runnable example here for the capability it ships.
