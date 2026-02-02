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
| `groups/<name>/CLAUDE.md` | Agent's soul (identity, memory, notes) |
| `groups/<name>/history.txt` | Chat log (agent can read) |
| `~/.hermit/.claude/` | Hermit's isolated Claude config |
| `~/.hermit/tools/` | Installed tools (gh, jq, etc.) |
| `~/.hermit/config/` | Tool configs (gh auth, etc.) |

## Memory Architecture

- **Soul:** `groups/<name>/CLAUDE.md` - identity, guidelines, persistent notes
- **History:** `groups/<name>/history.txt` - full chat log, agent can read/grep
- **Files:** Anything in `groups/<name>/` persists across sessions
- **Session:** Claude's `--resume` maintains context within a session

## Key Functions

| Function | Purpose |
|----------|---------|
| `Daemon.run()` | Main loop, accepts connections |
| `Daemon.run_scheduler()` | Task scheduler loop |
| `Daemon.handle_request()` | Route commands |
| `run_sandbox()` | Execute Claude in bwrap |
| `build_bwrap_args()` | Construct sandbox command |
| `get_gh_token()` | Read GH token from hermit config |
| `send_to_daemon()` | Client → daemon communication |

## Security Architecture

- Hermit uses `~/.hermit/.claude/` instead of user's `~/.claude/` (no access to user's plugins/skills)
- Claude credentials copied once from user's config (shares auth, not settings)
- Tool credentials (gh, etc.) passed via env vars, not mounted config files
- `/etc/pki` mounted read-only for TLS on Fedora

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
