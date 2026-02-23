
<!-- BEGIN CONTEXT-ORCHESTRATOR -->
## Context Orchestrator

The context-orchestrator MCP server is available. It provides persistent task and context management across sessions.

### Behaviors
- When I mention working on a task, call `get_task()` to load the full context manifest before doing anything else.
- When creating or listing tasks, auto-detect the project by running `git remote get-url origin` and pass the result as the `project` parameter.
- When I paste links, file paths, or text related to a task, call `add_source()` to persist them.
- When you discover something about a repo (setup steps, test commands, build requirements, conventions, gotchas, PR structure), proactively call `update_repo_knowledge()` to save it. Do not ask — just save it.

### Working directory
- If the current directory is a tasks/notes folder (not a code project), ask me where I want to work before writing any code.
- Look for `workdir/`, `src/`, or a git repo in subdirectories and suggest those.
<!-- END CONTEXT-ORCHESTRATOR -->
