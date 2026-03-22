import tempfile
from pathlib import Path

import pytest

from context_orchestrator.db import Database
from context_orchestrator.search import VectorSearch
from context_orchestrator.ingest import index_file_content


@pytest.fixture
def db():
    return Database(db_path=Path(tempfile.mktemp(suffix=".db")))


@pytest.fixture
def vs():
    return VectorSearch(chroma_path=Path(tempfile.mkdtemp()))


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

    def test_chunks_are_searchable(self, db, vs):
        task = db.create_task("test-task")
        source = db.add_source(task["id"], "file", "/tmp/auth.md")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("We discussed OAuth2 implementation and decided to use RS256 for JWT signing.")
            f.flush()
            index_file_content(vs, source["id"], f.name, "test-task")

        hits = vs.search("JWT signing algorithm")
        assert len(hits) > 0
        assert hits[0]["metadata"]["type"] == "file_chunk"
        assert hits[0]["metadata"]["task_name"] == "test-task"

    def test_chunk_metadata_includes_file_path(self, db, vs):
        task = db.create_task("test-task")
        source = db.add_source(task["id"], "file", "/tmp/notes.md")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Some meeting notes about deployment.")
            f.flush()
            index_file_content(vs, source["id"], f.name, "test-task")

        hits = vs.search("deployment")
        assert hits[0]["metadata"]["file_path"] == f.name
