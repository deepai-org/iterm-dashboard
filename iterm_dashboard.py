#!/usr/bin/env python3
"""iTerm2 Workspace Dashboard — a TUI tree view of all open sessions."""

import subprocess
import json
import re
import os
from collections import defaultdict
from dataclasses import dataclass, field


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class Session:
    window_id: int
    window_name: str
    session_id: str
    session_name: str
    tty: str
    profile: str
    columns: int
    rows: int
    bounds: tuple  # (x1, y1, x2, y2)
    # filled in later
    pid: int = 0
    cwd: str = ""
    processes: list = field(default_factory=list)
    uptime: str = ""
    mem_kb: int = 0
    cpu: float = 0.0
    is_claude: bool = False
    running_command: str = ""


@dataclass
class GitStatus:
    branch: str = ""
    dirty: int = 0
    stashes: int = 0
    ahead: int = 0
    behind: int = 0
    has_remote: bool = True
    is_git: bool = False


# ── Data Collection ───────────────────────────────────────────────────────────

def run(cmd, timeout=10):
    """Run a shell command and return stdout."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def get_iterm_sessions():
    """Use AppleScript to get all iTerm2 window/tab/session details."""
    script = '''
    tell application "iTerm2"
        set output to "["
        set first_win to true
        repeat with w in every window
            if not first_win then set output to output & ","
            set first_win to false
            set wId to id of w
            set wName to name of w
            set wBounds to bounds of w
            set output to output & "{\\"wid\\":" & wId
            set output to output & ",\\"wname\\":\\"" & wName & "\\""
            set output to output & ",\\"bounds\\":[" & item 1 of wBounds & "," & item 2 of wBounds & "," & item 3 of wBounds & "," & item 4 of wBounds & "]"
            set output to output & ",\\"sessions\\":["
            set first_sess to true
            set tabCounter to 0
            repeat with t in every tab of w
                set tabCounter to tabCounter + 1
                repeat with s in every session of t
                    if not first_sess then set output to output & ","
                    set first_sess to false
                    try
                        set sName to name of s
                        set sTTY to tty of s
                        set sProfile to profile name of s
                        set sCols to columns of s
                        set sRows to rows of s
                        set sUID to unique ID of s
                        set output to output & "{\\"sid\\":\\"" & sUID & "\\""
                        set output to output & ",\\"sname\\":\\"" & sName & "\\""
                        set output to output & ",\\"tty\\":\\"" & sTTY & "\\""
                        set output to output & ",\\"profile\\":\\"" & sProfile & "\\""
                        set output to output & ",\\"cols\\":" & sCols
                        set output to output & ",\\"rows\\":" & sRows
                        set output to output & ",\\"tab\\":" & tabCounter & "}"
                    on error
                        set output to output & "{\\"error\\":true}"
                    end try
                end repeat
            end repeat
            set output to output & "]}"
        end repeat
        set output to output & "]"
        return output
    end tell
    '''
    raw = run(f"osascript -e '{script}'", timeout=15)
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # AppleScript may produce strings with unescaped chars; try to sanitize
        # Replace literal control chars and retry
        sanitized = re.sub(r'[\x00-\x1f]+', ' ', raw)
        try:
            data = json.loads(sanitized)
        except json.JSONDecodeError:
            print("⚠  Failed to parse iTerm2 data.")
            return []

    sessions = []
    for win in data:
        if "error" in win:
            continue
        for sess in win.get("sessions", []):
            if sess.get("error"):
                continue
            sessions.append(Session(
                window_id=win["wid"],
                window_name=win["wname"],
                session_id=sess["sid"],
                session_name=sess["sname"],
                tty=sess["tty"],
                profile=sess["profile"],
                columns=sess["cols"],
                rows=sess["rows"],
                bounds=tuple(win["bounds"]),
            ))
    return sessions


def strip_iterm_prefix(name):
    """Strip iTerm2 status icons (braille patterns, emoji markers) from window names."""
    # Remove leading braille, emoji, whitespace, and common status markers
    return re.sub(r'^[\s\u2800-\u28FF✳⠐⠂⠈⠁]+\s*', '', name)


def enrich_sessions(sessions):
    """Add process info, CWD, memory, CPU, uptime to each session."""
    # Build a map of tty -> session
    tty_map = {}
    for s in sessions:
        short_tty = s.tty.replace("/dev/", "")
        tty_map[short_tty] = s

    # Get all process info in one call
    ps_output = run("ps -eo tty=,pid=,ppid=,pcpu=,rss=,etime=,command=")
    tty_procs = defaultdict(list)
    for line in ps_output.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        tty, pid, ppid, cpu, rss, etime, cmd = parts
        tty_procs[tty].append({
            "pid": int(pid), "ppid": int(ppid),
            "cpu": float(cpu), "rss": int(rss),
            "etime": etime.strip(), "cmd": cmd.strip(),
        })

    for tty_name, sess in tty_map.items():
        procs = tty_procs.get(tty_name, [])
        sess.processes = procs

        # Find the shell process (bash/zsh) to get CWD
        shell_pid = None
        for p in procs:
            if p["cmd"].startswith("-bash") or p["cmd"].startswith("-zsh") or p["cmd"] == "bash" or p["cmd"] == "zsh":
                shell_pid = p["pid"]
                sess.uptime = p["etime"]
                break

        # If no shell found, use the oldest process
        if shell_pid is None and procs:
            # login process is typically the oldest
            for p in procs:
                if "login" in p["cmd"]:
                    sess.uptime = p["etime"]
                    break

        # Get CWD from the shell PID
        if shell_pid:
            cwd_out = run(f"lsof -a -d cwd -p {shell_pid} -Fn 2>/dev/null | grep '^n/' | sed 's/^n//'")
            sess.cwd = cwd_out if cwd_out else ""

        # Detect Claude instances and compute their resource usage
        for p in procs:
            if p["cmd"].startswith("claude"):
                sess.is_claude = True
                sess.mem_kb = p["rss"]
                sess.cpu = p["cpu"]
                break

        # Count LSP servers (they may be children of claude processes)
        for p in procs:
            if "sourcekit-lsp" in p["cmd"]:
                sess.running_command = "sourcekit-lsp"
                break

        # Detect the primary running command
        if not sess.running_command:
            for p in procs:
                cmd = p["cmd"]
                if any(skip in cmd for skip in ["login ", "-bash", "-zsh", "bash", "zsh"]):
                    continue
                if cmd.startswith("claude"):
                    sess.running_command = "claude"
                elif "clangd" in cmd:
                    pass  # skip, prefer sourcekit-lsp
                elif "caffeinate" in cmd:
                    sess.running_command = "caffeinate"
                else:
                    sess.running_command = cmd.split()[0] if cmd else ""
                break

        # Clean window name
        sess.window_name = strip_iterm_prefix(sess.window_name)
        sess.session_name = strip_iterm_prefix(sess.session_name)


def get_git_status(path):
    """Get git status for a directory."""
    gs = GitStatus()
    if not os.path.isdir(os.path.join(path, ".git")):
        return gs
    gs.is_git = True
    gs.branch = run(f"git -C '{path}' branch --show-current 2>/dev/null") or "HEAD"
    dirty_out = run(f"git -C '{path}' status --porcelain 2>/dev/null")
    gs.dirty = len(dirty_out.splitlines()) if dirty_out else 0
    gs.stashes = int(run(f"git -C '{path}' stash list 2>/dev/null | wc -l").strip() or "0")
    ab = run(f"git -C '{path}' rev-list --left-right --count HEAD...@{{upstream}} 2>/dev/null")
    if ab and "\t" in ab:
        parts = ab.split("\t")
        gs.ahead = int(parts[0])
        gs.behind = int(parts[1])
        gs.has_remote = True
    else:
        gs.has_remote = False
    return gs


# ── Formatting Helpers ────────────────────────────────────────────────────────

def format_bytes(kb):
    """Format kilobytes into human-readable string."""
    if kb == 0:
        return ""
    mb = kb / 1024
    if mb >= 1024:
        return f"{mb/1024:.2f}GB"
    return f"{mb:.0f}MB"


def parse_etime(etime):
    """Parse ps etime format (dd-HH:MM:SS or HH:MM:SS or MM:SS) to seconds."""
    days = 0
    if "-" in etime:
        d, rest = etime.split("-", 1)
        days = int(d)
        etime = rest
    parts = etime.split(":")
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return days * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return days * 86400 + parts[0] * 60 + parts[1]
    return days * 86400


def format_uptime(etime):
    """Convert ps etime to a friendly string like '5d 6h' or '1h 13m'."""
    secs = parse_etime(etime)
    days = secs // 86400
    hours = (secs % 86400) // 3600
    mins = (secs % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def git_badge(gs):
    """Format git status as a compact badge like [main *25 ↑1]."""
    if not gs.is_git:
        return "  [no git]"
    parts = [gs.branch]
    if gs.dirty:
        parts.append(f"*{gs.dirty}")
    else:
        parts.append("✓")
    if gs.has_remote:
        if gs.ahead:
            parts.append(f"↑{gs.ahead}")
        if gs.behind:
            parts.append(f"↓{gs.behind}")
        if not gs.ahead and not gs.behind:
            parts.append("=")
    else:
        parts.append("⊘")
    badge = " ".join(parts)
    return f"  [{badge}]"


def session_line(sess, prefix, is_last, max_name_width):
    """Format a single session line."""
    connector = "└─" if is_last else "├─"
    icon = "🤖" if sess.is_claude else "🖥️ "
    name = sess.window_name
    # Truncate long names
    if len(name) > max_name_width:
        name = name[:max_name_width - 1] + "…"

    tty_short = sess.tty.replace("/dev/", "")

    # Build right-side metrics
    metrics = []
    if sess.is_claude:
        mem = format_bytes(sess.mem_kb)
        if mem:
            metrics.append(f"{mem:>7s}")
        metrics.append(f"{sess.cpu}% cpu".rjust(9))
    metrics.append(tty_short)
    if sess.uptime:
        metrics.append(format_uptime(sess.uptime))

    metrics_str = " │ ".join(metrics)

    # Pad name to align metrics
    pad_total = max_name_width - len(name)
    padding = " " + "─" * (pad_total + 2) + " "

    return f"{prefix}{connector} {icon} {name}{padding}{metrics_str}"


def grouped_shell_line(sessions, prefix, is_last, max_name_width, project_short):
    """Format a line for multiple identical shell sessions grouped together."""
    connector = "└─" if is_last else "├─"
    name = f"Default ({project_short})"
    ttys = sorted([s.tty.replace("/dev/ttys0", "").lstrip("0") or "0" for s in sessions])
    tty_str = "ttys: " + ", ".join(f"{t:>03s}" for t in ttys)

    pad_total = max_name_width - len(name)
    padding = " " + "─" * (pad_total + 2) + " "

    return f"{prefix}{connector} 🖥️  {name}{padding}{tty_str}"


# ── Main Render ───────────────────────────────────────────────────────────────

def render():
    print("\n⏳ Gathering iTerm2 session data...\n")

    sessions = get_iterm_sessions()
    if not sessions:
        print("No iTerm2 sessions found. Is iTerm2 running?")
        return

    enrich_sessions(sessions)

    # Group sessions by CWD (project)
    home = os.path.expanduser("~")
    projects = defaultdict(list)  # project_path -> [sessions]
    unassigned = []

    for s in sessions:
        cwd = s.cwd
        if not cwd:
            unassigned.append(s)
            continue

        # Normalize: get the "project root" — go up to a recognizable project dir
        # Heuristic: use the directory itself, or if it's home, mark as unassigned
        if cwd == home:
            unassigned.append(s)
        else:
            projects[cwd].append(s)

    # Sort projects by number of sessions (descending), then name
    sorted_projects = sorted(projects.items(), key=lambda x: (-len(x[1]), x[0]))

    # Collect all git statuses
    git_statuses = {}
    for path, _ in sorted_projects:
        git_statuses[path] = get_git_status(path)

    # ── Print Tree ────────────────────────────────────────────────────────

    max_name_width = 30  # for alignment
    total_claude = 0
    total_claude_mem = 0
    total_lsp = 0
    high_mem_projects = []
    old_claude = []

    # Count stats
    for s in sessions:
        if s.is_claude:
            total_claude += 1
            total_claude_mem += s.mem_kb
            if s.uptime and parse_etime(s.uptime) > 5 * 86400:
                old_claude.append(s)
        if s.running_command == "sourcekit-lsp":
            total_lsp += 1

    print(f"▼ WORKSPACES ({len(sorted_projects)})")

    for proj_path, sess_list in sorted_projects:
        gs = git_statuses[proj_path]
        proj_name = os.path.basename(proj_path)
        badge = git_badge(gs)
        warning = ""
        if gs.dirty > 20:
            warning = " ⚠️"
            high_mem_projects.append(proj_name)

        print(f"  ▼ 📁 {proj_name}{badge}{warning}")

        # Separate Claude sessions, named sessions, and default shells
        claude_sessions = []
        named_sessions = []
        shell_sessions = []

        for s in sess_list:
            sname_lower = s.window_name.lower()
            if s.is_claude:
                claude_sessions.append(s)
            elif re.match(r'^default\s*\(', sname_lower) or sname_lower.startswith("(-bash") or sname_lower.startswith("(-zsh"):
                shell_sessions.append(s)
            else:
                named_sessions.append(s)

        # Sort Claude sessions by memory (highest first)
        claude_sessions.sort(key=lambda s: -s.mem_kb)
        # Sort named sessions alphabetically
        named_sessions.sort(key=lambda s: s.window_name)

        all_items = claude_sessions + named_sessions
        has_shells = len(shell_sessions) > 0

        for i, s in enumerate(all_items):
            is_last = (i == len(all_items) - 1) and not has_shells
            print(session_line(s, "    ", is_last, max_name_width))

        # Shell sessions — each on its own line
        for i, s in enumerate(shell_sessions):
            is_last = (i == len(shell_sessions) - 1)
            print(session_line(s, "    ", is_last, max_name_width))

    # Unassigned
    if unassigned:
        print(f"\n▼ GLOBAL / UNASSIGNED")
        for i, s in enumerate(unassigned):
            is_last = (i == len(unassigned) - 1)
            print(session_line(s, "  ", is_last, max_name_width))

    # System insights
    print(f"\n▼ SYSTEM INSIGHTS")
    total_mem_str = format_bytes(total_claude_mem)
    print(f"  [!] {total_claude} Claude instances running (~{total_mem_str} RAM total)")

    for proj_path, sess_list in sorted_projects:
        gs = git_statuses[proj_path]
        proj_name = os.path.basename(proj_path)
        for s in sess_list:
            if s.mem_kb > 500 * 1024:  # > 500MB
                print(f"  [!] `{proj_name}` Claude instance using {format_bytes(s.mem_kb)} ({s.window_name})")
        if gs.dirty > 15:
            print(f"  [!] `{proj_name}` has {gs.dirty} dirty files — consider committing")
        if gs.ahead > 3:
            print(f"  [!] `{proj_name}` is {gs.ahead} commits ahead — consider pushing")

    if total_lsp:
        print(f"  [i] {total_lsp} active Swift LSP servers detected")

    if old_claude:
        names = ", ".join(s.window_name for s in old_claude)
        print(f"  [i] Suggestion: Close idle 🤖 instances (>5 days old) to free RAM")

    # Count total sessions
    total = len(sessions)
    shell_count = sum(1 for s in sessions if not s.is_claude and ("Default" in s.window_name or s.window_name.endswith("(-bash)")))
    print(f"\n  {total} windows │ {total_claude} claude │ {shell_count} shells │ {total_lsp} LSP servers\n")


if __name__ == "__main__":
    render()
