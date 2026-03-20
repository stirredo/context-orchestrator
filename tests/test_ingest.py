import tempfile
from pathlib import Path

import pytest

from context_orchestrator.db import Database
from context_orchestrator.search import VectorSearch
from context_orchestrator.ingest import (
    ingest_file,
    ingest_folder,
    index_file_content,
    match_task,
)


@pytest.fixture
def db():
    return Database(db_path=Path(tempfile.mktemp(suffix=".db")))


@pytest.fixture
def vs():
    return VectorSearch(chroma_path=Path(tempfile.mkdtemp()))


@pytest.fixture
def transcript_dir():
    d = Path(tempfile.mkdtemp())
    return d


def _write_file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestIndexFileContent:
    def test_index_creates_chunks(self, db, vs):
        task = db.create_task("test-task")
        source = db.add_source(task["id"], "file", "/tmp/test.md")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("word " * 1200)
            f.flush()
            num_chunks = index_file_content(vs, source["id"], f.name, "test-task")
        assert num_chunks >= 3
        assert vs.count() >= 3

    def test_index_nonexistent_file(self, vs):
        num = index_file_content(vs, 1, "/nonexistent/file.md")
        assert num == 0

    def test_short_file_single_chunk(self, db, vs):
        task = db.create_task("test-task")
        source = db.add_source(task["id"], "file", "/tmp/short.md")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("A short transcript about authentication.")
            f.flush()
            num_chunks = index_file_content(vs, source["id"], f.name, "test-task")
        assert num_chunks == 1


class TestMatchTask:
    def test_match_existing_task(self, db, vs):
        task = db.create_task("auth-refactor", "Refactoring the authentication module")
        # Add some indexed content for this task
        vs.add("source_notes:1", "Discussion about JWT tokens and OAuth2 authentication", {
            "type": "source_notes",
            "task_name": "auth-refactor",
            "project": "",
        })

        result = match_task(vs, db, "We need to update the JWT token validation in auth")
        assert result is not None
        assert result["name"] == "auth-refactor"

    def test_no_match_returns_none(self, db, vs):
        result = match_task(vs, db, "completely unrelated content about cooking")
        assert result is None

    def test_no_match_with_low_confidence(self, db, vs):
        db.create_task("auth-refactor")
        vs.add("source_notes:1", "Authentication module", {
            "type": "source_notes",
            "task_name": "auth-refactor",
            "project": "",
        })
        # Very different content should have low confidence
        result = match_task(vs, db, "quantum physics equations", min_confidence=0.99)
        assert result is None


class TestIngestFile:
    def test_ingest_new_file(self, db, vs, transcript_dir):
        f = _write_file(
            transcript_dir / "2026-03-20-sprint.md",
            "Sprint planning discussion about the new feature rollout and deployment timeline."
        )
        result = ingest_file(db, vs, f)
        assert result["status"] == "ingested"
        assert result["task"] is not None
        assert result["chunks"] >= 1

    def test_ingest_with_explicit_task(self, db, vs, transcript_dir):
        db.create_task("my-task", "Test task")
        f = _write_file(transcript_dir / "notes.md", "Some meeting notes about the project.")
        result = ingest_file(db, vs, f, task_name="my-task")
        assert result["status"] == "ingested"
        assert result["task"] == "my-task"
        assert result["match_method"] == "explicit"

    def test_ingest_creates_task_if_not_exists(self, db, vs, transcript_dir):
        f = _write_file(transcript_dir / "notes.md", "Meeting notes about new project.")
        result = ingest_file(db, vs, f, task_name="new-task")
        assert result["status"] == "ingested"
        assert result["task"] == "new-task"
        assert result["match_method"] == "created"
        # Task should exist in DB
        task = db.get_task_by_name("new-task")
        assert task is not None

    def test_ingest_duplicate_skipped(self, db, vs, transcript_dir):
        f = _write_file(transcript_dir / "notes.md", "Meeting notes content here.")
        result1 = ingest_file(db, vs, f)
        assert result1["status"] == "ingested"
        result2 = ingest_file(db, vs, f)
        assert result2["status"] == "skipped"

    def test_ingest_nonexistent_file(self, db, vs):
        result = ingest_file(db, vs, Path("/nonexistent/file.md"))
        assert result["status"] == "error"

    def test_ingest_empty_file(self, db, vs, transcript_dir):
        f = _write_file(transcript_dir / "empty.md", "")
        result = ingest_file(db, vs, f)
        assert result["status"] == "skipped"

    def test_ingest_searchable(self, db, vs, transcript_dir):
        f = _write_file(
            transcript_dir / "auth-meeting.md",
            "We discussed the OAuth2 implementation and decided to use RS256 for JWT signing."
        )
        ingest_file(db, vs, f)
        # Should be findable via search
        hits = vs.search("JWT signing algorithm")
        assert len(hits) > 0


class TestIngestFolder:
    def test_ingest_multiple_files(self, db, vs, transcript_dir):
        _write_file(transcript_dir / "meeting1.md", "First meeting about project planning and roadmap.")
        _write_file(transcript_dir / "meeting2.txt", "Second meeting about technical architecture decisions.")
        _write_file(transcript_dir / "notes.md", "Some additional notes about the deployment process.")

        results = ingest_folder(db, vs, transcript_dir)
        ingested = [r for r in results if r["status"] == "ingested"]
        assert len(ingested) == 3

    def test_skips_unsupported_extensions(self, db, vs, transcript_dir):
        _write_file(transcript_dir / "image.png", "not really an image")
        _write_file(transcript_dir / "notes.md", "Real transcript content here.")

        results = ingest_folder(db, vs, transcript_dir)
        assert len(results) == 1  # Only .md file

    def test_skips_hidden_files(self, db, vs, transcript_dir):
        _write_file(transcript_dir / ".hidden.md", "Hidden file content.")
        _write_file(transcript_dir / "visible.md", "Visible transcript content.")

        results = ingest_folder(db, vs, transcript_dir)
        assert len(results) == 1

    def test_idempotent(self, db, vs, transcript_dir):
        _write_file(transcript_dir / "notes.md", "Meeting notes about the project timeline.")

        results1 = ingest_folder(db, vs, transcript_dir)
        results2 = ingest_folder(db, vs, transcript_dir)

        assert len([r for r in results1 if r["status"] == "ingested"]) == 1
        assert len([r for r in results2 if r["status"] == "skipped"]) == 1
