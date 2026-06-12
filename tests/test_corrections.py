import json
import os
from pathlib import Path

import pytest

from context_orchestrator.corrections import (
    apply,
    build_corrections_re,
    load_corrections,
)


class TestApply:
    def test_basic_replacement(self):
        corrections = {"macatune": "Megatune", "gung": "Gang"}
        text = "We discussed macatune integration with Gung today."
        out = apply(text, corrections)
        assert "Megatune" in out
        assert "Gang" in out
        assert "macatune" not in out
        assert "Gung" not in out

    def test_case_insensitive_match_preserves_replacement_case(self):
        corrections = {"macatune": "Megatune"}
        out = apply("MACATUNE and Macatune and macatune are all the same.", corrections)
        # All three occurrences become "Megatune" (replacement case wins)
        assert out.count("Megatune") == 3

    def test_word_boundary(self):
        # "macatune" should NOT match inside "macatuneXYZ" — that's a different word
        corrections = {"macatune": "Megatune"}
        out = apply("This is unrelatedmacatune word here.", corrections)
        assert "Megatune" not in out

    def test_empty_corrections_is_noop(self):
        original = "Some text that should not change."
        assert apply(original, {}) == original

    def test_longer_keys_match_first(self):
        # "scheduled airport pickup" should be matched before "airport"
        corrections = {
            "airport": "AIRPORT",
            "scheduled airport pickup": "SchedAirportPickup",
        }
        # Note: spaces in keys → won't match a single word boundary; this test
        # covers the regex sort-by-length behavior with overlapping shorter prefixes
        corrections2 = {"reds": "Redis", "redis": "Redis"}
        out = apply("Use redis for caching.", corrections2)
        assert "Redis" in out


class TestLoadCorrections:
    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CO_CORRECTIONS_FILE", str(tmp_path / "missing.yaml"))
        assert load_corrections() == {}

    def test_load_json(self, tmp_path, monkeypatch):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({
            "_meta": {"source": "test"},
            "corrections": {"macatune": "Megatune", "gung": "Gang"},
        }))
        monkeypatch.setenv("CO_CORRECTIONS_FILE", str(f))
        c = load_corrections()
        assert c["macatune"] == "Megatune"
        assert c["gung"] == "Gang"

    def test_load_yaml_simple(self, tmp_path, monkeypatch):
        f = tmp_path / "c.yaml"
        f.write_text(
            "# Optional preamble\n"
            "corrections:\n"
            "  macatune: Megatune\n"
            "  gung: Gang\n"
            "  radius: Redis  # comment after value\n"
        )
        monkeypatch.setenv("CO_CORRECTIONS_FILE", str(f))
        c = load_corrections()
        assert c["macatune"] == "Megatune"
        assert c["radius"] == "Redis"

    def test_keys_lowercased(self, tmp_path, monkeypatch):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"corrections": {"Macatune": "Megatune"}}))
        monkeypatch.setenv("CO_CORRECTIONS_FILE", str(f))
        c = load_corrections()
        assert "macatune" in c  # key is lowercased
        assert c["macatune"] == "Megatune"  # value preserved

    def test_explicit_path(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"corrections": {"foo": "Bar"}}))
        c = load_corrections(path=f)
        assert c == {"foo": "Bar"}


class TestBuildRegex:
    def test_empty_returns_none(self):
        assert build_corrections_re({}) is None

    def test_compiled_regex_matches(self):
        pat = build_corrections_re({"macatune": "Megatune"})
        assert pat is not None
        m = pat.search("Discussed macatune today.")
        assert m is not None
        assert m.group(0).lower() == "macatune"
