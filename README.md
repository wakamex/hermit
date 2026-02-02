# Hermit

Personal Claude assistant with bwrap sandboxing for Linux.

## Requirements

- Python 3.10+
- bubblewrap (`bwrap`)
- Claude Code CLI

Install bwrap:
```bash
# Fedora
sudo dnf install bubblewrap

# Ubuntu/Debian
sudo apt install bubblewrap

# Arch
sudo pacman -S bubblewrap
```

## Setup

```bash
# Set your API key
export ANTHROPIC_API_KEY="sk-..."

# Or use OAuth token
export CLAUDE_CODE_OAUTH_TOKEN="..."

# Initialize database
python hermit.py --init

# Start chatting
python hermit.py
```

## Usage

```bash
# Interactive REPL (default group)
python hermit.py

# Interactive REPL with specific group
python hermit.py -g myproject

# Single prompt
python hermit.py -p "What is 2+2?"

# Single prompt with group
python hermit.py -g myproject -p "Summarize the codebase"
```

## Groups

Each group gets:
- Isolated filesystem at `groups/<name>/`
- Separate conversation history
- Own `CLAUDE.md` memory file

## Architecture

```
hermit.py          # Single-file implementation
├── SQLite DB      # Groups, sessions, messages
├── bwrap          # Linux namespace isolation
└── Claude Code    # AI agent
```

No daemon. No web tech. Just Python + bwrap + Claude.
