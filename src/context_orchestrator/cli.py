#!/usr/bin/env python3
"""save-transcript CLI — Save clipboard transcript and auto-ingest into context orchestrator."""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from context_orchestrator.db import Database
from context_orchestrator.search import VectorSearch
from context_orchestrator.ingest import ingest_file

TRANSCRIPT_DIR = Path.home() / "transcripts"


def get_clipboard() -> str:
    """Get text from macOS clipboard."""
    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(
        description="Save clipboard transcript and auto-ingest into context orchestrator"
    )
    parser.add_argument(
        "name",
        nargs="?",
        default="meeting",
        help="Short name for the transcript (e.g., 'sprint-planning', 'auth-discussion')",
    )
    parser.add_argument(
        "--task",
        help="Explicitly assign to this task instead of auto-matching",
    )
    parser.add_argument(
        "--dir",
        default=str(TRANSCRIPT_DIR),
        help=f"Directory to save transcripts (default: {TRANSCRIPT_DIR})",
    )
    parser.add_argument(
        "--file",
        help="Ingest an existing file instead of clipboard",
    )
    parser.add_argument(
        "--no-match",
        action="store_true",
        help="Skip auto-matching, create a new task from the name",
    )
    args = parser.parse_args()

    transcript_dir = Path(args.dir)
    transcript_dir.mkdir(parents=True, exist_ok=True)

    if args.file:
        # Ingest an existing file
        file_path = Path(args.file).expanduser().resolve()
    else:
        # Grab clipboard
        text = get_clipboard()
        lines = text.strip().split("\n")

        if len(lines) < 2:
            print("Clipboard looks empty or too short. Copy the transcript first.", file=sys.stderr)
            sys.exit(1)

        # Save to file
        date_str = datetime.now().strftime("%Y-%m-%d-%H%M")
        filename = f"{date_str}-{args.name}.md"
        file_path = transcript_dir / filename

        file_path.write_text(text, encoding="utf-8")
        word_count = len(text.split())
        print(f"Saved: {file_path} ({word_count} words)")

    # Ingest
    db = Database()
    vs = VectorSearch()

    result = ingest_file(
        db, vs, file_path,
        task_name=args.task if args.task else (args.name if args.no_match else None),
        auto_match=not args.no_match and not args.task,
    )

    status = result["status"]
    if status == "ingested":
        match_info = ""
        if result["match_method"] == "auto":
            match_info = f" (confidence: {result['confidence']})"
        elif result["match_method"] == "created":
            match_info = " (new task created)"

        print(f"Ingested: {result['file']}")
        print(f"  Task: {result['task']}{match_info}")
        print(f"  Chunks: {result['chunks']} | Words: {result['words']}")
        print(f"\n  Wrong task? Run: save-transcript --task CORRECT_TASK --file {result['path']}")
    elif status == "skipped":
        print(f"Skipped: {result['message']}")
    else:
        print(f"Error: {result['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
