#!/usr/bin/env python3
"""
Hermit - Personal Claude assistant with bwrap sandboxing
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.resolve()
GROUPS_DIR = BASE_DIR / "groups"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hermit.db"


def init_db():
    """Initialize SQLite database."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            folder TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups(id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups(id)
        );
    """)
    conn.close()


def get_or_create_group(name: str) -> dict:
    """Get or create a group by name."""
    folder = name.lower().replace(" ", "-")
    group_path = GROUPS_DIR / folder
    group_path.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM groups WHERE name = ?", (name,))
    row = cur.fetchone()

    if row:
        group = dict(row)
    else:
        cur.execute(
            "INSERT INTO groups (name, folder) VALUES (?, ?)",
            (name, folder)
        )
        conn.commit()
        group = {"id": cur.lastrowid, "name": name, "folder": folder}

    conn.close()
    return group


def build_bwrap_args(group: dict, env_vars: dict) -> list[str]:
    """Build bwrap command arguments."""
    group_path = GROUPS_DIR / group["folder"]

    args = [
        "bwrap",
        # Mount system directories read-only
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", "/etc/ssl", "/etc/ssl",
        # Symlink for /sbin if it exists
        "--symlink", "/usr/bin", "/sbin",
        # Basic filesystems
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        # Workspace - group folder is read-write
        "--bind", str(group_path), "/workspace",
        # Home directory for Claude
        "--tmpfs", "/home",
        "--setenv", "HOME", "/home/user",
        "--setenv", "USER", "user",
        # Working directory
        "--chdir", "/workspace",
        # Isolation
        "--unshare-all",
        "--share-net",  # Allow network for Claude API calls
        "--die-with-parent",
    ]

    # Add environment variables
    for key, value in env_vars.items():
        args.extend(["--setenv", key, value])

    return args


def run_sandbox(group: dict, prompt: str) -> dict:
    """Run Claude Code in bwrap sandbox."""
    # Environment variables for Claude
    env_vars = {}

    # Check for API key or OAuth token
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")

    if oauth_token:
        env_vars["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
    elif api_key:
        env_vars["ANTHROPIC_API_KEY"] = api_key
    else:
        return {
            "status": "error",
            "error": "No ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN set"
        }

    bwrap_args = build_bwrap_args(group, env_vars)

    # Add Claude Code command
    cmd = bwrap_args + [
        "claude",
        "--print",  # Print response and exit
        "--output-format", "json",
        prompt
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "error": f"Claude exited with code {result.returncode}: {result.stderr[-500:]}"
            }

        # Parse JSON output
        try:
            output = json.loads(result.stdout)
            return {"status": "success", "result": output}
        except json.JSONDecodeError:
            # If not JSON, return raw output
            return {"status": "success", "result": result.stdout}

    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Sandbox timed out after 5 minutes"}
    except FileNotFoundError as e:
        return {"status": "error", "error": f"Command not found: {e}"}


def chat(group_name: str, prompt: str) -> str:
    """Send a message to Claude in the specified group."""
    group = get_or_create_group(group_name)

    print(f"[hermit] Group: {group['name']}", file=sys.stderr)
    print(f"[hermit] Prompt: {prompt[:100]}...", file=sys.stderr)

    result = run_sandbox(group, prompt)

    if result["status"] == "error":
        return f"Error: {result['error']}"

    return result["result"]


def repl(group_name: str):
    """Interactive REPL mode."""
    group = get_or_create_group(group_name)
    print(f"Hermit - chatting in group '{group['name']}'")
    print("Type 'exit' or Ctrl+D to quit\n")

    while True:
        try:
            prompt = input("> ").strip()
            if not prompt:
                continue
            if prompt.lower() == "exit":
                break

            response = chat(group_name, prompt)
            print(f"\n{response}\n")

        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Hermit - Personal Claude assistant with bwrap sandboxing"
    )
    parser.add_argument(
        "-g", "--group",
        default="default",
        help="Group name for conversation isolation (default: 'default')"
    )
    parser.add_argument(
        "-p", "--prompt",
        help="Single prompt to send (non-interactive)"
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize database and exit"
    )

    args = parser.parse_args()

    # Always ensure DB exists
    init_db()

    if args.init:
        print(f"Database initialized at {DB_PATH}")
        return

    if args.prompt:
        # Single prompt mode
        response = chat(args.group, args.prompt)
        print(response)
    else:
        # Interactive REPL
        repl(args.group)


if __name__ == "__main__":
    main()
