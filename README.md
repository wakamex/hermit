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
