import sys
import os
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from context_orchestrator.db import Database

# CRITICAL: Never print to stdout — it corrupts the JSON-RPC protocol.
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("context-orchestrator")

mcp = FastMCP(
    "context-orchestrator",
    instructions="""You have access to the Context Orchestrator — a persistent task and context management system.

IMPORTANT BEHAVIORS:
1. AUTO-DETECT PROJECT: Before creating or listing tasks, detect the current project by running
   `git remote get-url origin` in the working directory. Pass the result as the `project` parameter.
   This scopes tasks to the correct project.

2. AUTO-LOAD CONTEXT: When the user mentions working on a task (e.g., "I'm working on auth-refactor"),
   call get_task() to load the full context manifest. Then read any file sources and clone any repo
   sources as needed.

3. AUTO-LEARN: When you discover something about a repository — setup steps, test commands, build
   requirements, conventions, gotchas, PR structure — proactively call update_repo_knowledge() to
   save it. Do NOT ask the user. Just save it. This knowledge will automatically appear in future
   sessions for any task that references this repo.

4. SOURCE CAPTURE: When the user pastes links, file paths, or text and associates them with a task,
   call add_source() to persist them. Use the appropriate source_type:
   - "file" for local file paths
   - "repo" for GitHub/GitLab repository URLs
   - "url" for other URLs (Slack, Confluence, etc.)
   - "text" for inline text pasted in conversation
""",
)

db_path = os.environ.get("CO_DB_PATH")
db = Database(db_path=Path(db_path) if db_path else None)


@mcp.tool()
def create_task(name: str, description: str = "", project: str = "") -> str:
    """Create a new task to group related context sources.

    Args:
        name: Short, unique name for the task (e.g., "auth-refactor")
        description: What this task is about
        project: Git remote URL to scope this task to a project. Claude should auto-detect this.
    """
    try:
        task = db.create_task(name, description, project)
        proj = f" (project: {project})" if project else ""
        return f"Created task '{task['name']}'{proj}"
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def list_tasks(project: str = "") -> str:
    """List all tasks, optionally filtered by project.

    Args:
        project: Git remote URL to filter tasks by project. If empty, lists all tasks.
    """
    tasks = db.list_tasks(project=project if project else None)
    if not tasks:
        scope = f" for project {project}" if project else ""
        return f"No tasks found{scope}. Create one with create_task()."

    lines = []
    for t in tasks:
        proj = f" [{t['project']}]" if t["project"] else ""
        lines.append(
            f"- {t['name']} ({t['source_count']} sources){proj}: {t['description']}"
        )
    return "\n".join(lines)


@mcp.tool()
def add_source(
    task_name: str, source_type: str, reference: str, notes: str = "", project: str = ""
) -> str:
    """Add a source to a task. Sources can be file paths, URLs, repo links, or inline text.

    Args:
        task_name: Name of the task to add the source to
        source_type: One of "file", "repo", "url", "text"
        reference: The file path, URL, or inline text content
        notes: Optional description of this source
        project: Git remote URL to find the task in the right project
    """
    valid_types = ("file", "repo", "url", "text")
    if source_type not in valid_types:
        return f"Error: source_type must be one of {valid_types}, got '{source_type}'"

    task = db.get_task_by_name(task_name, project=project if project else None)
    if not task:
        return f"Error: No task named '{task_name}'. Use list_tasks() to see available tasks."

    if source_type == "file":
        path = Path(reference).expanduser().resolve()
        if not path.exists():
            return f"Error: File not found: {path}"
        reference = str(path)

    try:
        source = db.add_source(task["id"], source_type, reference, notes)
        return (
            f"Added {source_type} source to task '{task_name}': {reference}"
            + (f" ({notes})" if notes else "")
        )
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def get_task(task_name: str, project: str = "") -> str:
    """Get the full context manifest for a task — all sources plus repo knowledge for linked repos.

    Args:
        task_name: Name of the task
        project: Git remote URL to find the task in the right project
    """
    task = db.get_task_by_name(task_name, project=project if project else None)
    if not task:
        return f"Error: No task named '{task_name}'."

    sources = db.get_sources_for_task(task["id"])

    lines = [
        f"Task: {task['name']}",
        f"Description: {task['description']}",
    ]
    if task["project"]:
        lines.append(f"Project: {task['project']}")
    lines.append(f"Created: {task['created_at']}")
    lines.append("")

    if not sources:
        lines.append("No sources yet. Use add_source() to add files, repos, URLs, or text.")
    else:
        lines.append(f"Sources ({len(sources)}):")
        for s in sources:
            note = f" — {s['notes']}" if s["notes"] else ""
            lines.append(f"  [{s['id']}] ({s['source_type']}) {s['reference']}{note}")

    # Auto-include repo knowledge for linked repos
    repo_sources = [s for s in sources if s["source_type"] == "repo"]
    for repo_source in repo_sources:
        knowledge = db.get_repo_knowledge(repo_source["reference"])
        if knowledge:
            lines.append("")
            lines.append(f"Repo knowledge ({repo_source['reference']}):")
            for k in knowledge:
                lines.append(f"  - {k['insight']}")

    return "\n".join(lines)


@mcp.tool()
def remove_source(task_name: str, source_id: int, project: str = "") -> str:
    """Remove a source from a task by its ID.

    Args:
        task_name: Name of the task (for verification)
        source_id: The numeric ID of the source to remove (shown in get_task output)
        project: Git remote URL to find the task in the right project
    """
    task = db.get_task_by_name(task_name, project=project if project else None)
    if not task:
        return f"Error: No task named '{task_name}'."

    sources = db.get_sources_for_task(task["id"])
    source_ids = {s["id"] for s in sources}
    if source_id not in source_ids:
        return f"Error: Source {source_id} does not belong to task '{task_name}'."

    if db.remove_source(source_id):
        return f"Removed source {source_id} from task '{task_name}'."
    return f"Error: Source {source_id} not found."


@mcp.tool()
def update_repo_knowledge(repo_url: str, insight: str) -> str:
    """Record a learning about a repository — setup steps, test commands, conventions, gotchas.

    Call this proactively whenever you discover something useful about a repo.
    Knowledge accumulates over time and is automatically shown when any task references this repo.

    Args:
        repo_url: The repository URL (e.g., "https://github.com/org/repo")
        insight: A single piece of knowledge (e.g., "Needs Node 18", "Run tests with pytest -x")
    """
    entry = db.update_repo_knowledge(repo_url, insight)
    return f"Saved repo knowledge for {repo_url}: {insight}"


@mcp.tool()
def get_repo_knowledge(repo_url: str) -> str:
    """Get all stored knowledge for a repository.

    Args:
        repo_url: The repository URL
    """
    knowledge = db.get_repo_knowledge(repo_url)
    if not knowledge:
        return f"No knowledge stored for {repo_url}."

    lines = [f"Repo knowledge ({repo_url}):"]
    for k in knowledge:
        lines.append(f"  - {k['insight']} (added {k['created_at']})")
    return "\n".join(lines)


def main():
    logger.info(f"Starting context-orchestrator, db={db.db_path}")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
