<!-- BEGIN auto-context-section (managed by install-claude-context.sh) -->
## Proactive context loading

A `UserPromptSubmit` hook (`~/.claude/hooks/auto-context.py`) runs on every prompt and pre-injects: top 5 hits from `context-orchestrator` semantic search, current branch, last 8 commits, uncommitted file list. Treat that injected `[auto-context]` block as authoritative — read it, use what's relevant, ignore what isn't, but do NOT re-run those same lookups manually.

When the auto-context block is missing or insufficient, do the following BEFORE answering substantive questions:

1. **context-orchestrator search** — call `mcp__context-orchestrator__search` for any non-trivial question. Don't say "I don't know" without searching first.
2. **Git history** — for questions about *why* something is the way it is, check `git log --oneline -20`, `git log --all --grep=<keyword>`, or `git blame` on relevant files. Past commits + PR squash messages are usually the authoritative record.
3. **GitHub via gh CLI** — for cross-PR or issue context: `gh pr list`, `gh pr view <n>`, `gh issue view <n>`, `gh api repos/:owner/:repo/issues?q=...`.
4. **Google Drive / Gmail / Calendar** — when the user references a doc / email thread / meeting by name, use the `mcp__claude_ai_Google_*` tools to look it up. After reading, call `add_source(source_type="url", reference=<doc-url>)` so the next session finds it via search.

## Context Orchestrator

The context-orchestrator MCP server is available. It provides persistent task and context management across sessions.

### Behaviors
- When I mention working on a task, call `get_task()` to load the full context manifest before doing anything else.
- When creating or listing tasks, auto-detect the project by running `git remote get-url origin` and pass the result as the `project` parameter.
- When I paste links, file paths, or text related to a task, call `add_source()` to persist them.
- When you discover something about a repo (setup steps, test commands, build requirements, conventions, gotchas, PR structure), proactively call `update_repo_knowledge()` to save it. Do not ask — just save it.
- When you read content from a Google Drive doc, Gmail thread, Slack message, or any external URL that informs your answer, call `add_source(source_type="url", reference=<url>, notes=<one-line summary>)` so it shows up in future searches.

### Working directory
- If the current directory is a tasks/notes folder (not a code project), ask me where I want to work before writing any code.
- Look for `workdir/`, `src/`, or a git repo in subdirectories and suggest those.

## Optional MCPs to install when needed

- **Slack**: `claude mcp add --transport sse slack https://mcp.slack.com/mcp` (browser OAuth). Once installed, `add_source` any threads that informed an answer.
- **GitHub MCP**: usually NOT needed — `gh` CLI via Bash covers `pr list`, `pr view`, `issue view`, `api` calls.
<!-- END auto-context-section -->
