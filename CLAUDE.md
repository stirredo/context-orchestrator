# Context Orchestrator

A task-aware context management MCP server — so Claude automatically knows what context to load when you mention a task, without losing any information across sessions.

## Current State

**Status:** MVP built — MCP server with 7 tools, SQLite storage, ready for use.

## Architecture

Two-layer persistent context system:
- **Task layer** — transcripts, docs, repo links, pasted text scoped to a task
- **Repo layer** — setup instructions, test commands, coding conventions that auto-attach to any task referencing a repo

## MCP Server

The `context-orchestrator` MCP server exposes 7 tools. The server's built-in instructions tell Claude to:
1. Auto-detect the git remote URL and pass it as `project` when creating/listing tasks
2. Auto-load context when the user mentions a task
3. Proactively save repo knowledge (setup, testing, conventions) without being asked
4. Capture sources when the user pastes links, paths, or text

## Files

- `src/context_orchestrator/server.py` — MCP server + tool definitions
- `src/context_orchestrator/db.py` — SQLite database layer
- `pyproject.toml` — project config, depends on `mcp[cli]`
- `.mcp.json` — Claude Code MCP config

## Setup (per laptop)

1. `python3 -m venv .venv && .venv/bin/pip install -e .`
2. Add to `~/.claude/settings.json` for global access, or use `.mcp.json` for this project only
3. Restart Claude Code — server auto-starts

## Key Decisions

- MCP server over CLI/API — native Claude Code integration
- No file content storage — stores paths/URLs only, Claude reads files directly
- Append-style repo knowledge — insights accumulate, never overwritten
- Git remote URL as project identifier — portable across machines
