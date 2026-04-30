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


class TestMMR:
    def test_mmr_returns_n_results(self, vs):
        # Add 6 docs about overlapping topics
        vs.add("a1", "PostgreSQL database connection pooling configuration", {"file": "a"})
        vs.add("a2", "PostgreSQL replication failover and read replicas", {"file": "a"})
        vs.add("a3", "PostgreSQL query planner and index tuning", {"file": "a"})
        vs.add("b1", "Redis caching strategy with TTL keys", {"file": "b"})
        vs.add("b2", "Redis cluster sharding and resharding", {"file": "b"})
        vs.add("c1", "Kafka topic partitioning and consumer groups", {"file": "c"})

        results = vs.search("database query performance", n_results=4, mmr=True)
        assert len(results) == 4
        # All results have id, text, metadata, distance — but no embedding
        assert "embedding" not in results[0]
        assert "sim_to_query" not in results[0]

    def test_mmr_increases_file_diversity(self, vs):
        # Heavily-clustered corpus: 5 PG docs + 2 Redis + 1 Kafka
        for i in range(5):
            vs.add(f"pg{i}", f"PostgreSQL transaction isolation level {i}", {"file": "postgres"})
        for i in range(2):
            vs.add(f"r{i}", f"Redis pub/sub channel pattern {i}", {"file": "redis"})
        vs.add("k1", "Kafka topic compaction policy", {"file": "kafka"})

        # Vanilla cosine ranking: top-3 likely all PG (highest topical match)
        vanilla = vs.search("database transactions", n_results=3, mmr=False)
        vanilla_files = {r["metadata"]["file"] for r in vanilla}

        # MMR should pull in distinct files
        mmr = vs.search("database transactions", n_results=3, mmr=True, mmr_lambda=0.5)
        mmr_files = {r["metadata"]["file"] for r in mmr}

        # MMR should produce >= as many distinct files as vanilla, often more
        assert len(mmr_files) >= len(vanilla_files)

    def test_mmr_lambda_one_is_pure_relevance(self, vs):
        for i in range(5):
            vs.add(f"d{i}", f"PostgreSQL replication topology variant {i}", {"file": "pg"})
        vs.add("other", "Completely unrelated text about cooking pasta", {"file": "other"})

        # λ=1.0 → top-K should be the topically-best matches
        results = vs.search("PostgreSQL replication", n_results=3, mmr=True, mmr_lambda=1.0)
        assert all("PostgreSQL" in r["text"] for r in results)

    def test_mmr_handles_small_corpus(self, vs):
        vs.add("doc1", "Single document in the corpus", {"type": "x"})
        results = vs.search("document", n_results=5, mmr=True)
        assert len(results) == 1


class TestHybridSearch:
    def test_hybrid_finds_proper_noun_via_bm25(self, vs):
        # Embedding alone struggles with rare proper nouns. BM25 catches them.
        vs.add("a", "Discussion about deployment infrastructure and CI pipelines",
               {"file": "a"})
        vs.add("b", "Notes on Megatune integration with the conversion model",
               {"file": "b"})
        vs.add("c", "Generic talk about machine learning model training",
               {"file": "c"})

        results = vs.search("Megatune", n_results=3, hybrid=True)
        ids = [r["id"] for r in results]
        # The document literally containing "Megatune" should rank first.
        assert ids[0] == "b"

    def test_hybrid_combines_dense_and_keyword(self, vs):
        # 'b' has the literal keyword; 'c' is semantically related; 'a' is unrelated.
        vs.add("a", "Cooking recipes for pasta dishes", {"file": "a"})
        vs.add("b", "PostgreSQL replication failover policy details", {"file": "b"})
        vs.add("c", "Database high-availability architecture overview", {"file": "c"})

        results = vs.search("PostgreSQL replication", n_results=3, hybrid=True)
        ids = [r["id"] for r in results]
        # Both b (keyword) and c (semantic) should rank above a (unrelated)
        assert "a" not in ids[:2] or len(ids) == 1

    def test_hybrid_with_mmr(self, vs):
        # 5 PG docs + 1 unrelated. Hybrid+MMR should still produce results.
        for i in range(5):
            vs.add(f"pg{i}", f"PostgreSQL sharding strategy variant {i}", {"file": "pg"})
        vs.add("k1", "Kafka topic compaction policies", {"file": "k"})

        results = vs.search("PostgreSQL", n_results=3, hybrid=True, mmr=True)
        assert len(results) == 3
        # Results should not include MMR-internal fields
        assert "embedding" not in results[0]
        assert "sim_to_query" not in results[0]

    def test_hybrid_handles_empty_corpus(self, vs):
        results = vs.search("anything", n_results=5, hybrid=True)
        assert results == []

    def test_invalidate_bm25_rebuilds(self, vs):
        vs.add("doc1", "Original content here", {"type": "x"})
        # First hybrid query builds the index
        results1 = vs.search("content", n_results=3, hybrid=True)
        assert len(results1) >= 1
        # Add a new doc and invalidate
        vs.add("doc2", "Megatune-specific content for the index", {"type": "x"})
        vs.invalidate_bm25()
        # New doc should now be findable via hybrid (BM25 picks up "Megatune")
        results2 = vs.search("Megatune", n_results=3, hybrid=True)
        ids = [r["id"] for r in results2]
        assert "doc2" in ids


class TestEmbeddingModelOverride:
    def test_default_returns_none(self, monkeypatch):
        from context_orchestrator.search import _build_embedding_function, EMBEDDING_MODEL_ENV
        monkeypatch.delenv(EMBEDDING_MODEL_ENV, raising=False)
        assert _build_embedding_function() is None

    def test_env_set_returns_callable_or_clear_error(self, monkeypatch):
        # If sentence-transformers IS installed (the embeddings extra), this
        # returns an EmbeddingFunction. If NOT installed, raises RuntimeError
        # with a clear message pointing the user at the extra.
        from context_orchestrator.search import _build_embedding_function, EMBEDDING_MODEL_ENV
        monkeypatch.setenv(EMBEDDING_MODEL_ENV, "all-MiniLM-L6-v2")
        try:
            ef = _build_embedding_function()
            assert ef is not None
            # Don't actually call it — would download the model. The signature is
            # callable(list[str]) -> list[list[float]] but we only need to verify
            # construction works.
        except RuntimeError as e:
            assert "embeddings" in str(e).lower()
