"""Chat-функции: ключ, проверка, история, настройки.

IPC-поверхность (@ext.expose) живёт в handlers_ipc.py -- разделение по
входу (чат vs межпроцессный вызов), а не по CRUD-слою, потому что именно
это разделение важно контракту PREPARATION.md (однонаправленная
IPC-зависимость должна быть видна как отдельный маленький файл, а не
затеряна среди 12 chat-функций).
"""

from __future__ import annotations

from imperal_sdk import ActionResult

import codes as c
import psi_client as psi
import storage as st
from app import chat
from core import (
    begin_speed_run, build_settings_state, complete_speed_run, doc_to_snapshot,
    normalize_url, run_and_save,
)
from models import (
    CheckSiteSpeedParams, CompareSnapshotsParams, ComparisonResult,
    ConnectPagespeedParams, GetScheduleParams, GetSnapshotParams,
    ListSnapshotsParams, NoParams, SaveCategoryTogglesParams,
    SaveNotifyModeParams, SaveRetentionParams, SaveScheduleParams,
    SaveThresholdsParams, SettingsState, SnapshotList, SnapshotSummary,
    SpeedSnapshot,
)
from shared import error as _error

# ──────────────────────────────────────────────────────────────────────────
# Ключ: connect/disconnect
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "connect_pagespeed",
    description=(
        "Connect Page Speed Insights by saving your Google API key, after "
        "checking it actually works. Get a free key at console.cloud.google.com "
        "-> APIs & Services -> enable 'PageSpeed Insights API' -> Credentials -> "
        "Create API key."
    ),
    action_type="write",
    chain_callable=True,
    effects=["secret.write"],
    event="page-speed-insights.connect_pagespeed",
    data_model=SettingsState,
)
async def connect_pagespeed(ctx, params: ConnectPagespeedParams) -> ActionResult:
    """Validate the pasted Google API key against the real PageSpeed Insights
    endpoint BEFORE saving it, so a bad paste is rejected immediately instead
    of failing silently on the first real check later."""
    key = (params.api_key or "").strip()
    if not key:
        return _error("Please provide a real API key.", c.PSI_NO_KEY)
    try:
        await psi.validate_api_key(ctx, key)
    except psi.ProviderError as exc:
        return _error(str(exc), exc.code, exc.retryable)
    await ctx.secrets.set("pagespeed_api_key", key)
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary="Google PageSpeed Insights key connected and verified.",
        refresh_panels=["psi_nav", "psi"],
    )


@chat.function(
    "disconnect_pagespeed",
    description="Disconnect Page Speed Insights: deletes the saved API key. Existing snapshots stay.",
    action_type="write",
    chain_callable=True,
    effects=["secret.delete"],
    event="page-speed-insights.disconnect_pagespeed",
    data_model=SettingsState,
)
async def disconnect_pagespeed(ctx, params: NoParams) -> ActionResult:
    """Delete the saved key. Past snapshots are untouched -- only future
    checks are blocked until a key is connected again."""
    await ctx.secrets.delete("pagespeed_api_key")
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary="Key disconnected. Past speed snapshots stay available.",
        refresh_panels=["psi_nav", "psi"],
    )


# ──────────────────────────────────────────────────────────────────────────
# Основная проверка
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "check_site_speed",
    description=(
        "Run a real Google PageSpeed Insights check for one URL. The run appears "
        "in the central history immediately as Running, then becomes Completed or "
        "Failed with its scores and fix opportunities."
    ),
    action_type="write",
    chain_callable=True,
    background=True,
    long_running=True,
    effects=["speed_snapshot.create", "speed_snapshot.update"],
    event="page-speed-insights.check_site_speed",
    data_model=SpeedSnapshot,
)
async def check_site_speed(ctx, params: CheckSiteSpeedParams) -> ActionResult:
    """Create a visible run immediately, then complete it in the background."""
    try:
        run = await begin_speed_run(ctx, params.url, params.strategy, params.categories)
    except psi.ProviderError as exc:
        return _error(str(exc), exc.code, exc.retryable)

    async def work() -> ActionResult:
        try:
            doc = await complete_speed_run(ctx, run)
        except psi.ProviderError as exc:
            return _error(str(exc), exc.code, exc.retryable)
        except Exception:
            # complete_speed_run has already marked the durable row Failed.
            # Background tasks must still return a structured result to the host.
            return _error("The PageSpeed check could not finish.", c.PSI_PROVIDER_ERROR, True)
        perf = doc["scores"].get("performance")
        perf_txt = f"{round(perf * 100)}/100" if perf is not None else "n/a"
        return ActionResult.success(
            data=doc_to_snapshot(doc),
            summary=(f"Check completed for {doc['url']} ({doc['strategy']}): "
                     f"Performance {perf_txt}."),
            refresh_panels=["psi_nav", "psi"],
        )

    coro = work()
    try:
        await ctx.background_task(coro, long_running=True, name="pagespeed-check")
    except (RuntimeError, AttributeError):
        # Tests and local/dev callers do not have the kernel spawn hook.
        return await coro

    return ActionResult.success(
        data=doc_to_snapshot(run),
        summary=(f"Check started for {run['url']} ({run['strategy']}). "
                 "It is now visible in the run history."),
        refresh_panels=["psi_nav", "psi"],
    )


@chat.function(
    "list_speed_snapshots",
    description="List past Page Speed Insights snapshots, optionally filtered by url/strategy.",
    action_type="read",
    chain_callable=True,
    data_model=SnapshotList,
)
async def list_speed_snapshots(ctx, params: ListSnapshotsParams) -> ActionResult:
    """List saved snapshots, newest first, optionally filtered by url/strategy."""
    rows = await st.list_snapshots(ctx, url=params.url, strategy=params.strategy, limit=params.limit)
    items = [
        SnapshotSummary(
            id=r.get("id", ""),
            url=r.get("url", ""),
            strategy=r.get("strategy", ""),
            performance_score=(r.get("scores") or {}).get("performance", 0.0),
            checked_at=r.get("checked_at", ""),
            has_field_data=bool(r.get("has_field_data")),
            status=str(r.get("status") or "completed"),
        )
        for r in rows
    ]
    if not items:
        return _error(
            "No checks yet. Tell me which site to check -- e.g. "
            "\"check climtec.md's speed\".", c.PSI_NO_RUNS,
        )
    return ActionResult.success(
        data=SnapshotList(id="snapshots", title="Page Speed Insights snapshots",
                           items=items, total=len(items)),
        summary=f"{len(items)} snapshot(s).",
    )


@chat.function(
    "get_speed_snapshot",
    description="Read one Page Speed Insights snapshot in full -- all metrics and opportunities.",
    action_type="read",
    chain_callable=True,
    data_model=SpeedSnapshot,
)
async def get_speed_snapshot(ctx, params: GetSnapshotParams) -> ActionResult:
    """Read one saved snapshot in full -- every metric and opportunity, not
    just the summary row list_speed_snapshots returns."""
    doc = await st.get_snapshot(ctx, params.snapshot_id)
    if not doc:
        return _error(f"Snapshot '{params.snapshot_id}' not found.", c.PSI_RUN_NOT_FOUND)
    return ActionResult.success(data=doc_to_snapshot(doc), summary="Snapshot loaded.")


@chat.function(
    "compare_speed_snapshots",
    description="Compare the two most recent Page Speed Insights snapshots for one url+strategy -- what improved, what regressed.",
    action_type="read",
    chain_callable=True,
    data_model=ComparisonResult,
)
async def compare_speed_snapshots(ctx, params: CompareSnapshotsParams) -> ActionResult:
    """Diff the two most recent snapshots for one url+strategy -- score and
    metric deltas, plus a regressed flag when any score dropped meaningfully."""
    full_url = normalize_url(params.url)
    pair = await st.latest_two(ctx, full_url, params.strategy)
    if len(pair) < 2:
        return _error(
            "Comparison needs at least two runs for this url+strategy. "
            "There are fewer right now -- run another check.", c.PSI_NO_RUNS,
        )
    current, previous = pair[0], pair[1]
    score_deltas = {
        k: round(current["scores"].get(k, 0.0) - previous["scores"].get(k, 0.0), 4)
        for k in set(current["scores"]) | set(previous["scores"])
    }
    prev_lab = {m["name"]: m["value"] for m in previous.get("lab_metrics") or []}
    curr_lab = {m["name"]: m["value"] for m in current.get("lab_metrics") or []}
    metric_deltas = {
        name: round(curr_lab[name] - prev_lab[name], 2)
        for name in curr_lab if name in prev_lab
    }
    regressed = any(v < -0.05 for v in score_deltas.values())
    result = ComparisonResult(
        id=f"{full_url}:{params.strategy}",
        title=f"Comparison -- {full_url} ({params.strategy})",
        url=full_url,
        strategy=params.strategy,
        previous_checked_at=previous.get("checked_at", ""),
        current_checked_at=current.get("checked_at", ""),
        score_deltas=score_deltas,
        metric_deltas=metric_deltas,
        regressed=regressed,
    )
    verdict = "regressed" if regressed else "no regression"
    return ActionResult.success(data=result, summary=f"Comparison ready: {verdict}.")


# ──────────────────────────────────────────────────────────────────────────
# Настройки (UI_INTERFACE_STANDARD.md: всё настраиваемое в одном месте)
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "save_speed_thresholds",
    description="Save custom good/needs-improvement/poor thresholds for LCP/CLS/INP. Defaults are Google's own official Core Web Vitals thresholds.",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_speed_thresholds",
    data_model=SettingsState,
)
async def save_speed_thresholds(ctx, params: SaveThresholdsParams) -> ActionResult:
    """Overwrite the good/needs-improvement/poor thresholds. Defaults are
    Google's own official Core Web Vitals thresholds, editable here, never
    silently hidden inside the code."""
    thresholds = params.model_dump()
    await st.save_settings(ctx, {"thresholds": thresholds})
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary="Core Web Vitals thresholds saved.",
        refresh_panels=["psi"],
    )


@chat.function(
    "save_speed_categories",
    description="Save which Lighthouse categories run by default (performance always included).",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_speed_categories",
    data_model=SettingsState,
)
async def save_speed_categories(ctx, params: SaveCategoryTogglesParams) -> ActionResult:
    """Save which Lighthouse categories run by default on every future check.
    'performance' is always kept even if omitted -- Core Web Vitals live there."""
    cats = list(dict.fromkeys(["performance"] + params.categories))
    await st.save_settings(ctx, {"default_categories": cats})
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary=f"Default categories saved: {', '.join(cats)}.",
        refresh_panels=["psi"],
    )


@chat.function(
    "save_speed_retention",
    description="Save how many days to keep raw Lighthouse snapshots before automatic cleanup.",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_speed_retention",
    data_model=SettingsState,
)
async def save_speed_retention(ctx, params: SaveRetentionParams) -> ActionResult:
    """Save the raw-snapshot retention window in days; the daily schedule
    tick purges anything older than this."""
    await st.save_settings(ctx, {"retention_days": params.retention_days})
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary=f"Snapshots will be kept for {params.retention_days} day(s).",
        refresh_panels=["psi"],
    )


@chat.function(
    "save_speed_notify_mode",
    description="Save when to notify: all runs, only regressions, or off.",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_speed_notify_mode",
    data_model=SettingsState,
)
async def save_speed_notify_mode(ctx, params: SaveNotifyModeParams) -> ActionResult:
    """Save when the scheduled auto-check should notify: every run, only
    regressions, or never."""
    await st.save_settings(ctx, {"notify_mode": params.notify_mode})
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary=f"Notification mode: {params.notify_mode}.",
        refresh_panels=["psi"],
    )


@chat.function(
    "save_speed_schedule",
    description="Turn on/off the daily automatic speed check and set its hour and site list (empty = every site in Sites Registry).",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_speed_schedule",
    data_model=SettingsState,
)
async def save_speed_schedule(ctx, params: SaveScheduleParams) -> ActionResult:
    """Turn the daily automatic check on/off, set its hour (UTC), and its
    explicit site list. Sites are the ONLY source the scheduler reads --
    an empty list means the scheduler skips, it never guesses a fallback."""
    sites = [s.strip() for s in params.sites.replace("\n", ",").split(",") if s.strip()]
    schedule = {"enabled": params.enabled, "hour": params.hour, "sites": sites}
    await st.save_settings(ctx, {"schedule": schedule})
    state_txt = "enabled" if params.enabled else "disabled"
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary=f"Automatic check {state_txt}, run hour {params.hour}:00 UTC.",
        refresh_panels=["psi"],
    )


@chat.function(
    "get_speed_settings",
    description="Read every configurable Page Speed Insights setting in one call -- key status, thresholds, categories, retention, notify mode, schedule.",
    action_type="read",
    chain_callable=True,
    data_model=SettingsState,
)
async def get_speed_settings(ctx, params: GetScheduleParams) -> ActionResult:
    """Read the whole App settings screen in one call: key status, thresholds,
    default categories, retention, notify mode, schedule."""
    state = await build_settings_state(ctx)
    return ActionResult.success(data=state, summary="Settings loaded.")
