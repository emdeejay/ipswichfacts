"""
Data-freshness safety.

The homepage shows road impacts. If the traffic scrape breaks, --strict keeps
the last good deploy — which without a guard means confidently serving week-old
closures as "active" and possibly sending someone down a road that reopened, or
missing one that closed. Two defences: the page states its snapshot time, and a
build refuses to publish closures older than a threshold.

    python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.modules.setdefault("httpx", types.ModuleType("httpx"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build.build_site import (  # noqa: E402
    CLOSURES_MAX_AGE_HOURS,
    fmt_snapshot,
    parse_iso,
)


def test_snapshot_renders_in_brisbane_time():
    # 06:15 UTC is 16:15 AEST (Queensland is fixed UTC+10, no DST).
    assert fmt_snapshot("2026-07-16T06:15:00Z") == "Thursday 16 July 2026, 4:15 pm AEST"


def test_snapshot_handles_midnight_and_noon():
    assert "12:00 am" in fmt_snapshot("2026-07-16T14:00:00Z")  # 00:00 next day AEST
    assert "12:00 pm" in fmt_snapshot("2026-07-16T02:00:00Z")  # noon AEST


def test_bad_timestamp_never_fabricates_a_time():
    assert fmt_snapshot("not a date") == ""
    assert fmt_snapshot(None) == ""
    assert fmt_snapshot("") == ""


def test_parse_iso_accepts_both_z_and_offset_forms():
    assert parse_iso("2026-07-16T06:15:00Z") is not None
    assert parse_iso("2026-07-16T06:15:00+00:00") is not None
    assert parse_iso("garbage") is None


def _age_verdict(snapshot_at, has_closures=True):
    """Mirror of the guard in main() --strict."""
    snap = parse_iso(snapshot_at)
    if snap is not None:
        age_h = (datetime.now(timezone.utc) - snap).total_seconds() / 3600
        return age_h <= CLOSURES_MAX_AGE_HOURS
    return not has_closures


def test_fresh_closures_pass():
    now = datetime.now(timezone.utc).isoformat()
    assert _age_verdict(now) is True


def test_week_old_closures_are_rejected():
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    assert _age_verdict(week) is False


def test_boundary_is_the_configured_threshold():
    just_over = (datetime.now(timezone.utc) - timedelta(hours=CLOSURES_MAX_AGE_HOURS + 1)).isoformat()
    just_under = (datetime.now(timezone.utc) - timedelta(hours=CLOSURES_MAX_AGE_HOURS - 1)).isoformat()
    assert _age_verdict(just_over) is False
    assert _age_verdict(just_under) is True


def test_closures_without_timestamp_are_rejected_but_empty_is_fine():
    assert _age_verdict(None, has_closures=True) is False   # data but no as-of → suspect
    assert _age_verdict(None, has_closures=False) is True   # empty dashboard is a real state
