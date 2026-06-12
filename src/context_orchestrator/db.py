import sqlite3
import sys
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("context-orchestrator")

DEFAULT_DB_PATH = Path.home() / ".context-orchestrator" / "context.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, project)
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    reference TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, source_type, reference)
);

CREATE TABLE IF NOT EXISTS repo_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url TEXT NOT NULL,
    insight TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_repo_knowledge_url ON repo_knowledge(repo_url);
CREATE INDEX IF NOT EXISTS idx_sources_task_id ON sources(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);
"""


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        logger.info(f"Database initialized at {self.db_path}")

    def _create_tables(self):
        self.conn.executescript(SCHEMA)
        # lightweight migrations for existing databases
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        if "dwt_project_id" not in cols:
            # Deep Work Timer linkage: a contorch task maps to a dwt PROJECT;
            # granular dwt tasks live under it and get ticked off as work
            # items complete.
            self.conn.execute("ALTER TABLE tasks ADD COLUMN dwt_project_id INTEGER")
        self.conn.commit()

    # --- Tasks ---

    def create_task(self, name: str, description: str = "", project: str = "") -> dict:
        try:
            cur = self.conn.execute(
                "INSERT INTO tasks (name, description, project) VALUES (?, ?, ?)",
                (name, description, project),
            )
            self.conn.commit()
            return self._get_task_by_id(cur.lastrowid)
        except sqlite3.IntegrityError:
            raise ValueError(f"Task '{name}' already exists in project '{project}'")

    def list_tasks(self, project: Optional[str] = None) -> list[dict]:
        if project is not None:
            rows = self.conn.execute(
                """SELECT t.*, COUNT(s.id) as source_count
                   FROM tasks t LEFT JOIN sources s ON t.id = s.task_id
                   WHERE t.project = ?
                   GROUP BY t.id ORDER BY t.created_at DESC""",
                (project,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT t.*, COUNT(s.id) as source_count
                   FROM tasks t LEFT JOIN sources s ON t.id = s.task_id
                   GROUP BY t.id ORDER BY t.created_at DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    def set_dwt_project(self, task_id: int, dwt_project_id: Optional[int]) -> None:
        self.conn.execute(
            "UPDATE tasks SET dwt_project_id = ? WHERE id = ?",
            (dwt_project_id, task_id),
        )
        self.conn.commit()

    def get_task_by_name(self, name: str, project: Optional[str] = None) -> Optional[dict]:
        if project is not None:
            row = self.conn.execute(
                "SELECT * FROM tasks WHERE name = ? AND project = ?",
                (name, project),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM tasks WHERE name = ?", (name,)
            ).fetchone()
        return dict(row) if row else None

    def _get_task_by_id(self, task_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    # --- Sources ---

    def add_source(
        self, task_id: int, source_type: str, reference: str, notes: str = ""
    ) -> dict:
        try:
            cur = self.conn.execute(
                "INSERT INTO sources (task_id, source_type, reference, notes) VALUES (?, ?, ?, ?)",
                (task_id, source_type, reference, notes),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM sources WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)
        except sqlite3.IntegrityError:
            raise ValueError(
                f"Source ({source_type}: {reference}) already exists in this task"
            )

    def get_sources_for_task(self, task_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sources WHERE task_id = ? ORDER BY added_at",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def remove_source(self, source_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- Repo Knowledge ---

    def update_repo_knowledge(self, repo_url: str, insight: str) -> dict:
        cur = self.conn.execute(
            "INSERT INTO repo_knowledge (repo_url, insight) VALUES (?, ?)",
            (repo_url, insight),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM repo_knowledge WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)

    def get_repo_knowledge(self, repo_url: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM repo_knowledge WHERE repo_url = ? ORDER BY created_at",
            (repo_url,),
        ).fetchall()
        return [dict(r) for r in rows]
