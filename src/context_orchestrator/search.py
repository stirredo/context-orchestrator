import logging
import os
from pathlib import Path
from typing import Optional

import chromadb

logger = logging.getLogger("context-orchestrator")

DEFAULT_CHROMA_PATH = Path.home() / ".context-orchestrator" / "chroma"
DEFAULT_CHROMA_HOST = "127.0.0.1"
DEFAULT_CHROMA_PORT = 8765


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
