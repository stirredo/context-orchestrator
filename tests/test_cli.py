import tempfile
from pathlib import Path

import pytest

from context_orchestrator.search import VectorSearch
from context_orchestrator.cli import index_transcript


@pytest.fixture
def vs():
    return VectorSearch(chroma_path=Path(tempfile.mkdtemp()))


class TestIndexTranscript:
    def test_index_short_transcript(self, vs):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Discussion about authentication tokens and session management.")
            f.flush()
            num_chunks = index_transcript(vs, Path(f.name))
        assert num_chunks == 1
        assert vs.count() == 1

    def test_index_long_transcript(self, vs):
        # Distinct tokens per word — guarantees uniqueness ratio is high so the
        # hallucination filter doesn't drop these chunks. (Same-word filler like
        # "word " * 1200 would correctly be dropped as low-entropy junk.)
        text = " ".join(f"token{i}" for i in range(1200))
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(text)
            f.flush()
            num_chunks = index_transcript(vs, Path(f.name))
        assert num_chunks >= 3

    def test_indexed_transcript_searchable(self, vs):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("We need to migrate the database from MySQL to PostgreSQL by end of quarter.")
            f.flush()
            index_transcript(vs, Path(f.name))

        hits = vs.search("database migration")
        assert len(hits) > 0
        assert hits[0]["metadata"]["type"] == "transcript"

    def test_metadata_includes_filename(self, vs):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, prefix="sprint-") as f:
            f.write("Sprint planning notes for the week.")
            f.flush()
            index_transcript(vs, Path(f.name))

        hits = vs.search("sprint planning")
        assert "sprint-" in hits[0]["metadata"]["filename"]
        assert hits[0]["metadata"]["file_path"] == f.name
