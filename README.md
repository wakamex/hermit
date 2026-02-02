# Hermit

Personal Claude assistant with bwrap sandboxing for Linux.

## Requirements

- Python 3.10+
- bubblewrap (`bwrap`)
- Claude Code CLI (native binary)

```bash
# Install bwrap
sudo dnf install bubblewrap  # Fedora
sudo apt install bubblewrap  # Debian/Ubuntu

# Install Claude Code native binary
claude install stable
```

## Usage

```bash
# Start the daemon (in a terminal or via systemd)
python hermit.py daemon

# In another terminal:
python hermit.py send "What is 2+2?"
python hermit.py send -g myproject "Summarize the codebase"

# Interactive REPL
python hermit.py repl
python hermit.py repl -g myproject

# Manage sessions
python hermit.py groups          # List groups and session status
python hermit.py new -g myproject  # Clear session, start fresh
python hermit.py status          # Check if daemon is running
```

## Architecture

```
hermit daemon          CLI client
     │                     │
     └──── Unix socket ────┘
              │
         ┌────┴────┐
         │ SQLite  │  (groups, sessions)
         └────┬────┘
              │
         ┌────┴────┐
         │  bwrap  │  (sandbox)
         └────┬────┘
              │
         ┌────┴────┐
         │ Claude  │  (AI)
         └─────────┘
```

## Groups

Each group gets:
- Isolated workspace at `groups/<name>/`
- Persistent session (multi-turn conversations)
- Own `CLAUDE.md` memory file

## Session Continuity

The daemon tracks Claude session IDs per group. Subsequent messages in the same group continue the conversation context.

Use `hermit new -g GROUP` to clear a session and start fresh.

## Systemd Service

```bash
# Create user service
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

# Enable and start
systemctl --user enable hermit
systemctl --user start hermit
```

## Scheduled Tasks

```bash
# Recurring tasks
hermit task add -c @hourly "Check for updates"
hermit task add -c @daily "Morning summary"
hermit task add -c */5 "Run every 5 minutes"

# One-time tasks
hermit task add -c "once:+30m" "Remind me in 30 minutes"
hermit task add -c "once:2026-02-02T09:00:00" "Morning meeting prep"

# Manage
hermit task list
hermit task rm <id>
```

## History

All messages are logged to `groups/<name>/history.txt` for easy viewing:

```bash
tail -f groups/default/history.txt
```

## Background

Hermit is a Linux-native, security-focused alternative to projects like Clawdbot/OpenClaw.

### Related Projects

| Project | Security | Platform | Input |
|---------|----------|----------|-------|
| [Clawdbot/OpenClaw](https://github.com/VoltAgent/awesome-clawdbot-skills) | None (full system access) | Multi-platform | WhatsApp, Telegram, Slack, etc. |
| [NanoClaw](https://github.com/anthropics/nanoclaw) | Apple Containers | macOS | WhatsApp |
| **Hermit** | bwrap sandbox | Linux | CLI |

### Why Hermit?

1. **Security** - Claude runs in a bwrap sandbox, isolated to `groups/<name>/`. No access to your home directory, system files, or other groups.

2. **Linux-native** - Uses bubblewrap (same tech as Flatpak) instead of Docker or Apple Containers.

3. **Minimal** - Single Python file, stdlib only, ~800 lines. No Node.js, no web frameworks.

4. **CLI-first** - No messaging app integration. Just `hermit send "prompt"`.

### Security Model

```
Host System (protected)
└── bwrap sandbox
    ├── /usr, /lib, /bin (read-only)
    ├── ~/.hermit/.claude (hermit's own config, isolated from user's)
    ├── ~/.hermit/tools/ (sandboxed tools like gh, jq)
    └── /workspace → groups/<name>/ (read-write)
```

**Isolation features:**
- Claude can only write to the group workspace
- Hermit has its own `.claude` directory (no access to user's plugins/skills/settings)
- Tool credentials passed via environment variables (not readable config files)
- Prompt injection attacks are contained - can't access files outside sandbox

## Tools

Hermit can install and authenticate tools for use inside the sandbox:

```bash
# Install tools
hermit install gh    # GitHub CLI
hermit install jq    # JSON processor

# Authenticate tools (stored in ~/.hermit/config/)
hermit auth gh       # Login with hermit's own GitHub identity
```

Tools are isolated from your personal configs - `hermit auth gh` creates a separate GitHub identity from your normal `~/.config/gh`.
