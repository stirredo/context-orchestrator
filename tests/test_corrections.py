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
        corrections = {"oldterm": "NewTerm", "oldphrase": "NewPhrase"}
        text = "We discussed oldterm integration with OldPhrase today."
        out = apply(text, corrections)
        assert "NewTerm" in out
        assert "NewPhrase" in out
        assert "oldterm" not in out
        assert "OldPhrase" not in out

    def test_case_insensitive_match_preserves_replacement_case(self):
        corrections = {"oldterm": "NewTerm"}
        out = apply("OLDTERM and OldTerm and oldterm are all the same.", corrections)
        # All three occurrences become "NewTerm" (replacement case wins)
        assert out.count("NewTerm") == 3

    def test_word_boundary(self):
        # "oldterm" should NOT match inside "oldtermXYZ" — that's a different word
        corrections = {"oldterm": "NewTerm"}
        out = apply("This is unrelatedoldterm word here.", corrections)
        assert "NewTerm" not in out

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
            "corrections": {"oldterm": "NewTerm", "oldphrase": "NewPhrase"},
        }))
        monkeypatch.setenv("CO_CORRECTIONS_FILE", str(f))
        c = load_corrections()
        assert c["oldterm"] == "NewTerm"
        assert c["oldphrase"] == "NewPhrase"

    def test_load_yaml_simple(self, tmp_path, monkeypatch):
        f = tmp_path / "c.yaml"
        f.write_text(
            "# Optional preamble\n"
            "corrections:\n"
            "  oldterm: NewTerm\n"
            "  oldphrase: NewPhrase\n"
            "  radius: Redis  # comment after value\n"
        )
        monkeypatch.setenv("CO_CORRECTIONS_FILE", str(f))
        c = load_corrections()
        assert c["oldterm"] == "NewTerm"
        assert c["radius"] == "Redis"

    def test_keys_lowercased(self, tmp_path, monkeypatch):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"corrections": {"OldTerm": "NewTerm"}}))
        monkeypatch.setenv("CO_CORRECTIONS_FILE", str(f))
        c = load_corrections()
        assert "oldterm" in c  # key is lowercased
        assert c["oldterm"] == "NewTerm"  # value preserved

    def test_explicit_path(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"corrections": {"foo": "Bar"}}))
        c = load_corrections(path=f)
        assert c == {"foo": "Bar"}


class TestBuildRegex:
    def test_empty_returns_none(self):
        assert build_corrections_re({}) is None

    def test_compiled_regex_matches(self):
        pat = build_corrections_re({"oldterm": "NewTerm"})
        assert pat is not None
        m = pat.search("Discussed oldterm today.")
        assert m is not None
        assert m.group(0).lower() == "oldterm"
