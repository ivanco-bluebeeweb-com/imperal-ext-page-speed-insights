"""Shared PageSpeed checking core used by chat, IPC, and schedules."""

from __future__ import annotations

import codes as c
import psi_client as psi
import storage as st
from models import (
    DEFAULT_THRESHOLDS, MetricValue, Opportunity, ScheduleState,
    SettingsState, SpeedSnapshot, ThresholdsState,
)
from shared import categorize

# Registry of app_ids that expose a "list_connected_sites" IPC method
# returning [{"site_id", "name"/"url", "status"}, ...]. Both WordPress Hub
# (a real WordPress connector) and Sites Registry (the platform-agnostic
# catalogue -- covers non-WordPress sites too) are queried, per explicit
# product decision: the sidebar must discover sites from EITHER source, not
# just one. Order matters only as a tie-breaker when the same domain is
# somehow reported by both -- the first provider's row wins.
SITE_PROVIDER_APP_IDS: list[str] = ["wordpress-hub", "sites-registry"]

# Marketplace display_name for each provider app_id -- shown under a site's
# bare domain in the sidebar so it's clear WHERE that connected site comes
# from, using the same name the user sees when browsing the Marketplace
# (confirmed via marketplace.list_my_installed), not the raw app_id slug.
PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "wordpress-hub": "WordPress Hub",
    "sites-registry": "Sites Registry",
}


def provider_display_name(app_id: str) -> str:
    return PROVIDER_DISPLAY_NAMES.get(app_id, app_id)


def _canonical_site_id(row: dict) -> str:
    """Normalise a provider's site identifier to its bare domain, the same
    scheme content-strategy-app/brand-strategy-hub already use -- so a site
    connected in WordPress Hub and also registered in Sites Registry is
    recognised as ONE site, not shown twice."""
    host = (row.get("url") or "").strip().split("://", 1)[-1].split("/", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host or (row.get("site_id") or "")


async def fetch_connected_sites(ctx) -> tuple[list[dict], list[dict]]:
    """Pull every connected site from WordPress Hub and Sites Registry via
    ctx.extensions.call -- direct in-process IPC, no chat round-trip.

    Returns (sites, problems). A provider that fails is reported in
    `problems` as {"provider", "reason"} instead of silently vanishing.
    Sites are de-duplicated by canonical domain across providers.
    """
    sites: list[dict] = []
    seen_ids: set[str] = set()
    problems: list[dict] = []
    for app_id in SITE_PROVIDER_APP_IDS:
        try:
            rows = await ctx.extensions.call(app_id, "list_connected_sites")
        except Exception as exc:  # noqa: BLE001 -- surfaced to the panel, not swallowed
            problems.append({
                "provider": app_id,
                "reason": f"{type(exc).__name__}: {exc}".strip()[:300],
            })
            continue
        for r in rows or []:
            canonical = _canonical_site_id(r)
            if not canonical or canonical in seen_ids:
                continue
            seen_ids.add(canonical)
            sites.append({
                "site_id": canonical,
                "url": r.get("url", "") or canonical,
                "status": r.get("status", ""),
                "provider": app_id,
            })
    return sites, problems


def normalize_url(url: str) -> str:
    """Normalize a bare domain to HTTPS while keeping a full URL unchanged."""
    value = (url or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


async def get_api_key(ctx) -> str | None:
    return await ctx.secrets.get("pagespeed_api_key")


async def build_settings_state(ctx) -> SettingsState:
    raw = await st.get_settings(ctx)
    key = await get_api_key(ctx)
    thresholds = raw.get("thresholds") or DEFAULT_THRESHOLDS
    schedule = raw.get("schedule") or {}
    return SettingsState(
        id="settings",
        title="Page Speed Insights -- settings",
        key_connected=bool(key),
        thresholds=ThresholdsState(**thresholds),
        default_categories=raw.get("default_categories") or ["performance"],
        retention_days=raw.get("retention_days", 30),
        notify_mode=raw.get("notify_mode", "regressions"),
        schedule=ScheduleState(
            enabled=schedule.get("enabled", False),
            hour=schedule.get("hour", 3),
            sites=schedule.get("sites") or [],
            last_run_date=schedule.get("last_run_date", ""),
        ),
    )


async def begin_speed_run(ctx, url: str, strategy: str, categories: list[str]) -> dict:
    """Persist a ``Running`` row before the provider request starts."""
    key = await get_api_key(ctx)
    if not key:
        raise psi.ProviderError(
            "Google PageSpeed Insights key is not connected. Connect it before running a check.",
            c.PSI_NO_KEY,
        )
    full_url = normalize_url(url)
    if not full_url:
        raise psi.ProviderError("No URL was given to check.", c.PSI_NO_URL)

    started_at = st.now_iso()
    run_id = await st.create_run(ctx, {
        "url": full_url,
        "strategy": strategy,
        "categories": categories,
        "scores": {},
        "field_metrics": [],
        "lab_metrics": [],
        "has_field_data": False,
        "opportunities": [],
        "status": "running",
        "started_at": started_at,
        "checked_at": started_at,
        "error": "",
    })
    return {
        "id": run_id, "url": full_url, "strategy": strategy,
        "categories": categories, "status": "running",
        "started_at": started_at, "checked_at": started_at,
    }


async def complete_speed_run(ctx, run: dict) -> dict:
    """Complete an existing run and update its original row in place."""
    run_id = run["id"]
    try:
        key = await get_api_key(ctx)
        if not key:
            raise psi.ProviderError(
                "Google PageSpeed Insights key is not connected.", c.PSI_NO_KEY,
            )
        payload = await psi.run_pagespeed(
            ctx, key, run["url"], strategy=run["strategy"], categories=run["categories"],
        )
        thresholds = (await st.get_settings(ctx)).get("thresholds") or DEFAULT_THRESHOLDS
        scores = psi.extract_scores(payload)
        lab_metrics = psi.extract_lab_metrics(payload)
        field_metrics, has_field_data = psi.extract_field_metrics(payload)
        for metric in lab_metrics + field_metrics:
            metric["category"] = categorize(metric["name"], metric["value"], thresholds)
        completed_at = st.now_iso()
        doc = {
            **run,
            "scores": scores,
            "field_metrics": field_metrics,
            "lab_metrics": lab_metrics,
            "has_field_data": has_field_data,
            "opportunities": psi.extract_opportunities(payload),
            "status": "completed",
            "checked_at": completed_at,
            "completed_at": completed_at,
            "error": "",
        }
        await st.update_run(ctx, run_id, {key: value for key, value in doc.items() if key != "id"})
        return doc
    except psi.ProviderError as exc:
        failed_at = st.now_iso()
        await st.update_run(ctx, run_id, {
            "status": "failed", "checked_at": failed_at, "completed_at": failed_at,
            "error": str(exc),
        })
        raise
    except Exception:
        failed_at = st.now_iso()
        await st.update_run(ctx, run_id, {
            "status": "failed", "checked_at": failed_at, "completed_at": failed_at,
            "error": "The PageSpeed check could not finish.",
        })
        raise


async def run_and_save(ctx, url: str, strategy: str, categories: list[str]) -> dict:
    """Synchronous convenience path for schedules and IPC callers."""
    return await complete_speed_run(ctx, await begin_speed_run(ctx, url, strategy, categories))


def to_metric_values(raw: list[dict]) -> list[MetricValue]:
    return [MetricValue(**item) for item in raw]


def to_opportunities(raw: list[dict]) -> list[Opportunity]:
    return [Opportunity(**item) for item in raw]


def doc_to_snapshot(doc: dict) -> SpeedSnapshot:
    return SpeedSnapshot(
        id=doc.get("id", ""),
        title=f"{doc.get('url', '')} ({doc.get('strategy', '')})",
        url=doc.get("url", ""), strategy=doc.get("strategy", ""),
        categories=doc.get("categories") or [], scores=doc.get("scores") or {},
        field_metrics=to_metric_values(doc.get("field_metrics") or []),
        lab_metrics=to_metric_values(doc.get("lab_metrics") or []),
        has_field_data=bool(doc.get("has_field_data")),
        opportunities=to_opportunities(doc.get("opportunities") or []),
        checked_at=doc.get("checked_at", ""),
        status=doc.get("status", "completed"),
    )
