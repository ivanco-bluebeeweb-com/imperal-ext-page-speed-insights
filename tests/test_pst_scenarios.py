"""Plausible Scenario Tests (PST) -- Page Speed Insights.

Method: Docs/session-notes/SCENARIO_TESTING_STANDARD.md. This app has 13
functions (11 @chat.function + 2 @ext.expose IPC surfaces) and 56 existing
tests. A name-based coverage audit found all 11 chat functions already
exercised at least once, but the two IPC-only surfaces used by SEO Audit
Engine (documented in handlers_ipc.py as the primary integration contract)
had zero test call sites:

    ping, check_site_speed_ipc

This file closes those 2 gaps, since a silent regression here would be
invisible from this app's own chat surface but would break a downstream
consumer's best-effort degradation path.
"""
from __future__ import annotations

import pytest

import core
import handlers_ipc as hipc
import psi_client as psi


pytestmark = pytest.mark.asyncio


# ── ping: presence probe, never touches the store ───────────────────────────

async def test_happy_ping_always_ok(ctx):
    out = await hipc.expose_ping(ctx)
    assert out == {"ok": True}


async def test_happy_ping_ok_even_without_a_connected_key(ctx):
    # ping distinguishes "not installed" from "installed but no key" for the
    # caller -- it must stay {"ok": True} even when no PSI key is connected.
    out = await hipc.expose_ping(ctx)
    assert out["ok"] is True


# ── check_site_speed_ipc: always returns a dict, never raises ───────────────

async def test_error_check_site_speed_ipc_requires_url(ctx_with_key):
    out = await hipc.expose_check_site_speed(ctx_with_key, url="")
    assert out == {"ok": False, "error": "url is required", "retryable": False}


async def test_happy_check_site_speed_ipc_returns_flat_scores(ctx_with_key, monkeypatch):
    async def fake_run_and_save(_ctx, url, strategy, categories):
        return {
            "id": "run1", "url": url, "strategy": strategy,
            "scores": {"performance": 87}, "field_metrics": [], "lab_metrics": [],
            "has_field_data": False, "opportunities": [{"id": "op1"}],
            "checked_at": "2026-08-19T00:00:00Z",
        }
    monkeypatch.setattr(hipc, "run_and_save", fake_run_and_save)

    out = await hipc.expose_check_site_speed(ctx_with_key, url="https://example.com")
    assert out["ok"] is True
    assert out["scores"] == {"performance": 87}
    assert out["top_opportunities"] == [{"id": "op1"}]


async def test_error_check_site_speed_ipc_never_raises_on_provider_error(ctx_with_key, monkeypatch):
    """The IPC contract promises a dict even on failure -- SEO Audit Engine
    degrades best-effort and must never see an exception cross the boundary."""
    async def fake_run_and_save(_ctx, url, strategy, categories):
        raise psi.ProviderError("Google rejected the request.", "PSI_BAD_REQUEST", retryable=False)
    monkeypatch.setattr(hipc, "run_and_save", fake_run_and_save)

    out = await hipc.expose_check_site_speed(ctx_with_key, url="https://example.com")
    assert out == {
        "ok": False, "error": "Google rejected the request.", "retryable": False,
    }


async def test_recovery_check_site_speed_ipc_retryable_flag_survives(ctx_with_key, monkeypatch):
    async def fake_run_and_save(_ctx, url, strategy, categories):
        raise psi.ProviderError("Rate limited.", "PSI_RATE_LIMITED", retryable=True)
    monkeypatch.setattr(hipc, "run_and_save", fake_run_and_save)

    out = await hipc.expose_check_site_speed(ctx_with_key, url="https://example.com")
    assert out["ok"] is False
    assert out["retryable"] is True
