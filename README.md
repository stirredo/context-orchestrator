<p align="left">
  <img src="assets/contorch-orange.png" width="96" alt="Contorch logo">
</p>

# context-orchestrator

> Part of **Contorch** — the persistent context layer for Claude Code. Captures meetings, indexes everything, carries the torch from one Claude session to the next.

MCP server that gives Claude persistent memory of your tasks, sources, and repo knowledge across sessions, with a vector index over everything you've captured.

Pairs with [meeting-capture](https://github.com/contorch/meeting-capture) — that daemon writes meeting transcripts to `~/transcripts/`, and this project's `transcript-watcher` indexes them automatically. Either project runs without the other.

## Requirements

- macOS or Linux
- Python 3.10+
- Claude Code (the MCP server is registered via `claude mcp add`)

## Install

### Recommended — full stack, one command

Installs context-orchestrator + [meeting-capture](https://github.com/contorch/meeting-capture) + [pipeline-monitor](https://github.com/contorch/pipeline-monitor) + the auto-context Claude Code hook + (optional) Gemini activation:

```bash
curl -fsSL https://raw.githubusercontent.com/contorch/context-orchestrator/main/bootstrap.sh | bash
```

The script is idempotent (safe to re-run), installs missing prereqs via Homebrew, prompts for a Gemini API key when it needs one (skip with empty input to stay all-local), and finishes with a clear punch-list of the few things macOS requires you to click yourself: restarting Claude Code so it re-reads `~/.claude/settings.json`, and granting Microphone + Screen Recording permissions in System Settings.

### Just this repo

```bash
git clone https://github.com/contorch/context-orchestrator.git
cd context-orchestrator
./setup.sh
```

`setup.sh` checks prerequisites, creates a Python venv, registers the MCP server with Claude Code (via `claude mcp add`), appends standard usage instructions to `~/.claude/CLAUDE.md`, installs the chroma HTTP server launchd agent (with a one-time backup of any existing chroma data), and installs the `transcript-watcher` launchd auto-start agent.

After either install path, restart Claude Code so the new MCP server is loaded.

To verify:

```bash
.venv/bin/transcript-watcher doctor
```

## MCP tools

| Tool | Purpose |
|---|---|
| `create_task` | Create a task scoped to a project (git remote URL) |
| `list_tasks` | List tasks, optionally filtered by project |
| `get_task` | Get all sources and repo knowledge attached to a task |
| `add_source` | Add a file path, repo URL, link, or inline text to a task |
| `drop` | Quick-save a source without naming a task — auto-attaches to the active meeting or daily inbox |
| `remove_source` | Remove a source from a task |
| `update_repo_knowledge` | Save a learning about a repo (setup, testing, conventions, gotchas) |
| `get_repo_knowledge` | Get all stored knowledge for a repo |
| `search` | Semantic search across all tasks, sources, transcripts, and repo knowledge |

## Components

### MCP server

Long-lived process spawned by Claude Code on session start. Exposes the tools above. Connects to the chroma HTTP server for the vector index and to a local SQLite database at `~/.context-orchestrator/context.db` for task and source metadata.

### chroma server

The vector index is served by a `chroma run` HTTP daemon (launchd-managed) listening on `127.0.0.1:8765`. The MCP server, the watcher, and one-shot CLIs all connect as HTTP clients, which avoids the SQLite-lock contention that `PersistentClient`-per-process configurations are prone to under concurrent access. Override the host or port via the `CO_CHROMA_HOST` / `CO_CHROMA_PORT` environment variables. CLI:

| Command | Purpose |
|---|---|
| `context-orchestrator-chroma status` | Plist state, port listening check, HTTP heartbeat |
| `context-orchestrator-chroma install` | Install and start the launchd agent |
| `context-orchestrator-chroma uninstall` | Stop and remove the launchd agent |

### transcript-watcher

Standalone daemon (launchd-managed) that polls `~/transcripts/` every 5 seconds and indexes new or modified Markdown files into the chroma server, into the same collection used by the MCP `search` tool. CLI:

| Command | Purpose |
|---|---|
| `transcript-watcher status` | Daemon state, watch dir, indexed file count |
| `transcript-watcher doctor` | Full health check |
| `transcript-watcher run` | Run the watch loop in the foreground |
| `transcript-watcher once` | Single-pass scan and exit (cron-friendly) |
| `transcript-watcher install` | Install the launchd auto-start agent |
| `transcript-watcher uninstall` | Remove the launchd auto-start agent |

### save-transcript

Manual transcript capture utility. Saves the current clipboard to `~/transcripts/{date}-{name}.md` and indexes it. Useful when you don't have meeting-capture installed but want to drop a transcript into the index.

## Storage

- `~/.context-orchestrator/context.db` — SQLite (tasks, sources, repo knowledge metadata)
- `~/.context-orchestrator/chroma/` — ChromaDB vector index, served by the chroma daemon (uses the default `all-MiniLM-L6-v2` embeddings; no API key required)
- `~/.context-orchestrator/chroma-daemon.log` — chroma server stdout / stderr
- `~/.context-orchestrator/watcher_state.json` — transcript-watcher mtime cache
- `~/.context-orchestrator/chroma.backup-<timestamp>/` — one-time backup created by `setup.sh` before first switching an existing install to the HTTP server

All local. No cloud sync. Each machine has its own independent state.

## Typical workflows

### Loading task context

```
You: "I'm picking up the auth-refactor work"

Claude → get_task("auth-refactor") →

  Task: auth-refactor
  Sources:
    [1] (file)  ~/docs/auth-spec.md      Auth spec v2
    [2] (repo)  github.com/org/backend   Main backend
    [3] (text)  Meeting summary 2026-04-22

  Repo knowledge for github.com/org/backend:
    - Setup: docker-compose up
    - Tests: pytest -x
    - Uses FastAPI + SQLAlchemy
```

Claude then reads the files and clones the repo using its built-in tools.

### Quick-saving during a meeting

```
You: "drop ~/Downloads/q3-design.pdf, presenter is Sarah"

Claude → drop("~/Downloads/q3-design.pdf", notes="presenter is Sarah") →

  Added file source to task 'meeting-2026-04-26T18-43-05'
  (auto-task: meeting-2026-04-26T18-43-05): /Users/.../q3-design.pdf
```

The PDF is auto-linked to the in-progress meeting transcript. If no meeting is active, it goes to `inbox-YYYY-MM-DD`.

### Recall

```
You: "what was that thing about Q3 architecture Sarah was talking about"

Claude → search("Q3 architecture Sarah") →

  [transcript chunk] meeting-2026-04-26T...md  "...Sarah explained the Q3..."
  [file chunk]       q3-design.pdf             "...service architecture for Q3..."
  [source_notes]                               "presenter is Sarah"
```

Claude reads the relevant files via the returned references and answers.

## Tests

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Architecture notes

- **Project scoping** uses the git remote URL as a stable identifier. Tasks are unique within `(name, project)` so the same task name can exist in different repos without collision.
- **Repo knowledge is append-only.** Calls to `update_repo_knowledge` accumulate insights rather than overwrite, so context grows organically as Claude discovers things.
- **The vector index runs as a separate HTTP service** so the watcher, the MCP server, and one-shot CLIs share a single writer. The on-disk format is identical to `chromadb.PersistentClient`, so existing data carries over without migration; `setup.sh` makes a one-time backup before switching an existing install to the server.
- **The transcript-watcher uses a 2-second settle window** to avoid reading meeting-capture's `.md` files mid-write.
