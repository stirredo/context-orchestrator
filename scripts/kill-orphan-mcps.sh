#!/usr/bin/env bash
# Kill orphaned context-orchestrator MCP server processes.
#
# Claude Code spawns the MCP server as a child of the `claude` CLI. When a
# session is force-quit, the terminal closes without a clean shutdown, or the
# OS sleep breaks stdio, the MCP child can outlive its parent. macOS reparents
# orphaned processes to launchd (PID 1), so PPID=1 is a reliable signal that
# the parent claude is gone and the MCP server is no longer attached to any
# session.
#
# This script does NOT touch MCP servers whose parent is still alive — those
# are children of paused/backgrounded Claude sessions.
#
# Run manually:    ./scripts/kill-orphan-mcps.sh
# Run hourly via launchd:
#   1. cp scripts/kill-orphan-mcps.sh ~/.local/bin/
#   2. Drop a plist in ~/Library/LaunchAgents/com.stirredo.mcp-janitor.plist
#      with StartInterval=3600 and ProgramArguments pointing at the script.

set -euo pipefail

killed=0
# `ps -eo pid,ppid,command` columns are space-padded; awk handles whitespace.
# Match `context_orchestrator.server` (the MCP entrypoint) and skip the
# watcher daemon, which is a different module and should keep running.
while read -r pid ppid; do
    [ -z "$pid" ] && continue
    if [ "$ppid" = "1" ]; then
        echo "killing orphan MCP server pid=$pid (parent claude is gone)"
        if kill -TERM "$pid" 2>/dev/null; then
            killed=$((killed + 1))
        fi
    fi
done < <(
    ps -eo pid,ppid,command \
        | awk '/context_orchestrator\.server/ && !/awk/ {print $1, $2}'
)

if [ "$killed" -eq 0 ]; then
    echo "no orphan MCP servers found"
else
    echo "killed $killed orphan(s)"
fi
