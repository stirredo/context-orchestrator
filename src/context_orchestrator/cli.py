#!/usr/bin/env python3
"""save-transcript CLI — Save clipboard transcript and index for search."""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from context_orchestrator.chunking import chunk_transcript, is_hallucination
from context_orchestrator.corrections import load_corrections
from context_orchestrator.search import VectorSearch

TRANSCRIPT_DIR = Path.home() / "transcripts"


def get_clipboard() -> str:
    """Get text from macOS clipboard."""
    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return result.stdout


def index_transcript(vs: VectorSearch, file_path: Path) -> int:
    """Read a transcript, chunk it (timestamp-aware when possible), filter
    whisper hallucinations, and index the rest.

    Returns the number of chunks actually indexed (post-filter). Skipped
    chunks are silently dropped — they have no information value.

    For meeting-capture / Cluely-format transcripts (recognizable date in
    filename + `[HH:MM:SS]` body lines), each emitted chunk carries
    `start_ts_unix`, `start_ts_iso`, `meeting_id`, and `chunk_type="speech"`
    metadata, enabling time-window filtering at query time via Chroma's
    native `where` clauses.
    """
    content = file_path.read_text(encoding="utf-8")
    corrections = load_corrections()
    chunks_with_meta = chunk_transcript(content, file_path.name, corrections=corrections)
    file_str = str(file_path)

    total = len(chunks_with_meta)
    indexed = 0
    for i, (chunk, meta) in enumerate(chunks_with_meta):
        is_junk, _ = is_hallucination(chunk)
        if is_junk:
            continue
        doc_id = f"transcript:{file_path.name}:{i}"
        full_meta = {
            "type": "transcript",
            "file_path": file_str,
            "filename": file_path.name,
            "chunk_index": i,
            "total_chunks": total,
            **meta,  # chunk_type, meeting_id, start_ts_*, n_blocks (when applicable)
        }
        vs.add(doc_id, chunk, full_meta)
        indexed += 1

    return indexed


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
