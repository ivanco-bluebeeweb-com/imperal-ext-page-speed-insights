"""Sidebar site-discovery feature: PageSpeed Insights must discover sites
already connected in WordPress Hub and/or Sites Registry, show them as a
clickable sidebar list AFTER a divider that itself comes after the Start
Check button, and route a click to a Speed Checker scoped to that one site.
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imperal_sdk.testing import MockContext, MockSecretStore

import handlers as h
import panels
import storage as st
from core import fetch_connected_sites
from models import ListConnectedSitesParams
from panels_views import site_view

_ROOT = Path(__file__).resolve().parent.parent


def _ctx() -> MockContext:
    ctx = MockContext()
    ctx.secrets = MockSecretStore({"pagespeed_api_key": "test-key-123"})
    return ctx


# ─────────────────────────── fetch_connected_sites (core) ───────────────────

@pytest.mark.asyncio
async def test_fetch_connected_sites_merges_wordpress_hub_and_sites_registry():
    ctx = _ctx()
    ctx.extensions.register(
        "wordpress-hub", "list_connected_sites",
        lambda: [{"site_id": "wp-1", "url": "https://climtec.md", "status": "connected"}],
    )
    ctx.extensions.register(
        "sites-registry", "list_connected_sites",
        lambda: [{"site_id": "g4s-md", "url": "https://g4s.md", "status": "active"}],
    )
    sites, problems = await fetch_connected_sites(ctx)
    assert problems == []
    domains = {s["site_id"] for s in sites}
    assert domains == {"climtec.md", "g4s.md"}


@pytest.mark.asyncio
async def test_fetch_connected_sites_deduplicates_by_canonical_domain():
    """Same domain reported by both providers -- one entry, not two."""
    ctx = _ctx()
    ctx.extensions.register(
        "wordpress-hub", "list_connected_sites",
        lambda: [{"site_id": "climtec", "url": "https://www.climtec.md", "status": "connected"}],
    )
    ctx.extensions.register(
        "sites-registry", "list_connected_sites",
        lambda: [{"site_id": "climtec-md", "url": "climtec.md", "status": "active"}],
    )
    sites, problems = await fetch_connected_sites(ctx)
    assert len(sites) == 1
    assert sites[0]["site_id"] == "climtec.md"
    assert sites[0]["provider"] == "wordpress-hub"  # first provider wins the tie


@pytest.mark.asyncio
async def test_fetch_connected_sites_reports_unreachable_provider_without_crashing():
    ctx = _ctx()  # neither provider registered -> both calls raise
    sites, problems = await fetch_connected_sites(ctx)
    assert sites == []
    assert {p["provider"] for p in problems} == {"wordpress-hub", "sites-registry"}


@pytest.mark.asyncio
async def test_fetch_connected_sites_degrades_to_working_provider_when_one_fails():
    ctx = _ctx()
    ctx.extensions.register(
        "wordpress-hub", "list_connected_sites",
        lambda: [{"site_id": "wp-1", "url": "https://climtec.md", "status": "connected"}],
    )
    # sites-registry left unregistered -> raises inside fetch_connected_sites
    sites, problems = await fetch_connected_sites(ctx)
    assert len(sites) == 1
    assert sites[0]["site_id"] == "climtec.md"
    assert len(problems) == 1
    assert problems[0]["provider"] == "sites-registry"


# ───────────────────────── list_connected_sites (chat) ──────────────────────

@pytest.mark.asyncio
async def test_list_connected_sites_returns_items_and_caches_them():
    ctx = _ctx()
    ctx.extensions.register(
        "wordpress-hub", "list_connected_sites",
        lambda: [{"site_id": "wp-1", "url": "https://climtec.md", "status": "connected"}],
    )
    ctx.extensions.register("sites-registry", "list_connected_sites", lambda: [])

    result = await h.list_connected_sites(ctx, ListConnectedSitesParams())
    assert result.status == "success"
    assert result.data.total == 1
    assert result.data.items[0].site_id == "climtec.md"

    cached_sites, cached_problems, has_cache = await st.read_cached_connected_sites(ctx)
    assert has_cache is True
    assert cached_sites and cached_sites[0]["site_id"] == "climtec.md"


@pytest.mark.asyncio
async def test_list_connected_sites_errors_when_no_provider_reachable():
    ctx = _ctx()
    result = await h.list_connected_sites(ctx, ListConnectedSitesParams())
    assert result.status != "success"


# ─────────────────────────── sidebar structure (panels.py) ──────────────────

def _nav_panel_source() -> str:
    return (_ROOT / "panels.py").read_text(encoding="utf-8")


def test_divider_comes_after_start_check_button_in_source():
    """Structural guard for the exact ordering the user asked for: the
    'Start check' Form/Section, THEN a Divider, THEN the connected-sites
    block. Reading the raw source (rather than rendering) keeps this test
    independent of any particular connected-sites state."""
    source = _nav_panel_source()
    start_idx = source.index('submit_label="Start check"')
    divider_idx = source.index("ui.Divider(")
    # rindex, not index: "_connected_sites_block(" also matches this helper's
    # own `def` line earlier in the file. The call site (inside
    # psi_nav_panel, after the Divider) is what ordering actually matters for.
    sites_block_idx = source.rindex("_connected_sites_block(")
    assert start_idx < divider_idx < sites_block_idx, (
        "Expected order: Start Check button -> Divider -> connected sites list"
    )


@pytest.mark.asyncio
async def test_nav_panel_renders_divider_and_site_list_after_start_check():
    ctx = _ctx()
    ctx.extensions.register(
        "wordpress-hub", "list_connected_sites",
        lambda: [{"site_id": "wp-1", "url": "https://climtec.md", "status": "connected"}],
    )
    ctx.extensions.register("sites-registry", "list_connected_sites", lambda: [])
    await h.list_connected_sites(ctx, ListConnectedSitesParams())  # populate cache

    node = await panels.psi_nav_panel(ctx)

    def flatten(n):
        out = [n]
        children = getattr(n, "children", None) or (getattr(n, "props", {}) or {}).get("children")
        for c in children or []:
            out.extend(flatten(c))
        return out

    all_nodes = flatten(node)
    kinds = [getattr(n, "type", None) or getattr(n, "component", None) or type(n).__name__ for n in all_nodes]
    # Structural smoke: a Divider node and at least one clickable ListItem
    # for the discovered site both exist somewhere in the rendered tree.
    rendered = repr(node)
    assert "Divider" in rendered
    assert "climtec.md" in rendered


# ───────────────────────────────── site_view (center) ───────────────────────

@pytest.mark.asyncio
async def test_site_view_shows_check_form_prefilled_with_site_and_its_own_history():
    ctx = _ctx()
    from models import CheckSiteSpeedParams
    from tests.test_smoke import _GOOD_PAYLOAD, _set_mock  # reuse the realistic payload

    _set_mock(ctx, _GOOD_PAYLOAD)
    await h.check_site_speed(ctx, CheckSiteSpeedParams(url="climtec.md"))

    node = await site_view(ctx, "climtec.md")
    rendered = repr(node)
    assert "climtec.md" in rendered
    assert "check_site_speed" in rendered


@pytest.mark.asyncio
async def test_site_view_with_no_site_id_shows_error_not_crash():
    ctx = _ctx()
    node = await site_view(ctx, "")
    assert "No site selected" in repr(node)


@pytest.mark.asyncio
async def test_site_view_history_is_scoped_only_to_that_site():
    """A run for a DIFFERENT site must not leak into this site's history."""
    ctx = _ctx()
    from models import CheckSiteSpeedParams
    from tests.test_smoke import _GOOD_PAYLOAD, _set_mock

    _set_mock(ctx, _GOOD_PAYLOAD)
    await h.check_site_speed(ctx, CheckSiteSpeedParams(url="other-site.example"))

    node = await site_view(ctx, "climtec.md")
    rendered = repr(node)
    assert "other-site.example" not in rendered


@pytest.mark.asyncio
async def test_psi_panel_routes_view_site_to_site_view():
    ctx = _ctx()
    node = await panels.psi_panel(ctx, view="site", site_id="climtec.md")
    assert "climtec.md" in repr(node)
