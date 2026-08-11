"""Smoke tests for Page Speed Insights: connect/disconnect key, a full
check_site_speed run against a realistic mocked Google response, snapshot
history/comparison, every App settings save/read handler, and the IPC
surface other extensions call (check_site_speed_ipc + ping).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imperal_sdk.testing import MockContext, MockSecretStore

import handlers as h
import handlers_ipc as hi
from models import (
    CheckSiteSpeedParams, CompareSnapshotsParams, ConnectPagespeedParams,
    GetScheduleParams, GetSnapshotParams, ListSnapshotsParams, NoParams,
    SaveCategoryTogglesParams, SaveNotifyModeParams, SaveRetentionParams,
    SaveScheduleParams, SaveThresholdsParams,
)

BASE_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# A realistic (trimmed but shape-accurate) runPagespeed response: both a
# lighthouseResult lab run AND a loadingExperience field-data block, so
# tests exercise both extraction paths, not just one.
_GOOD_PAYLOAD = {
    "lighthouseResult": {
        "categories": {
            "performance": {"score": 0.92},
            "seo": {"score": 1.0},
        },
        "audits": {
            "largest-contentful-paint": {"numericValue": 1800.0, "score": 0.95},
            "cumulative-layout-shift": {"numericValue": 0.05, "score": 0.98},
            "server-response-time": {"numericValue": 220.0, "score": 0.9},
            "render-blocking-resources": {
                "title": "Eliminate render-blocking resources",
                "description": "Resources are blocking the first paint.",
                "score": 0.4,
                "details": {"overallSavingsMs": 640.0},
            },
        },
    },
    "loadingExperience": {
        "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2100},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 8},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 180},
        }
    },
}

_POOR_PAYLOAD = {
    "lighthouseResult": {
        "categories": {"performance": {"score": 0.4}},
        "audits": {
            "largest-contentful-paint": {"numericValue": 5200.0, "score": 0.1},
            "cumulative-layout-shift": {"numericValue": 0.05, "score": 0.98},
        },
    },
    "loadingExperience": {"metrics": {}},
}


def _ctx() -> MockContext:
    """MockContext with an empty secrets store attached -- MockContext has
    no `secrets` attribute by default (confirmed via dir(MockContext())),
    same gap Media Studio's own tests/conftest.py hit and fixed the same
    way."""
    ctx = MockContext()
    ctx.secrets = MockSecretStore({})
    return ctx


def _set_mock(ctx: MockContext, payload: dict, status: int = 200) -> None:
    """Replace the runPagespeed mock response for subsequent calls.

    `MockHTTP._find` returns the FIRST registered mock whose pattern is a
    substring of the requested URL (confirmed by reading its source) --
    it does NOT overwrite on a second `mock_get` for the same pattern. A
    test that needs connect_pagespeed to see one payload and a later
    check_site_speed to see a DIFFERENT one must clear the list first."""
    ctx.http._mocks.clear()
    ctx.http.mock_get(BASE_URL, payload, status=status)


async def _connected_ctx() -> MockContext:
    ctx = _ctx()
    _set_mock(ctx, _GOOD_PAYLOAD)
    await h.connect_pagespeed(ctx, ConnectPagespeedParams(api_key="good-key"))
    return ctx


# ─────────────────────────── connect / disconnect ───────────────────────────

@pytest.mark.asyncio
async def test_connect_pagespeed_rejects_key_google_rejects():
    ctx = _ctx()
    ctx.http.mock_get(BASE_URL, {"error": {"message": "bad key"}}, status=400)
    result = await h.connect_pagespeed(ctx, ConnectPagespeedParams(api_key="bad-key"))
    assert result.status != "success"
    assert await ctx.secrets.is_set("pagespeed_api_key") is False


@pytest.mark.asyncio
async def test_connect_pagespeed_saves_key_google_accepts():
    ctx = _ctx()
    ctx.http.mock_get(BASE_URL, _GOOD_PAYLOAD, status=200)
    result = await h.connect_pagespeed(ctx, ConnectPagespeedParams(api_key="good-key"))
    assert result.status == "success"
    assert await ctx.secrets.get("pagespeed_api_key") == "good-key"


@pytest.mark.asyncio
async def test_connect_pagespeed_empty_key_rejected_without_any_call():
    ctx = _ctx()
    result = await h.connect_pagespeed(ctx, ConnectPagespeedParams(api_key="  "))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_disconnect_pagespeed_removes_key():
    ctx = await _connected_ctx()
    result = await h.disconnect_pagespeed(ctx, NoParams())
    assert result.status == "success"
    assert await ctx.secrets.is_set("pagespeed_api_key") is False


# ─────────────────────────────── check_site_speed ───────────────────────────

@pytest.mark.asyncio
async def test_check_site_speed_requires_connected_key():
    ctx = _ctx()
    result = await h.check_site_speed(ctx, CheckSiteSpeedParams(url="climtec.md"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_check_site_speed_normalizes_bare_domain_and_extracts_metrics():
    ctx = await _connected_ctx()
    result = await h.check_site_speed(ctx, CheckSiteSpeedParams(url="climtec.md", strategy="mobile"))
    assert result.status == "success"
    snap = result.data
    assert snap.url == "https://climtec.md"
    assert snap.scores["performance"] == 0.92
    assert snap.has_field_data is True
    lcp = next(m for m in snap.field_metrics if m.name == "LCP")
    assert lcp.value == 2100.0
    assert lcp.category == "good"  # <= 2500ms good threshold
    assert len(snap.opportunities) == 1
    assert snap.opportunities[0].savings_ms == 640.0
    assert snap.status == "completed"

    # The UI lifecycle keeps one durable run row: it starts as Running before
    # the provider call and is updated in place to Completed, never duplicated.
    saved = await h.list_speed_snapshots(ctx, ListSnapshotsParams())
    assert saved.status == "success"
    assert saved.data.total == 1
    assert saved.data.items[0].id == snap.id
    assert saved.data.items[0].status == "completed"


@pytest.mark.asyncio
async def test_check_site_speed_poor_lcp_categorized_poor():
    ctx = await _connected_ctx()
    _set_mock(ctx, _POOR_PAYLOAD)
    result = await h.check_site_speed(ctx, CheckSiteSpeedParams(url="slow.example"))
    assert result.status == "success"
    lcp = next(m for m in result.data.lab_metrics if m.name == "LCP")
    assert lcp.category == "poor"  # 5200ms > 4000ms poor threshold


@pytest.mark.asyncio
async def test_check_site_speed_surfaces_rate_limit_as_retryable_error():
    ctx = await _connected_ctx()
    _set_mock(ctx, {"error": "rate limited"}, status=429)
    result = await h.check_site_speed(ctx, CheckSiteSpeedParams(url="busy.example"))
    assert result.status != "success"


# ─────────────────────────── history / comparison ───────────────────────────

@pytest.mark.asyncio
async def test_list_speed_snapshots_empty_is_honest_not_a_crash():
    ctx = _ctx()
    result = await h.list_speed_snapshots(ctx, ListSnapshotsParams())
    assert result.status != "success"


@pytest.mark.asyncio
async def test_list_and_get_speed_snapshot_roundtrip():
    ctx = await _connected_ctx()
    created = await h.check_site_speed(ctx, CheckSiteSpeedParams(url="climtec.md"))
    listed = await h.list_speed_snapshots(ctx, ListSnapshotsParams())
    assert listed.status == "success"
    assert listed.data.total == 1
    snap_id = listed.data.items[0].id
    got = await h.get_speed_snapshot(ctx, GetSnapshotParams(snapshot_id=snap_id))
    assert got.status == "success"
    assert got.data.url == "https://climtec.md"


@pytest.mark.asyncio
async def test_get_speed_snapshot_missing_id_errors():
    ctx = _ctx()
    result = await h.get_speed_snapshot(ctx, GetSnapshotParams(snapshot_id="missing"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_compare_speed_snapshots_needs_two_runs():
    ctx = await _connected_ctx()
    await h.check_site_speed(ctx, CheckSiteSpeedParams(url="climtec.md"))
    result = await h.compare_speed_snapshots(ctx, CompareSnapshotsParams(url="climtec.md", strategy="mobile"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_compare_speed_snapshots_detects_regression():
    ctx = await _connected_ctx()
    await h.check_site_speed(ctx, CheckSiteSpeedParams(url="climtec.md"))
    _set_mock(ctx, _POOR_PAYLOAD)
    await h.check_site_speed(ctx, CheckSiteSpeedParams(url="climtec.md"))
    result = await h.compare_speed_snapshots(ctx, CompareSnapshotsParams(url="climtec.md", strategy="mobile"))
    assert result.status == "success"
    assert result.data.regressed is True
    assert result.data.score_deltas["performance"] < 0


# ────────────────────────────── App settings ────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_read_thresholds_roundtrip():
    ctx = _ctx()
    await h.save_speed_thresholds(ctx, SaveThresholdsParams(
        lcp_good_ms=2000, lcp_poor_ms=3500, cls_good=0.08, cls_poor=0.2,
        inp_good_ms=150, inp_poor_ms=400,
    ))
    settings = await h.get_speed_settings(ctx, GetScheduleParams())
    assert settings.status == "success"
    assert settings.data.thresholds.lcp_good_ms == 2000


@pytest.mark.asyncio
async def test_save_speed_categories_always_keeps_performance():
    ctx = _ctx()
    result = await h.save_speed_categories(ctx, SaveCategoryTogglesParams(categories=["seo"]))
    assert result.status == "success"
    assert "performance" in result.data.default_categories
    assert "seo" in result.data.default_categories


@pytest.mark.asyncio
async def test_save_speed_retention_and_notify_mode_persist():
    ctx = _ctx()
    await h.save_speed_retention(ctx, SaveRetentionParams(retention_days=45))
    await h.save_speed_notify_mode(ctx, SaveNotifyModeParams(notify_mode="regressions"))
    settings = await h.get_speed_settings(ctx, GetScheduleParams())
    assert settings.data.retention_days == 45
    assert settings.data.notify_mode == "regressions"


@pytest.mark.asyncio
async def test_save_speed_schedule_parses_site_list():
    ctx = _ctx()
    result = await h.save_speed_schedule(ctx, SaveScheduleParams(
        enabled=True, hour=4, sites="climtec.md, g4s.md\nksrenovationgroup.com",
    ))
    assert result.status == "success"
    assert result.data.schedule.sites == ["climtec.md", "g4s.md", "ksrenovationgroup.com"]
    assert result.data.schedule.enabled is True
    assert result.data.schedule.hour == 4


@pytest.mark.asyncio
async def test_get_speed_settings_defaults_when_nothing_saved_yet():
    ctx = _ctx()
    result = await h.get_speed_settings(ctx, GetScheduleParams())
    assert result.status == "success"
    assert result.data.key_connected is False
    assert result.data.thresholds.lcp_good_ms == 2500  # official Google default
    assert result.data.thresholds.cls_poor == 0.25
    assert result.data.schedule.enabled is False


# ───────────────────────────────── IPC surface ──────────────────────────────

@pytest.mark.asyncio
async def test_expose_ping_returns_ok_true_with_no_side_effects():
    ctx = _ctx()
    result = await hi.expose_ping(ctx)
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_expose_check_site_speed_ipc_degrades_gracefully_without_key():
    """The exact behaviour SEO Audit Engine (or any caller) depends on:
    NEVER raise across the IPC boundary -- always a dict with ok=False."""
    ctx = _ctx()
    result = await hi.expose_check_site_speed(ctx, url="climtec.md")
    assert result["ok"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_expose_check_site_speed_ipc_returns_real_metrics_on_success():
    ctx = await _connected_ctx()
    result = await hi.expose_check_site_speed(ctx, url="climtec.md", strategy="mobile")
    assert result["ok"] is True
    assert result["scores"]["performance"] == 0.92
    assert result["has_field_data"] is True
    assert len(result["top_opportunities"]) <= 5


@pytest.mark.asyncio
async def test_expose_check_site_speed_ipc_requires_url():
    ctx = await _connected_ctx()
    result = await hi.expose_check_site_speed(ctx, url="")
    assert result["ok"] is False
