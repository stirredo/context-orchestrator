import logging
import os
import re
from pathlib import Path
from typing import Optional

import chromadb

logger = logging.getLogger("context-orchestrator")

DEFAULT_CHROMA_PATH = Path.home() / ".context-orchestrator" / "chroma"
DEFAULT_CHROMA_HOST = "127.0.0.1"
DEFAULT_CHROMA_PORT = 8765

# Optional embedding-model override. When unset, Chroma's default
# all-MiniLM-L6-v2 is used (384d, 256-token cap, no extra deps).
#
# To upgrade to a longer-context / higher-quality model:
#   pip install -e '.[embeddings]'
#   export CO_EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5
#
# The new model produces vectors with different dimensionality, so
# existing collections must be wiped before the first run.
EMBEDDING_MODEL_ENV = "CO_EMBEDDING_MODEL"

# MMR re-rank knobs. Lambda 1.0 = pure relevance (no diversity); 0.0 = pure
# diversity (ignore relevance). 0.7 is empirically a good balance for
# transcript-heavy corpora — keeps top-3 strictly on-topic, then opens up.
DEFAULT_MMR_LAMBDA = 0.7
# How many candidates to fetch from Chroma before MMR re-ranking. 3-5x the
# requested n_results gives MMR room to spread.
MMR_CANDIDATE_MULTIPLIER = 3
MMR_CANDIDATE_MIN = 30

# RRF (reciprocal rank fusion) combines dense and BM25 rankings.
# Per the canonical RRF paper, k=60 is a robust default.
DEFAULT_RRF_K = 60
# How many candidates each retriever should pull before fusion.
HYBRID_FETCH_PER_RETRIEVER = 50


def _cosine(a, b) -> float:
    """Cosine similarity between two embedding vectors."""
    import numpy as np  # local import to keep search.py importable without numpy
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)


def _build_embedding_function():
    """Construct a Chroma EmbeddingFunction for the user-configured model.

    Returns None when no override is set (Chroma falls back to its built-in
    default, all-MiniLM-L6-v2). Returns a SentenceTransformerEmbeddingFunction
    otherwise. Raises a clear error if the user requested a custom model but
    the optional `[embeddings]` extra isn't installed.
    """
    model_name = os.environ.get(EMBEDDING_MODEL_ENV)
    if not model_name:
        return None
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError as e:
        raise RuntimeError(
            f"{EMBEDDING_MODEL_ENV}={model_name} requires the 'embeddings' "
            "extra. Install with: pip install -e '.[embeddings]'"
        ) from e
    # trust_remote_code is required for nomic-embed and similar custom-arch
    # models. It's safe here because the model name is user-controlled and
    # they explicitly opted in via env var.
    return SentenceTransformerEmbeddingFunction(
        model_name=model_name,
        trust_remote_code=True,
    )


def _bm25_tokenize(text: str) -> list[str]:
    """Lowercased word tokens. Good enough for technical proper-noun queries
    where exact matches dominate."""
    return re.findall(r"\w+", text.lower())


def _rrf_fuse(rankings: list[list[str]], k: int = DEFAULT_RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion. Each input is an ordered list of doc ids.
    Output is sorted by sum(1 / (k + rank)) descending."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


def _mmr_select(candidates: list[dict], k: int, lam: float) -> list[dict]:
    """Maximal Marginal Relevance re-ranking.

    Each candidate is a dict with at least 'sim_to_query' (float in [0,1])
    and 'embedding' (list[float]). Returns up to k candidates ordered by
    `lam * sim_to_query - (1 - lam) * max(sim_to_already_selected)`.
    """
    selected: list[int] = []
    remaining = list(range(len(candidates)))
    while remaining and len(selected) < k:
        best_i, best_score = remaining[0], -float("inf")
        for ri in remaining:
            relev = candidates[ri]["sim_to_query"]
            penalty = 0.0
            if selected:
                penalty = max(
                    _cosine(candidates[ri]["embedding"], candidates[s]["embedding"])
                    for s in selected
                )
            score = lam * relev - (1 - lam) * penalty
            if score > best_score:
                best_score, best_i = score, ri
        selected.append(best_i)
        remaining.remove(best_i)
    return [candidates[i] for i in selected]


class VectorSearch:
    """Connects to Chroma. Two modes:

    - HTTP (default): connects to a `chroma run` daemon. Multiple processes can
      share one index without SQLite-lock contention. Override host/port via the
      `CO_CHROMA_HOST` / `CO_CHROMA_PORT` env vars.
    - Local persistent (tests, migration): pass `chroma_path` to use a
      `PersistentClient` against an on-disk directory. Same on-disk format as
      the daemon, so data is portable between modes.
    """

    def __init__(
        self,
        chroma_path: Optional[Path] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ):
        self.chroma_path = chroma_path
        if chroma_path is not None:
            self.host = None
            self.port = None
            chroma_path.mkdir(parents=True, exist_ok=True)
        else:
            self.host = host or os.environ.get("CO_CHROMA_HOST", DEFAULT_CHROMA_HOST)
            self.port = int(port if port is not None else os.environ.get("CO_CHROMA_PORT", DEFAULT_CHROMA_PORT))
        self._connect()
        # Lazy BM25 index — built on first hybrid search, refreshed when
        # the caller invalidates explicitly.
        self._bm25 = None
        self._bm25_ids: list[str] = []
        target = str(self.chroma_path) if self.chroma_path else f"http://{self.host}:{self.port}"
        logger.info(f"Vector search initialized at {target} ({self.collection.count()} docs)")

    def _connect(self) -> None:
        if self.chroma_path is not None:
            self.client = chromadb.PersistentClient(path=str(self.chroma_path))
        else:
            self.client = chromadb.HttpClient(host=self.host, port=self.port)
        kwargs: dict = {
            "name": "context",
            "metadata": {"hnsw:space": "cosine"},
        }
        ef = _build_embedding_function()
        if ef is not None:
            kwargs["embedding_function"] = ef
        self.collection = self.client.get_or_create_collection(**kwargs)

    def reload(self) -> None:
        """Reconnect to disk so writes from other processes (e.g. the watcher) become visible."""
        self._connect()
        # Underlying corpus may have changed — drop cached BM25 index.
        self._bm25 = None
        self._bm25_ids = []

    def invalidate_bm25(self) -> None:
        """Force the next hybrid search to rebuild the BM25 index. Call this
        after batch writes if you can't wait for the natural reload."""
        self._bm25 = None
        self._bm25_ids = []

    def _ensure_bm25(self) -> None:
        """Build (or reuse) an in-memory BM25 index over every document in
        the collection. Lazy: only constructed on first hybrid search.
        """
        if self._bm25 is not None:
            return
        try:
            from rank_bm25 import BM25Okapi
        except ImportError as e:
            raise RuntimeError(
                "hybrid search requires rank-bm25 — pip install rank-bm25"
            ) from e
        data = self.collection.get(include=["documents"], limit=100000)
        ids = data["ids"]
        docs = data["documents"]
        if not ids:
            # Empty collection — leave _bm25 as None; hybrid degrades to dense
            return
        tokenized = [_bm25_tokenize(d) for d in docs]
        self._bm25 = BM25Okapi(tokenized)
        self._bm25_ids = ids

    def add(self, doc_id: str, text: str, metadata: dict) -> None:
        """Add or update a document in the vector index."""
        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )

    def remove(self, doc_id: str) -> None:
        """Remove a document from the vector index."""
        try:
            self.collection.delete(ids=[doc_id])
        except Exception:
            pass  # Ignore if not found

    def search(
        self,
        query: str,
        n_results: int = 10,
        where: Optional[dict] = None,
        mmr: bool = False,
        mmr_lambda: float = DEFAULT_MMR_LAMBDA,
        hybrid: bool = False,
    ) -> list[dict]:
        """Semantic search across all indexed documents.

        Args:
            query: natural-language query string
            n_results: maximum hits to return
            where: optional ChromaDB metadata filter (e.g. time-window, file_path)
            mmr: when True, re-rank with Maximal Marginal Relevance to spread
                results across distinct documents instead of returning many
                near-duplicates from one source. Default False.
            mmr_lambda: MMR trade-off; 1.0 = pure relevance, 0.0 = pure
                diversity. Default 0.7.
            hybrid: when True, run BM25 keyword search alongside dense vector
                search and fuse via Reciprocal Rank Fusion. Helps with
                proper-noun and exact-term queries that pure embeddings miss.
                Default False. Compatible with `mmr` (MMR is applied AFTER
                fusion). Note: incompatible with `where` filtering — hybrid
                runs across the full corpus.
        """
        total = self.collection.count() or 1

        if hybrid:
            return self._hybrid_search(query, n_results, mmr, mmr_lambda)

        if mmr:
            fetch_n = min(total, max(MMR_CANDIDATE_MIN, n_results * MMR_CANDIDATE_MULTIPLIER))
            kwargs = {
                "query_texts": [query],
                "n_results": fetch_n,
                "include": ["documents", "metadatas", "embeddings", "distances"],
            }
            if where:
                kwargs["where"] = where
            results = self.collection.query(**kwargs)
            if not results or not results["ids"] or not results["ids"][0]:
                return []
            candidates = []
            for i, doc_id in enumerate(results["ids"][0]):
                dist = results["distances"][0][i]
                # cosine distance is in [0, 2]; convert to similarity in [0, 1]
                sim = max(0.0, 1.0 - dist / 2.0)
                candidates.append({
                    "id": doc_id,
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": dist,
                    "sim_to_query": sim,
                    "embedding": results["embeddings"][0][i],
                })
            reranked = _mmr_select(candidates, n_results, lam=mmr_lambda)
            # Strip the embedding before returning — callers don't need it.
            return [
                {k: v for k, v in c.items() if k not in ("embedding", "sim_to_query")}
                for c in reranked
            ]

        # Standard path: raw cosine top-K
        kwargs = {
            "query_texts": [query],
            "n_results": min(n_results, total),
        }
        if where:
            kwargs["where"] = where

        results = self.collection.query(**kwargs)

        hits = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                hits.append({
                    "id": doc_id,
                    "text": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else None,
                })
        return hits

    def _hybrid_search(
        self,
        query: str,
        n_results: int,
        mmr: bool,
        mmr_lambda: float,
    ) -> list[dict]:
        """Dense + BM25 → RRF fuse → optional MMR → top-K."""
        total = self.collection.count() or 1
        fetch = min(total, HYBRID_FETCH_PER_RETRIEVER)

        # Dense retrieval — also gives us embeddings for an optional MMR step
        include = ["documents", "metadatas", "distances"]
        if mmr:
            include.append("embeddings")
        dense = self.collection.query(query_texts=[query], n_results=fetch, include=include)
        if not dense or not dense["ids"] or not dense["ids"][0]:
            return []
        dense_ids = dense["ids"][0]
        doc_by_id = {id_: dense["documents"][0][i] for i, id_ in enumerate(dense_ids)}
        meta_by_id = {id_: dense["metadatas"][0][i] for i, id_ in enumerate(dense_ids)}
        dist_by_id = {id_: dense["distances"][0][i] for i, id_ in enumerate(dense_ids)}
        embed_by_id = (
            {id_: dense["embeddings"][0][i] for i, id_ in enumerate(dense_ids)} if mmr else {}
        )

        # BM25 retrieval over the cached corpus
        self._ensure_bm25()
        if self._bm25 is None:
            # Empty collection — degrade to dense only
            return [
                {"id": id_, "text": doc_by_id[id_], "metadata": meta_by_id[id_],
                 "distance": dist_by_id[id_]}
                for id_ in dense_ids[:n_results]
            ]
        bm25_scores = self._bm25.get_scores(_bm25_tokenize(query))
        bm25_top_idx = sorted(range(len(bm25_scores)), key=lambda i: -bm25_scores[i])[:fetch]
        bm25_ids = [self._bm25_ids[i] for i in bm25_top_idx]

        # Fuse rankings
        fused = _rrf_fuse([dense_ids, bm25_ids])
        if not fused:
            return []

        # Hydrate any BM25-only ids that aren't in dense's payload
        missing = [id_ for id_, _ in fused if id_ not in doc_by_id]
        if missing:
            extra_include = ["documents", "metadatas"] + (["embeddings"] if mmr else [])
            extra = self.collection.get(ids=missing, include=extra_include)
            for i, id_ in enumerate(extra["ids"]):
                doc_by_id[id_] = extra["documents"][i]
                meta_by_id[id_] = extra["metadatas"][i]
                if mmr and extra.get("embeddings") is not None:
                    embed_by_id[id_] = extra["embeddings"][i]
                # No distance for BM25-only docs — use the RRF score as a
                # rough sim signal for downstream MMR
                dist_by_id.setdefault(id_, None)

        if mmr:
            # Re-rank top-30 of fused candidates with MMR
            top_for_mmr = fused[: max(MMR_CANDIDATE_MIN, n_results * MMR_CANDIDATE_MULTIPLIER)]
            candidates = []
            for id_, rrf_score in top_for_mmr:
                if id_ not in embed_by_id:
                    continue
                d = dist_by_id.get(id_)
                sim = max(0.0, 1.0 - d / 2.0) if d is not None else rrf_score
                candidates.append({
                    "id": id_,
                    "text": doc_by_id[id_],
                    "metadata": meta_by_id[id_],
                    "distance": d,
                    "sim_to_query": sim,
                    "embedding": embed_by_id[id_],
                })
            reranked = _mmr_select(candidates, n_results, lam=mmr_lambda)
            return [
                {k: v for k, v in c.items() if k not in ("embedding", "sim_to_query")}
                for c in reranked
            ]

        # No MMR — just take the top-N fused
        out = []
        for id_, _rrf_score in fused[:n_results]:
            out.append({
                "id": id_,
                "text": doc_by_id.get(id_, ""),
                "metadata": meta_by_id.get(id_, {}),
                "distance": dist_by_id.get(id_),
            })
        return out

    def count(self) -> int:
        return self.collection.count()
