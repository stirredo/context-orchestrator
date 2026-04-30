"""Split text into chunks for vector indexing.

Three layers:
  - `chunk_text`: pure word-count chunker with overlap. Used for plain
    documents (file_chunk sources, etc.) and as a fallback.
  - `chunk_transcript`: transcript-aware chunker. Detects `[HH:MM:SS]`
    timestamp blocks in the body, groups adjacent blocks up to a target
    word budget, and emits per-chunk metadata (`start_ts_unix`,
    `start_ts_iso`, `meeting_id`, `chunk_type`). Falls back to
    `chunk_text` for files without timestamps.
  - `is_hallucination`: helper used by the indexer to skip whisper-style
    junk chunks (silence-period repeats, low-entropy noise) before embed.

The transcript-aware chunker is the basis for time-window queries against
Chroma — once `start_ts_unix` is in metadata, callers can filter via
`where: {"start_ts_unix": {"$gte": X, "$lte": Y}}` for free.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Optional  # noqa: F401  (used in chunk_transcript signature)

# Hallucination filter thresholds. Tuned against ~/transcripts/ corpus where
# whisper-large-v3-turbo produces "Thank you. Thank you. Thank you..." runs
# during silent stretches and "Alright Alright Alright..." cascades on
# noisy-but-quiet audio. These chunks have no information value and pollute
# vector search top-K results.
HALLUCINATION_MIN_CHARS = 30
HALLUCINATION_MIN_TOKENS = 5
HALLUCINATION_MIN_UNIQUE_RATIO = 0.20  # unique tokens / total tokens
HALLUCINATION_MAX_REPEAT_RUN = 5  # max consecutive identical tokens

# Transcript chunking
TRANSCRIPT_CHUNK_WORDS = 500  # target words per merged chunk
TRANSCRIPT_MIN_CHUNK_CHARS = 20  # drop chunks shorter than this even before hallucination filter

# Filename patterns we know how to date-parse for transcript timestamping.
# meeting-capture format: meeting-YYYY-MM-DDTHH-MM-SS.md
_MEETING_FILENAME_RE = re.compile(r"^meeting-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})\.md$")
# Cluely / save-transcript format: YYYY-MM-DD-HHMM-<slug>.md
_CLUELY_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})-")
# Body line format: "[HH:MM:SS] body text..."
_TS_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s*(.*)$", re.MULTILINE)


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into word-based chunks with overlap.

    Args:
        text: The full text to chunk
        chunk_size: Target number of words per chunk
        overlap: Number of words to overlap between chunks
    """
    words = text.split()
    if len(words) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start += chunk_size - overlap

    return chunks


def parse_meeting_date(filename: str) -> Optional[datetime]:
    """Best-effort date parsing from common transcript filename formats.

    Returns None if the filename doesn't look like a transcript we can
    date — caller should fall back to word-count chunking with no
    timestamp metadata.
    """
    m = _MEETING_FILENAME_RE.match(filename)
    if m:
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    m = _CLUELY_FILENAME_RE.match(filename)
    if m:
        y, mo, d, h, mi = (int(x) for x in m.groups())
        return datetime(y, mo, d, h, mi, 0, tzinfo=timezone.utc)
    return None


def chunk_transcript(
    text: str,
    filename: str,
    corrections: Optional[dict] = None,
) -> list[tuple[str, dict]]:
    """Chunk a transcript into (text, metadata) pairs.

    For files with a recognizable date in the filename AND `[HH:MM:SS]`
    body lines: group consecutive timestamp blocks until ~500 words per
    chunk, emitting `start_ts_unix`, `start_ts_iso`, `meeting_id`, and
    `chunk_type="speech"` metadata.

    For other files (no parseable date or no timestamps): fall back to
    `chunk_text` with no extra metadata. Metadata still includes
    `chunk_type` set to `"transcript_wordcount"` so callers can
    distinguish.

    If `corrections` is provided (a dict from lower-cased misspelled
    token to canonical replacement), apply them to the text before
    chunking. The on-disk source file is never modified — corrections
    are an embedding-layer concern only.

    Caller is expected to add `file_path`, `filename`, `chunk_index`,
    `total_chunks` to each chunk's metadata before indexing.
    """
    if corrections:
        # Imported here to avoid a hard dependency cycle: chunking is core,
        # corrections is an optional layer on top.
        from context_orchestrator.corrections import apply as _apply_corrections
        text = _apply_corrections(text, corrections)

    meeting_id = filename.rsplit(".md", 1)[0] if filename.endswith(".md") else filename
    meeting_dt = parse_meeting_date(filename)
    ts_matches = list(_TS_LINE_RE.finditer(text)) if meeting_dt else []

    if not (meeting_dt and ts_matches):
        # Fallback path — treat as a plain document
        chunks = chunk_text(text)
        return [
            (c, {"chunk_type": "transcript_wordcount", "meeting_id": meeting_id})
            for c in chunks
        ]

    # Timestamp-aware path — group adjacent blocks
    out: list[tuple[str, dict]] = []
    cur_buf: list[tuple[datetime, str]] = []
    cur_words = 0
    prev_ts: Optional[datetime] = None

    def flush() -> None:
        if not cur_buf:
            return
        start_dt = cur_buf[0][0]
        body = " ".join(b[1] for b in cur_buf if b[1].strip())
        if not body or len(body) < TRANSCRIPT_MIN_CHUNK_CHARS:
            return
        out.append((body, {
            "chunk_type": "speech",
            "meeting_id": meeting_id,
            "start_ts_unix": start_dt.timestamp(),
            "start_ts_iso": start_dt.isoformat(),
            "n_blocks": len(cur_buf),
        }))

    for m in ts_matches:
        h, mi, s, body = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        body = body.strip()
        if not body:
            continue
        chunk_dt = meeting_dt.replace(hour=h, minute=mi, second=s)
        # Handle midnight rollover within a single meeting
        if prev_ts and chunk_dt < prev_ts:
            chunk_dt = chunk_dt + timedelta(days=1)
        prev_ts = chunk_dt
        wc = len(body.split())
        if cur_words + wc > TRANSCRIPT_CHUNK_WORDS and cur_buf:
            flush()
            cur_buf = []
            cur_words = 0
        cur_buf.append((chunk_dt, body))
        cur_words += wc
    flush()

    return out


def is_hallucination(text: str) -> tuple[bool, str]:
    """Decide whether a chunk is whisper hallucination noise that should not
    be indexed. Returns (is_junk, reason).

    Catches three patterns observed in real meeting-capture transcripts:
      - "Thank you. Thank you. Thank you..." (silence-period repeats)
      - "Alright Alright Alright..." (200+ consecutive same-token cascade)
      - Low unique-token ratio (chunk dominated by repeated phrases)

    Returns (False, "") for any chunk with real content. Conservative: only
    flags clearly redundant text. Tunable thresholds at module top.
    """
    text = text.strip()
    if len(text) < HALLUCINATION_MIN_CHARS:
        return True, "too_short"

    tokens = text.split()
    if len(tokens) < HALLUCINATION_MIN_TOKENS:
        return True, "too_few_tokens"

    # Unique-token ratio (catches "Thank you. Thank you. ...")
    unique = set(t.lower().rstrip(",.!?;:") for t in tokens)
    if len(unique) / len(tokens) < HALLUCINATION_MIN_UNIQUE_RATIO:
        return True, f"low_uniqueness_{len(unique)}/{len(tokens)}"

    # Consecutive-repeat run (catches "Alright Alright Alright...")
    cur_run, max_run, prev = 1, 1, None
    for t in tokens:
        tl = t.lower().rstrip(",.!?;:")
        if tl == prev:
            cur_run += 1
            if cur_run > max_run:
                max_run = cur_run
        else:
            cur_run = 1
            prev = tl
    if max_run >= HALLUCINATION_MAX_REPEAT_RUN:
        return True, f"repeat_run_{max_run}"

    return False, ""
