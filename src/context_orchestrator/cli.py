#!/usr/bin/env python3
"""save-transcript CLI — Save clipboard transcript and index for search."""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from context_orchestrator.chunking import chunk_text
from context_orchestrator.search import VectorSearch

TRANSCRIPT_DIR = Path.home() / "transcripts"


def get_clipboard() -> str:
    """Get text from macOS clipboard."""
    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return result.stdout


def index_transcript(vs: VectorSearch, file_path: Path) -> int:
    """Read a file, chunk it, and index in ChromaDB. Returns chunk count."""
    content = file_path.read_text(encoding="utf-8")
    chunks = chunk_text(content)
    file_str = str(file_path)

    for i, chunk in enumerate(chunks):
        doc_id = f"transcript:{file_path.name}:{i}"
        vs.add(doc_id, chunk, {
            "type": "transcript",
            "file_path": file_str,
            "filename": file_path.name,
            "chunk_index": i,
            "total_chunks": len(chunks),
        })

    return len(chunks)


def main():
    parser = argparse.ArgumentParser(
        description="Save clipboard transcript and index for search"
    )
    parser.add_argument(
        "name",
        nargs="?",
        default="meeting",
        help="Short name for the transcript (e.g., 'sprint-planning', 'auth-discussion')",
    )
    parser.add_argument(
        "--dir",
        default=str(TRANSCRIPT_DIR),
        help=f"Directory to save transcripts (default: {TRANSCRIPT_DIR})",
    )
    parser.add_argument(
        "--file",
        help="Index an existing file instead of clipboard",
    )
    args = parser.parse_args()

    transcript_dir = Path(args.dir)
    transcript_dir.mkdir(parents=True, exist_ok=True)

    if args.file:
        file_path = Path(args.file).expanduser().resolve()
        if not file_path.exists():
            print(f"File not found: {file_path}", file=sys.stderr)
            sys.exit(1)
    else:
        text = get_clipboard()
        if len(text.strip().split("\n")) < 2:
            print("Clipboard looks empty or too short. Copy the transcript first.", file=sys.stderr)
            sys.exit(1)

        date_str = datetime.now().strftime("%Y-%m-%d-%H%M")
        filename = f"{date_str}-{args.name}.md"
        file_path = transcript_dir / filename
        file_path.write_text(text, encoding="utf-8")

        word_count = len(text.split())
        print(f"Saved: {file_path} ({word_count} words)")

    # Index in ChromaDB — no task assignment
    vs = VectorSearch()
    num_chunks = index_transcript(vs, file_path)

    print(f"Indexed: {file_path.name} ({num_chunks} chunks)")
    print(f"Searchable now. Claude will find it via search() and link to a task when you're ready.")


if __name__ == "__main__":
    main()
