from context_orchestrator.chunking import chunk_text, is_hallucination


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
            "So we'll start with the Lyft features. What we're looking at on the left is "
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
