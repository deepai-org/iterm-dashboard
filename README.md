# iTerm Dashboard

A macOS menu bar app that shows all open iTerm2 windows grouped by project workspace.

## Features

- **Project grouping** — sessions grouped by working directory with git status badges
- **Claude instance tracking** — memory, CPU, uptime, and busy/idle status for all running Claude Code instances
- **Busy/idle detection** — real-time status indicator showing whether Claude is actively working or waiting for input
- **Companion app tracking** — detects BBEdit, PyCharm, and Finder windows; projects open in these apps appear in the workspace grouping even without terminal sessions
- **Nexus terminal support** — also queries Nexus terminal windows alongside iTerm2
- **Selective window raising** — click a project header to raise only that project's windows; companion app clicks raise only the matching window via AXRaise, not all app windows
- **Color-coded metrics** — semantic colors: blue=navigation, purple=AI/Claude, cyan=remote, green/amber/red=health
- **SF Symbols** — native macOS icons for folders, terminals, CPU, insights
- **Click to focus** — click a session to raise that iTerm2 window, click a companion app to raise its project window
- **Tooltips** — hover for full path, profile, terminal size, and process list
- **Git status** — branch, dirty file count, ahead/behind, no-remote detection (walks up to find repo root)
- **Tree connectors** — pixel-drawn tree lines for visual hierarchy
- **System insights** — warnings for high memory, dirty files, unpushed commits, stale instances
- **Auto-refresh** — updates every 30 seconds, plus on menu open

## Install

No dependencies beyond the system Python 3 and PyObjC (ships with macOS).

```bash
open iTermDashboard.app
```

To launch on login, add `iTermDashboard.app` to System Settings > General > Login Items.

## Useful Commands

```bash
# Launch the menu bar app
open iTermDashboard.app

# Quit from terminal
pkill -f itermdashboard

# Relaunch (quit + open)
pkill -f itermdashboard; sleep 1; open iTermDashboard.app

# View the main script
cat iTermDashboard.app/Contents/MacOS/itermdashboard

# Check if it's running
pgrep -f itermdashboard && echo "running" || echo "not running"

# See its resource usage
ps aux | grep itermdashboard | grep -v grep
```

## How It Works

1. Queries iTerm2 via AppleScript for all windows/tabs/sessions
2. Runs a single `ps` call to get process trees per TTY
3. Runs a single batched `lsof` call to resolve CWDs for all shell PIDs
4. Runs `git status --porcelain=v2 --branch` (one call per project) for git info
5. Caches everything in a background thread; menu renders instantly from cache

## Project Structure

```
iTermDashboard.app/
  Contents/
    Info.plist                  # App bundle config (LSUIElement for menu-bar-only)
    MacOS/itermdashboard        # Main Python executable
    Resources/AppIcon.icns      # Dock/Finder icon
    Resources/AppIcon.png       # Source icon image
```
