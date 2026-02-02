# Hermit

Personal Claude assistant with bwrap sandboxing for Linux.

## Structure

Single Python file (`hermit.py`) that:
1. Manages groups via SQLite
2. Spawns Claude Code in bwrap sandbox
3. Routes input/output

## Key Functions

| Function | Purpose |
|----------|---------|
| `init_db()` | Create SQLite tables |
| `get_or_create_group()` | Group management |
| `build_bwrap_args()` | Construct bwrap command |
| `run_sandbox()` | Execute Claude in sandbox |
| `chat()` | High-level send message |
| `repl()` | Interactive mode |

## Development

```bash
python hermit.py --init    # Initialize DB
python hermit.py           # Interactive REPL
python hermit.py -p "..."  # Single prompt
```

## Adding Features

To add input channels (HTTP, Unix socket, etc.), add a new function that calls `chat(group, prompt)` and returns the response.
