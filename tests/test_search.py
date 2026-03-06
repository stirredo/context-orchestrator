import tempfile
from pathlib import Path
import pytest
from context_orchestrator.search import VectorSearch


@pytest.fixture
def vs():
    return VectorSearch(chroma_path=Path(tempfile.mkdtemp()))


class TestVectorSearch:
    def test_add_and_search(self, vs):
        vs.add("doc1", "How to set up Docker for development", {"type": "repo_knowledge"})
        vs.add("doc2", "Authentication uses JWT with RS256", {"type": "source"})
        vs.add("doc3", "Run tests with pytest -x", {"type": "repo_knowledge"})

        results = vs.search("docker setup")
        assert len(results) > 0
        assert results[0]["id"] == "doc1"

    def test_semantic_ranking(self, vs):
        vs.add("doc1", "The backend uses PostgreSQL database", {"type": "repo_knowledge"})
        vs.add("doc2", "Frontend is built with React", {"type": "repo_knowledge"})
        vs.add("doc3", "Run database migrations with alembic upgrade head", {"type": "repo_knowledge"})

        results = vs.search("how to migrate the database")
        # Alembic migration should rank higher than just "PostgreSQL"
        ids = [r["id"] for r in results]
        assert ids.index("doc3") < ids.index("doc2")

    def test_search_with_filter(self, vs):
        vs.add("doc1", "Setup instructions", {"type": "repo_knowledge", "project": "proj1"})
        vs.add("doc2", "Setup instructions", {"type": "repo_knowledge", "project": "proj2"})

        results = vs.search("setup", where={"project": "proj1"})
        assert len(results) == 1
        assert results[0]["metadata"]["project"] == "proj1"

    def test_remove(self, vs):
        vs.add("doc1", "Some content", {"type": "test"})
        assert vs.count() == 1
        vs.remove("doc1")
        assert vs.count() == 0

    def test_remove_nonexistent(self, vs):
        # Should not raise
        vs.remove("nonexistent")

    def test_upsert(self, vs):
        vs.add("doc1", "Original content", {"type": "test"})
        vs.add("doc1", "Updated content", {"type": "test"})
        assert vs.count() == 1
        results = vs.search("Updated content")
        assert results[0]["text"] == "Updated content"

    def test_empty_search(self, vs):
        results = vs.search("anything")
        assert results == []

    def test_count(self, vs):
        assert vs.count() == 0
        vs.add("doc1", "Content 1", {"type": "test"})
        vs.add("doc2", "Content 2", {"type": "test"})
        assert vs.count() == 2
