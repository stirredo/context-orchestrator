# context-orchestrator

MCP server that gives Claude persistent memory of your tasks, sources, and repo knowledge across sessions.

## What it does

- **Tasks** — group related sources (transcripts, docs, repo links, pasted text) under a task name
- **Repo knowledge** — setup steps, test commands, conventions auto-accumulate as Claude discovers them
- **Cross-session** — mention a task and Claude already knows what files, repos, and URLs are involved

## Setup

```bash
git clone https://github.com/stirredo/context-orchestrator.git
cd context-orchestrator
./setup.sh
```

Then add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "context-orchestrator": {
      "command": "<path-to-clone>/.venv/bin/python",
      "args": ["-m", "context_orchestrator.server"],
      "env": {
        "PYTHONPATH": "<path-to-clone>/src"
      }
    }
  }
}
```

Restart Claude Code.

## Tools

| Tool | Purpose |
|------|---------|
| `create_task` | Create a task scoped to a project (git remote URL) |
| `list_tasks` | List tasks, optionally filtered by project |
| `add_source` | Add a file path, repo URL, link, or inline text to a task |
| `get_task` | Get all sources + repo knowledge for a task |
| `remove_source` | Remove a source from a task |
| `update_repo_knowledge` | Save a learning about a repo (setup, testing, conventions) |
| `get_repo_knowledge` | Get all stored knowledge for a repo |

## How it works

```
You: "I'm working on auth-refactor"

Claude calls get_task("auth-refactor") →

  Task: auth-refactor
  Sources:
    [1] (file) ~/docs/auth-spec.md — Auth spec v2
    [2] (repo) https://github.com/org/backend — Main backend
    [3] (text) Meeting transcript from Feb 22

  Repo knowledge (https://github.com/org/backend):
    - Setup: docker-compose up
    - Tests: pytest -x
    - Uses FastAPI + SQLAlchemy
```

Claude then reads files and clones repos using its built-in tools. No re-explaining.

## Storage

SQLite database at `~/.context-orchestrator/context.db`. Local to each machine, no cloud sync.
