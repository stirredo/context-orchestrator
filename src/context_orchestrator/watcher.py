"""Polls ~/transcripts/ for new or modified .md files and reindexes them.

Designed to pair with meeting-capture (https://github.com/stirredo/meeting-capture),
which writes transcripts continuously while a meeting runs. Files get appended to
in flight, so the watcher must reindex when mtime advances — not just on first sight.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .cli import index_transcript
from .search import VectorSearch

TRANSCRIPT_DIR = Path.home() / "transcripts"
STATE_FILE = Path.home() / ".context-orchestrator" / "watcher_state.json"
DEFAULT_INTERVAL = 5.0
SETTLE_SECONDS = 2.0

log = logging.getLogger("context-orchestrator.watcher")


def load_state(state_file: Path = STATE_FILE) -> dict[str, float]:
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, float], state_file: Path = STATE_FILE) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True))


def scan_once(
    vs: VectorSearch,
    watch_dir: Path,
    state: dict[str, float],
    settle_seconds: float = SETTLE_SECONDS,
) -> list[Path]:
    """Reindex any .md file in watch_dir whose mtime has advanced.

    Files modified within the last `settle_seconds` are skipped on this pass to
    avoid indexing mid-write. They'll be picked up on the next scan.
    """
    if not watch_dir.exists():
        return []
    now = time.time()
    indexed: list[Path] = []
    for f in sorted(watch_dir.glob("*.md")):
        try:
            mtime = f.stat().st_mtime
        except FileNotFoundError:
            continue
        key = str(f)
        if mtime <= state.get(key, 0.0):
            continue
        if (now - mtime) < settle_seconds:
            continue
        try:
            vs.collection.delete(where={"file_path": key})
        except Exception:
            log.exception("failed to clear old chunks for %s", key)
        try:
            index_transcript(vs, f)
        except Exception:
            log.exception("failed to index %s", key)
            continue
        state[key] = mtime
        indexed.append(f)
    return indexed


def watch_loop(
    watch_dir: Path = TRANSCRIPT_DIR,
    interval: float = DEFAULT_INTERVAL,
    state_file: Path = STATE_FILE,
) -> None:
    vs = VectorSearch()
    state = load_state(state_file)
    log.info("watching %s every %.1fs", watch_dir, interval)
    while True:
        try:
            indexed = scan_once(vs, watch_dir, state)
            if indexed:
                save_state(state, state_file)
                for p in indexed:
                    log.info("indexed %s", p.name)
        except Exception:
            log.exception("scan failed")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="transcript-watcher",
        description="Watch ~/transcripts/ and auto-index new or modified files.",
    )
    parser.add_argument("--dir", default=str(TRANSCRIPT_DIR), help="Directory to watch")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="Poll interval in seconds")
    parser.add_argument("--once", action="store_true", help="Scan once and exit (for cron)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    watch_dir = Path(args.dir).expanduser()
    if args.once:
        vs = VectorSearch()
        state = load_state()
        indexed = scan_once(vs, watch_dir, state)
        if indexed:
            save_state(state)
        log.info("indexed %d file(s)", len(indexed))
        return 0

    watch_loop(watch_dir, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
