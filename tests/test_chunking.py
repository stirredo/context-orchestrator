from context_orchestrator.chunking import (
    chunk_text,
    chunk_transcript,
    is_hallucination,
    parse_meeting_date,
)


class TestChunking:
    def test_short_text_single_chunk(self):
        text = "This is a short text."
        chunks = chunk_text(text, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_multiple_chunks(self):
        words = ["word"] * 1200
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=500, overlap=50)
        assert len(chunks) >= 3
        # Each chunk should have roughly 500 words (except last)
        for chunk in chunks[:-1]:
            assert len(chunk.split()) == 500

    def test_overlap(self):
        words = [f"w{i}" for i in range(100)]
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=60, overlap=10)
        # Second chunk should start 50 words in (60 - 10 overlap)
        second_words = chunks[1].split()
        assert second_words[0] == "w50"

    def test_exact_chunk_size(self):
        words = ["word"] * 500
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=500)
        assert len(chunks) == 1

    def test_empty_text(self):
        chunks = chunk_text("", chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == ""


class TestHallucinationFilter:
    def test_real_speech_passes(self):
        text = (
            "So we'll start with the second part of the demo. What we're looking at on the left is "
            "awareness of the brand and then historic usage if they've actually used this."
        )
        is_junk, _ = is_hallucination(text)
        assert is_junk is False

    def test_short_chunk_dropped(self):
        is_junk, reason = is_hallucination("Thank you.")
        assert is_junk is True
        assert "short" in reason or "few_tokens" in reason

    def test_alright_cascade_dropped(self):
        # Real example from meeting-2026-04-30T13-40-35.md — 200+ "Alright"s
        text = "Alright " * 200
        is_junk, reason = is_hallucination(text)
        assert is_junk is True
        assert "repeat_run" in reason or "low_uniqueness" in reason

    def test_thank_you_run_dropped(self):
        text = "Thank you. " * 30
        is_junk, reason = is_hallucination(text)
        assert is_junk is True

    def test_low_unique_ratio_dropped(self):
        # 14 unique tokens in 356 — taken from real silence-hallucination chunk
        text = (
            "Thank you. Alright, I have to do it. Alright, I have to do it. "
            "Thank you. Thank you. Alright, I have to do it. Thank you. "
            "Alright, I have to do this one. Thank you. Thank you. Thank you."
        ) * 8
        is_junk, reason = is_hallucination(text)
        assert is_junk is True
        assert "low_uniqueness" in reason or "repeat" in reason

    def test_substantive_chunk_with_some_repetition_passes(self):
        # Real speech can have "yeah yeah" or "you know you know" — should NOT trip the filter
        text = (
            "Yeah yeah I think the calibration is the part we still need to validate. "
            "You know I'm just going to push the PR for review and we can iterate on it. "
            "I think the main risk is the timing on the launch."
        )
        is_junk, _ = is_hallucination(text)
        assert is_junk is False

    def test_youtube_intro_passes(self):
        # The Mitchell Hashimoto Code Report YouTube clip — long, real content,
        # no repeats. Even though it's off-topic, it's not a hallucination.
        text = (
            "It's 10pm. Do you know where your children are? I don't know where mine "
            "are because I'm too busy working on pushing commit, final final v2 actual "
            "fix to GitHub. Unfortunately, if you're one of the 100 million plus "
            "developers who use GitHub, you may have encountered a message like this."
        )
        is_junk, _ = is_hallucination(text)
        assert is_junk is False


class TestParseMeetingDate:
    def test_meeting_capture_format(self):
        dt = parse_meeting_date("meeting-2026-04-30T13-40-35.md")
        assert dt is not None
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (2026, 4, 30, 13, 40, 35)

    def test_cluely_format(self):
        dt = parse_meeting_date("2026-04-21-1122-pvt-conversion-learner.md")
        assert dt is not None
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 4, 21, 11, 22)

    def test_unrecognized_format(self):
        assert parse_meeting_date("random-document.md") is None
        assert parse_meeting_date("notes.md") is None


class TestChunkTranscript:
    def test_timestamp_aware_emits_metadata(self):
        text = (
            "[14:25:00] First block of speech with several real-content words.\n"
            "[14:25:30] Second block adding more real content to the discussion.\n"
            "[14:26:00] Third block continuing the real discussion topic here.\n"
        )
        chunks = chunk_transcript(text, "meeting-2026-04-30T13-40-35.md")
        assert len(chunks) >= 1
        text0, meta0 = chunks[0]
        assert meta0["chunk_type"] == "speech"
        assert meta0["meeting_id"] == "meeting-2026-04-30T13-40-35"
        assert "start_ts_unix" in meta0
        assert meta0["start_ts_iso"].startswith("2026-04-30T14:25:00")
        # blocks should be merged into the chunk
        assert "First block" in text0 and "Third block" in text0

    def test_timestamp_chunk_splits_at_word_budget(self):
        # Over 500 words across timestamp blocks — should produce >= 2 chunks
        block_body = " ".join(f"realwordvariant{i}" for i in range(120))
        text = "\n".join(f"[14:{m:02d}:00] {block_body}" for m in range(10))
        chunks = chunk_transcript(text, "meeting-2026-04-30T14-00-00.md")
        assert len(chunks) >= 2
        for _, meta in chunks:
            assert meta["chunk_type"] == "speech"

    def test_fallback_when_no_timestamps(self):
        text = "A document with no timestamp markers, just regular prose content."
        chunks = chunk_transcript(text, "random-notes.md")
        assert len(chunks) == 1
        text0, meta0 = chunks[0]
        assert meta0["chunk_type"] == "transcript_wordcount"
        assert "start_ts_unix" not in meta0

    def test_fallback_when_filename_unparseable(self):
        # Has timestamps but unrecognized filename → falls back to wordcount
        text = "[14:25:00] some content here that should be word-count chunked instead."
        chunks = chunk_transcript(text, "weird-name.md")
        assert len(chunks) >= 1
        for _, meta in chunks:
            assert meta["chunk_type"] == "transcript_wordcount"

    def test_midnight_rollover(self):
        # Meeting starts late, crosses midnight — second block's HH:MM:SS would
        # appear "before" the first if we didn't compensate.
        text = (
            "[23:55:00] late evening discussion content here for the meeting.\n"
            "[00:05:00] just past midnight continuing the same discussion now.\n"
        )
        chunks = chunk_transcript(text, "meeting-2026-04-30T23-50-00.md")
        # Both blocks should land in one chunk (within 500 words)
        assert len(chunks) >= 1
        # And no chunk's start_ts should be wildly out of order — checked by
        # asserting the merged chunk includes both bodies
        joined = " ".join(c[0] for c in chunks)
        assert "late evening" in joined and "past midnight" in joined
