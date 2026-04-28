"""Pytest config — point Chroma at a per-session temp dir so importing the
server module doesn't try to dial the production HTTP daemon."""
import os
import tempfile
from pathlib import Path

# Set before any context_orchestrator import. Each test that needs isolation
# overrides server.vs / server.db with its own tmp_path-scoped instance, so
# this only matters for module-level instantiation in server.py.
os.environ.setdefault(
    "CO_CHROMA_PATH",
    str(Path(tempfile.mkdtemp(prefix="co-tests-chroma-")))
)
