#!/usr/bin/env python3
"""
Hermit - Personal Claude assistant with bwrap sandboxing

Usage:
    hermit daemon                     Start the daemon
    hermit send -g GROUP MSG          Send message to group
    hermit task add -g GROUP CRON MSG Schedule a task
    hermit task list                  List scheduled tasks
    hermit task rm ID                 Remove a task
    hermit groups                     List groups
    hermit status                     Check daemon status
"""

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.resolve()
GROUPS_DIR = BASE_DIR / "groups"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hermit.db"
SOCKET_PATH = DATA_DIR / "hermit.sock"
PID_FILE = DATA_DIR / "hermit.pid"

SCHEDULER_INTERVAL = 60  # Check for due tasks every 60 seconds
HOT_RELOAD_INTERVAL = 2  # Check for code changes every 2 seconds

# Tools directory
HERMIT_DIR = Path.home() / ".hermit"
TOOLS_DIR = HERMIT_DIR / "tools"
CONFIG_DIR = HERMIT_DIR / "config"  # For tool configs (gh, etc.)

# Static binary URLs (x86_64 Linux)
TOOL_URLS = {
    "gh": "https://github.com/cli/cli/releases/download/v2.65.0/gh_2.65.0_linux_amd64.tar.gz",
    "jq": "https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-amd64",
    "yq": "https://github.com/mikefarah/yq/releases/download/v4.44.1/yq_linux_amd64",
    "rg": "https://github.com/BurntSushi/ripgrep/releases/download/14.1.0/ripgrep-14.1.0-x86_64-unknown-linux-musl.tar.gz",
    "fd": "https://github.com/sharkdp/fd/releases/download/v10.2.0/fd-v10.2.0-x86_64-unknown-linux-musl.tar.gz",
    "fzf": "https://github.com/junegunn/fzf/releases/download/v0.56.3/fzf-0.56.3-linux_amd64.tar.gz",
}

# ============================================================================
# Database
# ============================================================================

def init_db():
    """Initialize SQLite database."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            folder TEXT UNIQUE NOT NULL,
            session_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups(id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            group_name TEXT NOT NULL,
            cron TEXT NOT NULL,
            prompt TEXT NOT NULL,
            next_run TEXT,
            last_run TEXT,
            last_result TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_next_run ON tasks(next_run);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    """)
    conn.close()


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_or_create_group(name: str) -> dict:
    """Get or create a group by name."""
    folder = name.lower().replace(" ", "-")
    group_path = GROUPS_DIR / folder
    group_path.mkdir(parents=True, exist_ok=True)

    conn = get_db()
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
        group = {"id": cur.lastrowid, "name": name, "folder": folder, "session_id": None}

    conn.close()
    return group


def update_session(group_name: str, session_id: str | None):
    """Update the session ID for a group."""
    conn = get_db()
    conn.execute("UPDATE groups SET session_id = ? WHERE name = ?", (session_id, group_name))
    conn.commit()
    conn.close()


def list_groups() -> list[dict]:
    """List all groups."""
    conn = get_db()
    rows = conn.execute("SELECT name, folder, session_id, created_at FROM groups ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================================
# Tasks / Scheduler
# ============================================================================

def parse_cron(cron: str) -> dict | None:
    """Parse simple cron expression. Supports: @hourly, @daily, @weekly, */N, or once:DATETIME."""
    cron = cron.strip()

    if cron.lower() == "@hourly":
        return {"type": "interval", "minutes": 60}
    elif cron.lower() == "@daily":
        return {"type": "interval", "minutes": 1440}
    elif cron.lower() == "@weekly":
        return {"type": "interval", "minutes": 10080}
    elif cron.lower().startswith("*/"):
        try:
            minutes = int(cron[2:])
            if minutes > 0:
                return {"type": "interval", "minutes": minutes}
        except ValueError:
            pass
    elif cron.lower().startswith("once:"):
        time_str = cron[5:].strip()
        try:
            # Support +Nm for "N minutes from now"
            if time_str.startswith("+") and time_str.endswith("m"):
                minutes = int(time_str[1:-1])
                return {"type": "once", "minutes": minutes}
            # Otherwise parse as ISO datetime
            run_time = datetime.fromisoformat(time_str)
            return {"type": "once", "datetime": run_time.isoformat()}
        except (ValueError, TypeError):
            pass

    return None


def calc_next_run(cron: str, from_time: datetime | None = None, after_run: bool = False) -> str | None:
    """Calculate next run time based on cron expression."""
    parsed = parse_cron(cron)
    if not parsed:
        return None

    base = from_time or datetime.now()

    if parsed["type"] == "interval":
        next_time = base.timestamp() + (parsed["minutes"] * 60)
        return datetime.fromtimestamp(next_time).isoformat()
    elif parsed["type"] == "once":
        if after_run:
            return None  # No next run after one-time task completes
        if "minutes" in parsed:
            next_time = base.timestamp() + (parsed["minutes"] * 60)
            return datetime.fromtimestamp(next_time).isoformat()
        return parsed.get("datetime")

    return None


def create_task(group_name: str, cron: str, prompt: str) -> dict:
    """Create a scheduled task."""
    parsed = parse_cron(cron)
    if not parsed:
        return {"status": "error", "error": f"Invalid cron: {cron}. Use @hourly, @daily, @weekly, */N, once:+Nm, or once:DATETIME"}

    task_id = str(uuid.uuid4())[:8]
    next_run = calc_next_run(cron)

    conn = get_db()
    conn.execute(
        "INSERT INTO tasks (id, group_name, cron, prompt, next_run, status) VALUES (?, ?, ?, ?, ?, 'active')",
        (task_id, group_name, cron, prompt, next_run)
    )
    conn.commit()
    conn.close()

    return {"status": "ok", "task_id": task_id, "next_run": next_run}


def list_tasks() -> list[dict]:
    """List all tasks."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, group_name, cron, prompt, next_run, last_run, last_result, status FROM tasks ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_task(task_id: str) -> dict:
    """Delete a task."""
    conn = get_db()
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()

    if cur.rowcount > 0:
        return {"status": "ok", "message": f"Task {task_id} deleted"}
    return {"status": "error", "error": f"Task {task_id} not found"}


def get_due_tasks() -> list[dict]:
    """Get tasks that are due to run."""
    now = datetime.now().isoformat()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status = 'active' AND next_run IS NOT NULL AND next_run <= ?",
        (now,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def install_tool(name: str) -> dict:
    """Download and install a tool to ~/.hermit/tools/"""
    import tarfile
    import urllib.request
    import shutil

    if name not in TOOL_URLS:
        available = ", ".join(TOOL_URLS.keys())
        return {"status": "error", "error": f"Unknown tool: {name}. Available: {available}"}

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    url = TOOL_URLS[name]
    tool_path = TOOLS_DIR / name

    print(f"Downloading {name}...")

    try:
        if url.endswith(".tar.gz"):
            # Download and extract tarball
            tmp_tar = TOOLS_DIR / f"{name}.tar.gz"
            urllib.request.urlretrieve(url, tmp_tar)

            with tarfile.open(tmp_tar, "r:gz") as tar:
                # Find the binary in the archive
                for member in tar.getmembers():
                    if member.name.endswith(f"/{name}") or member.name == name:
                        member.name = name  # Flatten path
                        tar.extract(member, TOOLS_DIR)
                        break
                else:
                    # Try to find binary by common patterns
                    for member in tar.getmembers():
                        basename = Path(member.name).name
                        if basename == name or basename.startswith(name):
                            if member.isfile():
                                member.name = name
                                tar.extract(member, TOOLS_DIR)
                                break

            tmp_tar.unlink()
        else:
            # Direct binary download
            urllib.request.urlretrieve(url, tool_path)

        # Make executable
        tool_path.chmod(0o755)
        return {"status": "ok", "message": f"Installed {name} to {tool_path}"}

    except Exception as e:
        return {"status": "error", "error": f"Failed to install {name}: {e}"}


def list_tools() -> list[str]:
    """List installed tools."""
    if not TOOLS_DIR.exists():
        return []
    return [f.name for f in TOOLS_DIR.iterdir() if f.is_file() and os.access(f, os.X_OK)]


def log_message(group_folder: str, role: str, content: str):
    """Append message to group's history file."""
    history_file = GROUPS_DIR / group_folder / "history.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(history_file, "a") as f:
        f.write(f"--- {timestamp} ---\n")
        if role == "user":
            f.write(f"> {content}\n\n")
        else:
            f.write(f"{content}\n\n")


def update_task_after_run(task_id: str, result: str, cron: str):
    """Update task after execution."""
    now = datetime.now()
    next_run = calc_next_run(cron, now, after_run=True)

    # One-time tasks get marked completed
    status = "completed" if next_run is None else "active"

    conn = get_db()
    conn.execute(
        "UPDATE tasks SET last_run = ?, last_result = ?, next_run = ?, status = ? WHERE id = ?",
        (now.isoformat(), result[:500], next_run, status, task_id)
    )
    conn.commit()
    conn.close()


def get_gh_token() -> str | None:
    """Read GH token from hermit's config (not user's personal config)."""
    hosts_file = CONFIG_DIR / "gh" / "hosts.yml"
    if not hosts_file.exists():
        return None
    try:
        import re
        content = hosts_file.read_text()
        # Simple YAML parsing for oauth_token
        match = re.search(r'oauth_token:\s*(\S+)', content)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


# ============================================================================
# Sandbox
# ============================================================================

def build_bwrap_args(group: dict) -> list[str]:
    """Build bwrap command arguments."""
    group_path = GROUPS_DIR / group["folder"]

    args = [
        "bwrap",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", "/etc/ssl", "/etc/ssl",
        "--ro-bind", "/etc/pki", "/etc/pki",  # Fedora CA certs
        "--symlink", "/usr/bin", "/sbin",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--bind", str(group_path), "/workspace",
        "--tmpfs", "/home",
    ]

    home = Path.home()
    claude_bin = home / ".local" / "bin"
    claude_share = home / ".local" / "share" / "claude"
    user_claude_creds = home / ".claude" / ".credentials.json"

    # Hermit has its own .claude directory (not user's)
    hermit_claude = HERMIT_DIR / ".claude"
    hermit_claude.mkdir(parents=True, exist_ok=True)

    # Copy user's credentials if hermit doesn't have its own yet
    hermit_creds = hermit_claude / ".credentials.json"
    if user_claude_creds.exists() and not hermit_creds.exists():
        import shutil
        shutil.copy2(user_claude_creds, hermit_creds)

    args.extend(["--dir", str(home)])

    # Mount hermit's .claude as ~/.claude (isolated from user's plugins/settings)
    if hermit_claude.exists():
        args.extend(["--bind", str(hermit_claude), str(home / ".claude")])
    if claude_bin.exists():
        args.extend(["--dir", str(home / ".local")])
        args.extend(["--ro-bind", str(claude_bin), str(claude_bin)])
    if claude_share.exists():
        args.extend(["--ro-bind", str(claude_share), str(claude_share)])

    # Mount hermit tools directory
    if TOOLS_DIR.exists():
        args.extend(["--ro-bind", str(TOOLS_DIR), str(TOOLS_DIR)])

    # Build PATH with available tools
    path_parts = [str(claude_bin), "/usr/bin", "/bin"]
    if TOOLS_DIR.exists():
        path_parts.insert(0, str(TOOLS_DIR))

    args.extend([
        "--setenv", "HOME", str(home),
        "--setenv", "USER", home.name,
        "--setenv", "PATH", ":".join(path_parts),
        "--chdir", "/workspace",
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
    ])

    # Pass GH_TOKEN from hermit's config (don't mount config files)
    gh_token = get_gh_token()
    if gh_token:
        args.extend(["--setenv", "GH_TOKEN", gh_token])

    return args


def run_sandbox(group: dict, prompt: str, session_id: str | None = None) -> dict:
    """Run Claude Code in bwrap sandbox."""
    bwrap_args = build_bwrap_args(group)

    cmd = bwrap_args + ["claude", "-p", "--output-format", "json", "--dangerously-skip-permissions"]

    if session_id:
        cmd.extend(["--resume", session_id])

    cmd.append(prompt)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "error": f"Claude exited with code {result.returncode}: {result.stderr[-500:]}"
            }

        try:
            output = json.loads(result.stdout)
            return {
                "status": "success",
                "result": output.get("result", result.stdout),
                "session_id": output.get("session_id")
            }
        except json.JSONDecodeError:
            return {"status": "success", "result": result.stdout}

    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Sandbox timed out after 5 minutes"}
    except FileNotFoundError as e:
        return {"status": "error", "error": f"Command not found: {e}"}


# ============================================================================
# Daemon
# ============================================================================

class Daemon:
    """Hermit daemon - manages sessions, scheduler, and handles requests."""

    def __init__(self, hot_reload=False):
        self.running = False
        self.scheduler_thread = None
        self.hot_reload = hot_reload
        self.script_path = Path(__file__).resolve()
        self.script_mtime = self.script_path.stat().st_mtime

    def check_reload(self):
        """Check if script changed and re-exec if so."""
        while self.running and self.hot_reload:
            try:
                current_mtime = self.script_path.stat().st_mtime
                if current_mtime != self.script_mtime:
                    print("\nCode changed, reloading...")
                    SOCKET_PATH.unlink(missing_ok=True)
                    PID_FILE.unlink(missing_ok=True)
                    os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as e:
                print(f"Reload check error: {e}")
            time.sleep(HOT_RELOAD_INTERVAL)

    def run_scheduler(self):
        """Scheduler loop - runs due tasks."""
        print("Scheduler started")
        while self.running:
            try:
                due_tasks = get_due_tasks()
                for task in due_tasks:
                    print(f"Running task {task['id']}: {task['prompt'][:50]}...")
                    group = get_or_create_group(task["group_name"])
                    result = run_sandbox(group, task["prompt"], group.get("session_id"))

                    # Log to history
                    log_message(group["folder"], "user", f"[task:{task['id']}] {task['prompt']}")
                    result_text = result.get("result", result.get("error", ""))
                    if result_text:
                        log_message(group["folder"], "assistant", result_text)

                    # Update session if task uses group context
                    if result.get("session_id"):
                        update_session(task["group_name"], result["session_id"])

                    update_task_after_run(task["id"], result_text, task["cron"])
                    print(f"Task {task['id']} completed")
            except Exception as e:
                print(f"Scheduler error: {e}")

            time.sleep(SCHEDULER_INTERVAL)

    def handle_request(self, data: dict) -> dict:
        """Handle a request from a client."""
        cmd = data.get("cmd")

        if cmd == "ping":
            return {"status": "ok", "message": "pong"}

        elif cmd == "send":
            group_name = data.get("group", "default")
            prompt = data.get("prompt", "")

            if not prompt:
                return {"status": "error", "error": "No prompt provided"}

            group = get_or_create_group(group_name)
            result = run_sandbox(group, prompt, group.get("session_id"))

            # Log to history
            log_message(group["folder"], "user", prompt)
            if result.get("result"):
                log_message(group["folder"], "assistant", result["result"])

            if result.get("session_id"):
                update_session(group_name, result["session_id"])

            return result

        elif cmd == "groups":
            return {"status": "ok", "groups": list_groups()}

        elif cmd == "new_session":
            group_name = data.get("group", "default")
            update_session(group_name, None)
            return {"status": "ok", "message": f"Session cleared for {group_name}"}

        elif cmd == "task_add":
            return create_task(
                data.get("group", "default"),
                data.get("cron", ""),
                data.get("prompt", "")
            )

        elif cmd == "task_list":
            return {"status": "ok", "tasks": list_tasks()}

        elif cmd == "task_rm":
            return delete_task(data.get("task_id", ""))

        else:
            return {"status": "error", "error": f"Unknown command: {cmd}"}

    def handle_client(self, conn: socket.socket):
        """Handle a single client connection."""
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            if data:
                request = json.loads(data.decode())
                response = self.handle_request(request)
                conn.sendall(json.dumps(response).encode() + b"\n")
        except Exception as e:
            try:
                conn.sendall(json.dumps({"status": "error", "error": str(e)}).encode() + b"\n")
            except:
                pass
        finally:
            conn.close()

    def run(self):
        """Run the daemon."""
        init_db()

        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        PID_FILE.write_text(str(os.getpid()))

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(SOCKET_PATH))
        sock.listen(5)

        print(f"Hermit daemon listening on {SOCKET_PATH}")
        self.running = True

        # Start scheduler thread
        self.scheduler_thread = threading.Thread(target=self.run_scheduler, daemon=True)
        self.scheduler_thread.start()

        # Start hot reload thread if enabled
        if self.hot_reload:
            reload_thread = threading.Thread(target=self.check_reload, daemon=True)
            reload_thread.start()
            print("Hot reload enabled")

        try:
            while self.running:
                conn, _ = sock.accept()
                thread = threading.Thread(target=self.handle_client, args=(conn,))
                thread.daemon = True
                thread.start()
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.running = False
            sock.close()
            SOCKET_PATH.unlink(missing_ok=True)
            PID_FILE.unlink(missing_ok=True)


# ============================================================================
# Client
# ============================================================================

def send_to_daemon(request: dict) -> dict:
    """Send a request to the daemon."""
    if not SOCKET_PATH.exists():
        return {"status": "error", "error": "Daemon not running. Start with: hermit daemon"}

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(SOCKET_PATH))
        sock.sendall(json.dumps(request).encode() + b"\n")

        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

        return json.loads(data.decode())
    finally:
        sock.close()


# ============================================================================
# CLI
# ============================================================================

def cmd_daemon(args):
    """Start the daemon."""
    if SOCKET_PATH.exists():
        if args.force:
            SOCKET_PATH.unlink()
        else:
            print("Daemon may already be running. Use --force to override:")
            print(f"  hermit daemon --force")
            sys.exit(1)

    daemon = Daemon(hot_reload=args.reload)
    daemon.run()


def cmd_send(args):
    """Send a message."""
    prompt = " ".join(args.prompt) if args.prompt else None

    if not prompt:
        print("Error: No prompt provided", file=sys.stderr)
        sys.exit(1)

    response = send_to_daemon({
        "cmd": "send",
        "group": args.group,
        "prompt": prompt
    })

    if response.get("status") == "error":
        print(f"Error: {response.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(response.get("result", ""))


def cmd_groups(args):
    """List groups."""
    response = send_to_daemon({"cmd": "groups"})

    if response.get("status") == "error":
        print(f"Error: {response.get('error')}", file=sys.stderr)
        sys.exit(1)

    groups = response.get("groups", [])
    if not groups:
        print("No groups yet.")
        return

    for g in groups:
        session = "active" if g.get("session_id") else "none"
        print(f"  {g['name']}: session={session}")


def cmd_status(args):
    """Check daemon status."""
    response = send_to_daemon({"cmd": "ping"})

    if response.get("status") == "error":
        print(f"Daemon: not running")
        print(f"  Start with: hermit daemon")
        sys.exit(1)

    print("Daemon: running")


def cmd_new(args):
    """Start a new session (clear existing)."""
    response = send_to_daemon({
        "cmd": "new_session",
        "group": args.group
    })

    if response.get("status") == "error":
        print(f"Error: {response.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(response.get("message"))


def cmd_repl(args):
    """Interactive REPL via daemon."""
    print(f"Hermit - chatting in group '{args.group}'")
    print("Type 'exit' or Ctrl+D to quit\n")

    while True:
        try:
            prompt = input("> ").strip()
            if not prompt:
                continue
            if prompt.lower() == "exit":
                break
            if prompt.lower() == "/new":
                send_to_daemon({"cmd": "new_session", "group": args.group})
                print("Session cleared.\n")
                continue

            response = send_to_daemon({
                "cmd": "send",
                "group": args.group,
                "prompt": prompt
            })

            if response.get("status") == "error":
                print(f"Error: {response.get('error')}\n")
            else:
                print(f"\n{response.get('result', '')}\n")

        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break


def cmd_task_add(args):
    """Add a scheduled task."""
    prompt = " ".join(args.prompt) if args.prompt else None

    if not prompt:
        print("Error: No prompt provided", file=sys.stderr)
        sys.exit(1)

    response = send_to_daemon({
        "cmd": "task_add",
        "group": args.group,
        "cron": args.cron,
        "prompt": prompt
    })

    if response.get("status") == "error":
        print(f"Error: {response.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(f"Task {response.get('task_id')} created. Next run: {response.get('next_run')}")


def cmd_task_list(args):
    """List scheduled tasks."""
    response = send_to_daemon({"cmd": "task_list"})

    if response.get("status") == "error":
        print(f"Error: {response.get('error')}", file=sys.stderr)
        sys.exit(1)

    tasks = response.get("tasks", [])
    if not tasks:
        print("No scheduled tasks.")
        return

    for t in tasks:
        status = t.get("status", "?")
        print(f"  [{t['id']}] {t['group_name']} | {t['cron']} | {status}")
        print(f"      Prompt: {t['prompt'][:60]}...")
        if t.get("next_run"):
            print(f"      Next: {t['next_run']}")
        if t.get("last_result"):
            print(f"      Last: {t['last_result'][:60]}...")
        print()


def cmd_task_rm(args):
    """Remove a scheduled task."""
    response = send_to_daemon({
        "cmd": "task_rm",
        "task_id": args.task_id
    })

    if response.get("status") == "error":
        print(f"Error: {response.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(response.get("message"))


def cmd_tools_install(args):
    """Install a tool."""
    result = install_tool(args.tool)
    if result.get("status") == "error":
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)
    print(result.get("message"))


def cmd_tools_list(args):
    """List installed tools."""
    tools = list_tools()
    if not tools:
        print("No tools installed. Available:")
        for name in TOOL_URLS:
            print(f"  hermit tools install {name}")
        return
    print("Installed tools:")
    for t in tools:
        print(f"  {t}")
    print("\nAvailable:")
    for name in TOOL_URLS:
        if name not in tools:
            print(f"  hermit tools install {name}")


def cmd_auth(args):
    """Authenticate a tool for hermit's sandbox."""
    tool = args.tool

    if tool == "gh":
        # Create hermit's gh config directory
        gh_config_dir = CONFIG_DIR / "gh"
        gh_config_dir.mkdir(parents=True, exist_ok=True)

        # Check if gh is installed
        gh_path = TOOLS_DIR / "gh"
        if not gh_path.exists():
            print("gh not installed. Installing...")
            result = install_tool("gh")
            if result.get("status") == "error":
                print(f"Error: {result.get('error')}")
                sys.exit(1)

        # Run gh auth login with hermit's config
        env = os.environ.copy()
        env["GH_CONFIG_DIR"] = str(gh_config_dir)

        print(f"Authenticating gh for hermit (config: {gh_config_dir})")
        subprocess.run([str(gh_path), "auth", "login", "-h", "github.com"], env=env)

        print(f"\nDone. gh auth stored in {gh_config_dir}")
    else:
        print(f"Unknown tool: {tool}. Supported: gh")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Hermit - Personal Claude assistant with bwrap sandboxing"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Start the daemon")
    p_daemon.add_argument("-f", "--force", action="store_true", help="Force start (remove stale socket)")
    p_daemon.add_argument("-r", "--reload", action="store_true", help="Hot reload on code changes")
    p_daemon.set_defaults(func=cmd_daemon)

    # send
    p_send = subparsers.add_parser("send", help="Send a message")
    p_send.add_argument("-g", "--group", default="default", help="Group name")
    p_send.add_argument("prompt", nargs="*", help="Message to send")
    p_send.set_defaults(func=cmd_send)

    # groups
    p_groups = subparsers.add_parser("groups", help="List groups")
    p_groups.set_defaults(func=cmd_groups)

    # status
    p_status = subparsers.add_parser("status", help="Check daemon status")
    p_status.set_defaults(func=cmd_status)

    # new
    p_new = subparsers.add_parser("new", help="Start new session (clear existing)")
    p_new.add_argument("-g", "--group", default="default", help="Group name")
    p_new.set_defaults(func=cmd_new)

    # repl
    p_repl = subparsers.add_parser("repl", help="Interactive REPL")
    p_repl.add_argument("-g", "--group", default="default", help="Group name")
    p_repl.set_defaults(func=cmd_repl)

    # task (subcommand group)
    p_task = subparsers.add_parser("task", help="Manage scheduled tasks")
    task_sub = p_task.add_subparsers(dest="task_cmd")

    # task add
    p_task_add = task_sub.add_parser("add", help="Add a scheduled task")
    p_task_add.add_argument("-g", "--group", default="default", help="Group name")
    p_task_add.add_argument("-c", "--cron", required=True, help="Schedule: @hourly, @daily, @weekly, */N (minutes), once:+Nm, once:DATETIME")
    p_task_add.add_argument("prompt", nargs="*", help="Task prompt")
    p_task_add.set_defaults(func=cmd_task_add)

    # task list
    p_task_list = task_sub.add_parser("list", help="List scheduled tasks")
    p_task_list.set_defaults(func=cmd_task_list)

    # task rm
    p_task_rm = task_sub.add_parser("rm", help="Remove a task")
    p_task_rm.add_argument("task_id", help="Task ID to remove")
    p_task_rm.set_defaults(func=cmd_task_rm)

    # auth
    p_auth = subparsers.add_parser("auth", help="Authenticate tools for sandbox")
    p_auth.add_argument("tool", help="Tool to authenticate (gh)")
    p_auth.set_defaults(func=cmd_auth)

    # tools (subcommand group)
    p_tools = subparsers.add_parser("tools", help="Manage sandbox tools")
    tools_sub = p_tools.add_subparsers(dest="tools_cmd")

    # tools install
    p_tools_install = tools_sub.add_parser("install", help="Install a tool")
    p_tools_install.add_argument("tool", help="Tool name (gh, jq, yq, rg, fd, fzf)")
    p_tools_install.set_defaults(func=cmd_tools_install)

    # tools list
    p_tools_list = tools_sub.add_parser("list", help="List installed/available tools")
    p_tools_list.set_defaults(func=cmd_tools_list)

    # init (standalone, no daemon)
    p_init = subparsers.add_parser("init", help="Initialize database")
    p_init.set_defaults(func=lambda a: (init_db(), print(f"Database initialized at {DB_PATH}")))

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "task" and getattr(args, "task_cmd", None) is None:
        p_task.print_help()
        sys.exit(1)

    if args.command == "tools" and getattr(args, "tools_cmd", None) is None:
        p_tools.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
