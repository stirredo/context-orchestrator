from context_orchestrator.chunking import chunk_text


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
