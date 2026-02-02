# Hermit

Personal Claude assistant with bwrap sandboxing for Linux.

## Requirements

- Python 3.10+
- bubblewrap (`bwrap`)
- Claude Code CLI

```bash
# Install bwrap
sudo dnf install bubblewrap  # Fedora
sudo apt install bubblewrap  # Debian/Ubuntu

# Install Claude Code
claude install stable
```

## Quick Start

```bash
# Start the daemon
python hermit.py daemon

# In another terminal
python hermit.py send "Hello, Hermit!"
```

## Project Structure

```
hermit/
├── hermit.py              # Main script (all you need)
├── CLAUDE.md.template     # Template for new groups
├── README.md
│
├── data/                  # Runtime (gitignored)
│   ├── hermit.db          # SQLite database
│   ├── hermit.sock        # Unix socket
│   └── hermit.pid         # Daemon PID
│
└── groups/                # Runtime (gitignored)
    └── <group-name>/
        ├── CLAUDE.md      # Agent's soul: identity, memory, notes
        ├── history.txt    # Full conversation log
        ├── .claude/       # Claude session data
        └── .moltbook/     # Moltbook credentials (if registered)
```

**Tracked in git:** `hermit.py`, `CLAUDE.md.template`, `README.md`
**Not tracked (runtime data):** `data/`, `groups/`

When you create a new group, copy `CLAUDE.md.template` to `groups/<name>/CLAUDE.md`.

## Usage

```bash
# Send a message
python hermit.py send "What is 2+2?"
python hermit.py send -g myproject "Summarize the codebase"

# Pipe input (avoids shell escaping issues)
echo "Hello!" | python hermit.py send
cat prompt.txt | python hermit.py send

# Here-doc for multi-line messages
python hermit.py send << 'EOF'
Multi-line message with special chars! 🦞
No escaping needed.
EOF

# Interactive REPL
python hermit.py repl
python hermit.py repl -g myproject

# Manage sessions
python hermit.py groups          # List groups and session status
python hermit.py new             # Clear session, start fresh
python hermit.py new -g myproject
python hermit.py status          # Check if daemon is running
```

## Groups

Each group is an isolated workspace with its own:

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Agent's identity, guidelines, and memory |
| `history.txt` | Complete conversation log |
| `.claude/` | Claude session state |
| `*` | Any files the agent creates or downloads |

Groups are isolated from each other and from your home directory.

## Agent Identity & Memory

The agent's "soul" is its `CLAUDE.md` file. The agent can:

- **Read** its conversation history from `history.txt`
- **Update** `CLAUDE.md` to remember things across sessions
- **Store** credentials and files in its workspace

Example `CLAUDE.md` structure:

```markdown
# Hermit

You are Hermit, an autonomous AI agent...

## Memory

### Credentials
- Moltbook: `/workspace/.moltbook/credentials.json`
- GitHub: authenticated via GH_TOKEN

### Notes
- User prefers concise responses
- Project X uses Python 3.11
```

## Session Continuity

The daemon tracks Claude session IDs per group. Messages continue the conversation until you clear the session:

```bash
hermit new              # Clear default group session
hermit new -g myproject # Clear specific group session
```

## Architecture

```
hermit daemon          CLI client
     │                     │
     └──── Unix socket ────┘
              │
         ┌────┴────┐
         │ SQLite  │  sessions, tasks
         └────┬────┘
              │
         ┌────┴────┐
         │  bwrap  │  namespace isolation
         └────┬────┘
              │
         ┌────┴────┐
         │ Claude  │  AI agent
         └─────────┘
```

## Security Model

```
Host System (protected)
└── bwrap sandbox
    ├── /usr, /lib, /bin       (read-only)
    ├── ~/.hermit/.claude/     (hermit's config, not yours)
    ├── ~/.hermit/tools/       (gh, jq, etc.)
    └── /workspace/            → groups/<name>/ (read-write)
```

**Key isolation features:**

1. **Filesystem** - Agent can only write to `/workspace/` (its group directory)
2. **Config separation** - Hermit has its own `.claude/` directory, no access to your plugins/skills/settings
3. **Credential isolation** - Tool credentials passed via env vars, not mounted config files
4. **Group isolation** - Each group is sandboxed separately

This means prompt injection attacks are contained - a malicious skill can't access files outside the sandbox or steal your personal credentials.

## Tools

Install tools for use inside the sandbox:

```bash
hermit install gh    # GitHub CLI
hermit install jq    # JSON processor

hermit auth gh       # Authenticate (separate from your personal gh)
```

Tools use Hermit's own credentials, completely separate from your personal configs.

## Scheduled Tasks

```bash
# Recurring
hermit task add -c @hourly "Check for updates"
hermit task add -c @daily "Morning summary"
hermit task add -c "*/5" "Every 5 minutes"

# One-time
hermit task add -c "once:+30m" "Remind me in 30 minutes"
hermit task add -c "once:2026-02-02T09:00:00" "Meeting prep"

# Manage
hermit task list
hermit task rm <id>
```

## Systemd Service

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/hermit.service << 'EOF'
[Unit]
Description=Hermit Claude Assistant

[Service]
ExecStart=/usr/bin/python3 /path/to/hermit/hermit.py daemon
Restart=always

[Install]
WantedBy=default.target
EOF

systemctl --user enable --now hermit
```

## Comparison

| Project | Security | Platform | Interface |
|---------|----------|----------|-----------|
| [OpenClaw](https://github.com/VoltAgent/awesome-clawdbot-skills) | None (full access) | Any | WhatsApp, Telegram, etc. |
| [NanoClaw](https://github.com/anthropics/nanoclaw) | Apple Containers | macOS | WhatsApp |
| **Hermit** | bwrap sandbox | Linux | CLI |

## Why Hermit?

1. **Secure** - bwrap sandbox isolates Claude from your system
2. **Linux-native** - Uses bubblewrap (same tech as Flatpak)
3. **Minimal** - Single Python file, stdlib only, ~800 lines
4. **CLI-first** - No messaging app dependencies
