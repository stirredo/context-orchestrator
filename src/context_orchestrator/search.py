import logging
import os
from pathlib import Path
from typing import Optional

import chromadb

logger = logging.getLogger("context-orchestrator")

DEFAULT_CHROMA_PATH = Path.home() / ".context-orchestrator" / "chroma"
DEFAULT_CHROMA_HOST = "127.0.0.1"
DEFAULT_CHROMA_PORT = 8765

# MMR re-rank knobs. Lambda 1.0 = pure relevance (no diversity); 0.0 = pure
# diversity (ignore relevance). 0.7 is empirically a good balance for
# transcript-heavy corpora — keeps top-3 strictly on-topic, then opens up.
DEFAULT_MMR_LAMBDA = 0.7
# How many candidates to fetch from Chroma before MMR re-ranking. 3-5x the
# requested n_results gives MMR room to spread.
MMR_CANDIDATE_MULTIPLIER = 3
MMR_CANDIDATE_MIN = 30


def _cosine(a, b) -> float:
    """Cosine similarity between two embedding vectors."""
    import numpy as np  # local import to keep search.py importable without numpy
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)


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
        target = str(self.chroma_path) if self.chroma_path else f"http://{self.host}:{self.port}"
        logger.info(f"Vector search initialized at {target} ({self.collection.count()} docs)")

    def _connect(self) -> None:
        if self.chroma_path is not None:
            self.client = chromadb.PersistentClient(path=str(self.chroma_path))
        else:
            self.client = chromadb.HttpClient(host=self.host, port=self.port)
        self.collection = self.client.get_or_create_collection(
            name="context",
            metadata={"hnsw:space": "cosine"},
        )

    def reload(self) -> None:
        """Reconnect to disk so writes from other processes (e.g. the watcher) become visible."""
        self._connect()

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
    ) -> list[dict]:
        """Semantic search across all indexed documents.

        Args:
            query: natural-language query string
            n_results: maximum hits to return
            where: optional ChromaDB metadata filter (e.g. time-window, file_path)
            mmr: when True, fetch ~3x candidates and re-rank with Maximal
                Marginal Relevance to spread results across distinct documents
                instead of returning many near-duplicates from one source.
                Defaults False so existing callers keep raw cosine ranking.
            mmr_lambda: MMR trade-off; 1.0 = pure relevance, 0.0 = pure
                diversity. Default 0.7 balances both for transcript corpora.
        """
        total = self.collection.count() or 1

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

    def count(self) -> int:
        return self.collection.count()
