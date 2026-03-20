"""Ingest files into the context orchestrator — chunk, index, and auto-match to tasks."""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from context_orchestrator.chunking import chunk_text
from context_orchestrator.db import Database
from context_orchestrator.search import VectorSearch

logger = logging.getLogger("context-orchestrator")

INGEST_LOG = Path.home() / ".context-orchestrator" / "ingest.log"
SUPPORTED_EXTENSIONS = {".md", ".txt", ".json", ".py", ".js", ".ts", ".yaml", ".yml"}


def _file_hash(path: Path) -> str:
    """SHA-256 hash of file content for dedup."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _already_ingested(db: Database, file_path: str) -> bool:
    """Check if a file path is already a source in any task."""
    row = db.conn.execute(
        "SELECT id FROM sources WHERE source_type = 'file' AND reference = ?",
        (file_path,),
    ).fetchone()
    return row is not None


def _log_ingest(file_path: str, task_name: Optional[str], action: str):
    """Append to ingest log for auditability."""
    INGEST_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "file": file_path,
        "task": task_name,
        "action": action,
    }
    with open(INGEST_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def index_file_content(vs: VectorSearch, source_id: int, file_path: str,
                       task_name: str = "", project: str = "") -> int:
    """Read a file, chunk it, and index all chunks in ChromaDB.

    Returns the number of chunks indexed.
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return 0

    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return 0

    chunks = chunk_text(content)

    for i, chunk in enumerate(chunks):
        doc_id = f"file_chunk:{source_id}:{i}"
        vs.add(doc_id, chunk, {
            "type": "file_chunk",
            "source_id": str(source_id),
            "chunk_index": i,
            "total_chunks": len(chunks),
            "file_path": file_path,
            "filename": path.name,
            "task_name": task_name,
            "project": project,
        })

    return len(chunks)


def match_task(vs: VectorSearch, db: Database, text: str,
               min_confidence: float = 0.5) -> Optional[dict]:
    """Find the best matching task for a piece of text using vector search.

    Returns the task dict if confidence is above threshold, None otherwise.
    """
    hits = vs.search(text[:500], n_results=5)
    if not hits:
        return None

    # Count which task appears most in top hits
    task_scores: dict[str, float] = {}
    for h in hits:
        task_name = h["metadata"].get("task_name", "")
        if not task_name:
            continue
        # Distance is cosine distance (0 = identical, 2 = opposite)
        # Convert to confidence: 1 - (distance / 2)
        confidence = 1 - (h["distance"] / 2) if h["distance"] is not None else 0
        if task_name not in task_scores or confidence > task_scores[task_name]:
            task_scores[task_name] = confidence

    if not task_scores:
        return None

    best_task_name = max(task_scores, key=task_scores.get)
    best_confidence = task_scores[best_task_name]

    if best_confidence < min_confidence:
        return None

    task = db.get_task_by_name(best_task_name)
    if task:
        task["_match_confidence"] = best_confidence
    return task


def ingest_file(db: Database, vs: VectorSearch, file_path: Path,
                task_name: Optional[str] = None,
                auto_match: bool = True) -> dict:
    """Ingest a single file into the context orchestrator.

    1. Check for duplicates
    2. Auto-match to a task (or use provided task_name)
    3. Add as source in SQLite
    4. Chunk and index in ChromaDB
    5. Log the action

    Returns a result dict with status and details.
    """
    resolved = file_path.expanduser().resolve()
    file_str = str(resolved)

    if not resolved.exists():
        return {"status": "error", "message": f"File not found: {resolved}"}

    if not resolved.is_file():
        return {"status": "error", "message": f"Not a file: {resolved}"}

    if _already_ingested(db, file_str):
        return {"status": "skipped", "message": f"Already ingested: {resolved.name}"}

    # Read content
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"status": "error", "message": f"Cannot read as text: {resolved.name}"}

    if len(content.strip()) < 10:
        return {"status": "skipped", "message": f"File too short: {resolved.name}"}

    # Find or create task
    task = None
    confidence = 0.0
    matched_method = "none"

    if task_name:
        task = db.get_task_by_name(task_name)
        if not task:
            task = db.create_task(task_name, f"Auto-created for {resolved.name}")
            matched_method = "created"
        else:
            matched_method = "explicit"
    elif auto_match:
        task = match_task(vs, db, content)
        if task:
            confidence = task.pop("_match_confidence", 0)
            matched_method = "auto"

    if not task:
        # Create a task from the filename
        stem = resolved.stem
        task = db.create_task(stem, f"Auto-created from transcript: {resolved.name}")
        matched_method = "created"

    # Add as source
    try:
        source = db.add_source(
            task["id"], "file", file_str,
            notes=f"Transcript: {resolved.name}",
        )
    except ValueError:
        return {"status": "skipped", "message": f"Already a source: {resolved.name}"}

    # Index file content in chunks
    num_chunks = index_file_content(
        vs, source["id"], file_str,
        task_name=task["name"],
        project=task.get("project", ""),
    )

    # Also index the notes
    vs.add(f"source_notes:{source['id']}", f"Transcript: {resolved.name}", {
        "type": "source_notes",
        "source_type": "file",
        "reference": file_str,
        "task_name": task["name"],
        "project": task.get("project", ""),
    })

    result = {
        "status": "ingested",
        "file": resolved.name,
        "path": file_str,
        "task": task["name"],
        "match_method": matched_method,
        "confidence": round(confidence, 2),
        "chunks": num_chunks,
        "words": len(content.split()),
    }

    _log_ingest(file_str, task["name"], f"ingested ({matched_method}, conf={confidence:.2f})")
    return result


def ingest_folder(db: Database, vs: VectorSearch, folder: Path,
                  auto_match: bool = True) -> list[dict]:
    """Scan a folder for new files and ingest any that haven't been processed."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        return [{"status": "error", "message": f"Not a directory: {folder}"}]

    results = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix in SUPPORTED_EXTENSIONS and not f.name.startswith("."):
            result = ingest_file(db, vs, f, auto_match=auto_match)
            results.append(result)

    return results
