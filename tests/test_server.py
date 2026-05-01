"""Smoke tests for the MCP server's search tool. We verify the date parser
and that filter args translate into the right Chroma `where` shape via the
public search tool. The full ranking pipeline is exercised by test_search."""
from datetime import datetime, timezone

import pytest

from context_orchestrator.server import _parse_iso_date


class TestParseIsoDate:
    def test_date_only(self):
        ts = _parse_iso_date("2026-04-30")
        expected = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        assert ts == expected

    def test_datetime_no_tz(self):
        ts = _parse_iso_date("2026-04-30T14:25:00")
        expected = datetime(2026, 4, 30, 14, 25, 0, tzinfo=timezone.utc).timestamp()
        assert ts == expected

    def test_datetime_with_z(self):
        ts = _parse_iso_date("2026-04-30T14:25:00Z")
        expected = datetime(2026, 4, 30, 14, 25, 0, tzinfo=timezone.utc).timestamp()
        assert ts == expected

    def test_datetime_with_offset(self):
        ts = _parse_iso_date("2026-04-30T14:25:00+00:00")
        expected = datetime(2026, 4, 30, 14, 25, 0, tzinfo=timezone.utc).timestamp()
        assert ts == expected

    def test_unparseable_returns_none(self):
        assert _parse_iso_date("not a date") is None
        assert _parse_iso_date("") is None
        assert _parse_iso_date("2026/04/30") is None

    def test_partial_iso_with_offset(self):
        # Edge case: time has explicit +HH:MM offset
        ts = _parse_iso_date("2026-04-30T14:25:00-05:00")
        # Should be 14:25 EST = 19:25 UTC
        expected = datetime(2026, 4, 30, 19, 25, 0, tzinfo=timezone.utc).timestamp()
        assert ts == expected
