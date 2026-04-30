"""Split text into overlapping chunks for vector indexing.

`chunk_text` is the core chunker — pure, deterministic, no filtering.
`is_hallucination` is a side helper used by the indexer to skip whisper-style
junk chunks (silence-period repeats, low-entropy noise) before embedding.
"""

# Hallucination filter thresholds. Tuned against ~/transcripts/ corpus where
# whisper-large-v3-turbo produces "Thank you. Thank you. Thank you..." runs
# during silent stretches and "Alright Alright Alright..." cascades on
# noisy-but-quiet audio. These chunks have no information value and pollute
# vector search top-K results.
HALLUCINATION_MIN_CHARS = 30
HALLUCINATION_MIN_TOKENS = 5
HALLUCINATION_MIN_UNIQUE_RATIO = 0.20  # unique tokens / total tokens
HALLUCINATION_MAX_REPEAT_RUN = 5  # max consecutive identical tokens


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
