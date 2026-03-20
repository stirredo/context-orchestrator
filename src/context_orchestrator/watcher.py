#!/usr/bin/env python3
"""Transcript watcher — scan a folder for new transcripts and auto-ingest them."""

import argparse
import time
import logging
import sys
from pathlib import Path

from context_orchestrator.db import Database
from context_orchestrator.search import VectorSearch
from context_orchestrator.ingest import ingest_folder

TRANSCRIPT_DIR = Path.home() / "transcripts"

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("transcript-watcher")


def run_once(transcript_dir: Path, db: Database, vs: VectorSearch) -> int:
    """Scan folder once and ingest new files. Returns count of ingested files."""
    results = ingest_folder(db, vs, transcript_dir)
    ingested = [r for r in results if r["status"] == "ingested"]

    for r in ingested:
        match_info = ""
        if r["match_method"] == "auto":
            match_info = f" (auto-matched, conf={r['confidence']})"
        elif r["match_method"] == "created":
            match_info = " (new task)"
        logger.info(f"Ingested: {r['file']} → task '{r['task']}'{match_info} ({r['chunks']} chunks)")

    return len(ingested)


def main():
    parser = argparse.ArgumentParser(
        description="Watch a folder for new transcripts and auto-ingest them"
    )
    parser.add_argument(
        "--dir",
        default=str(TRANSCRIPT_DIR),
        help=f"Directory to watch (default: {TRANSCRIPT_DIR})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between scans (default: 300 = 5 minutes)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (don't loop)",
    )
    args = parser.parse_args()

    transcript_dir = Path(args.dir)
    transcript_dir.mkdir(parents=True, exist_ok=True)

    db = Database()
    vs = VectorSearch()

    if args.once:
        count = run_once(transcript_dir, db, vs)
        if count:
            logger.info(f"Done. Ingested {count} new file(s).")
        else:
            logger.info("No new files to ingest.")
        return

    logger.info(f"Watching {transcript_dir} every {args.interval}s")
    while True:
        try:
            run_once(transcript_dir, db, vs)
        except Exception as e:
            logger.error(f"Error during scan: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
