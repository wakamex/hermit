# Hermit

Personal Claude assistant with bwrap sandboxing for Linux.

## Architecture

Daemon + CLI client pattern:
- Daemon listens on Unix socket (`data/hermit.sock`)
- CLI sends JSON commands to daemon
- Daemon manages sessions, scheduler, runs Claude in bwrap sandbox

## Commands

| Command | Description |
|---------|-------------|
| `daemon` | Start the daemon |
| `send [-g GROUP] MSG` | Send message |
| `repl [-g GROUP]` | Interactive REPL |
| `groups` | List groups |
| `new [-g GROUP]` | Clear session |
| `status` | Check daemon |
| `task add -c CRON [-g GROUP] MSG` | Schedule task |
| `task list` | List tasks |
| `task rm ID` | Delete task |

## Scheduler

Supports simple cron expressions:
- `@hourly`, `@daily`, `@weekly`
- `*/N` - every N minutes

## Key Files

| File | Purpose |
|------|---------|
| `hermit.py` | Single-file implementation |
| `data/hermit.db` | SQLite database |
| `data/hermit.sock` | Unix socket |
| `groups/<name>/` | Per-group workspace |

## Key Functions

| Function | Purpose |
|----------|---------|
| `Daemon.run()` | Main loop, accepts connections |
| `Daemon.run_scheduler()` | Task scheduler loop |
| `Daemon.handle_request()` | Route commands |
| `run_sandbox()` | Execute Claude in bwrap |
| `build_bwrap_args()` | Construct sandbox command |
| `send_to_daemon()` | Client → daemon communication |

## Session Continuity

Sessions tracked in SQLite per group. Uses Claude's `--resume SESSION_ID` flag.

## Development

```bash
# Terminal 1: Start daemon
python hermit.py daemon

# Terminal 2: Send messages
python hermit.py send "hello"
python hermit.py task add -c @hourly "Check for updates"
python hermit.py task list
```
