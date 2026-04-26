import logging
from pathlib import Path
from typing import Optional

import chromadb

logger = logging.getLogger("context-orchestrator")

DEFAULT_CHROMA_PATH = Path.home() / ".context-orchestrator" / "chroma"


class VectorSearch:
    def __init__(self, chroma_path: Optional[Path] = None):
        self.chroma_path = chroma_path or DEFAULT_CHROMA_PATH
        self.chroma_path.mkdir(parents=True, exist_ok=True)
        self._connect()
        logger.info(f"Vector search initialized at {self.chroma_path} ({self.collection.count()} docs)")

    def _connect(self) -> None:
        self.client = chromadb.PersistentClient(path=str(self.chroma_path))
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

    def search(self, query: str, n_results: int = 10, where: Optional[dict] = None) -> list[dict]:
        """Semantic search across all indexed documents."""
        kwargs = {
            "query_texts": [query],
            "n_results": min(n_results, self.collection.count() or 1),
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
