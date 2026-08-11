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
from core import build_settings_state, doc_to_snapshot, normalize_url, run_and_save
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
        return _error("Дай, пожалуйста, реальный API-ключ.", c.PSI_NO_KEY)
    try:
        await psi.validate_api_key(ctx, key)
    except psi.ProviderError as exc:
        return _error(str(exc), exc.code, exc.retryable)
    await ctx.secrets.set("pagespeed_api_key", key)
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary="Ключ Google PageSpeed Insights подключён и проверен.",
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
        summary="Ключ отключён. Прежние снимки скорости остались доступны.",
        refresh_panels=["psi_nav", "psi"],
    )


# ──────────────────────────────────────────────────────────────────────────
# Основная проверка
# ──────────────────────────────────────────────────────────────────────────

@chat.function(
    "check_site_speed",
    description=(
        "Run a real Google PageSpeed Insights check for one URL: Core Web "
        "Vitals (LCP, CLS, INP) from real-user field data (if available) plus "
        "Lighthouse lab scores and top fix opportunities. Saves a timestamped "
        "snapshot for history/comparison."
    ),
    action_type="write",
    chain_callable=True,
    effects=["speed_snapshot.create"],
    event="page-speed-insights.check_site_speed",
    data_model=SpeedSnapshot,
)
async def check_site_speed(ctx, params: CheckSiteSpeedParams) -> ActionResult:
    """Run one real Google PageSpeed Insights check and persist it as a
    history snapshot. Raises no exception outward -- provider errors become
    a structured ActionResult.error via psi.ProviderError.code/.retryable."""
    try:
        doc = await run_and_save(ctx, params.url, params.strategy, params.categories)
    except psi.ProviderError as exc:
        return _error(str(exc), exc.code, exc.retryable)
    perf = doc["scores"].get("performance")
    perf_txt = f"{round(perf * 100)}/100" if perf is not None else "н/д"
    return ActionResult.success(
        data=doc_to_snapshot(doc),
        summary=f"Проверка {doc['url']} ({doc['strategy']}) готова: Performance {perf_txt}.",
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
        )
        for r in rows
    ]
    if not items:
        return _error(
            "Проверок пока не было. Скажи, какой сайт проверить -- например "
            "«проверь скорость climtec.md».", c.PSI_NO_RUNS,
        )
    return ActionResult.success(
        data=SnapshotList(id="snapshots", title="Снимки Page Speed Insights",
                           items=items, total=len(items)),
        summary=f"{len(items)} снимок(ов).",
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
        return _error(f"Снимок '{params.snapshot_id}' не найден.", c.PSI_RUN_NOT_FOUND)
    return ActionResult.success(data=doc_to_snapshot(doc), summary="Снимок загружен.")


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
            "Для сравнения нужно минимум два прогона для этого url+стратегии. "
            "Пока есть меньше -- запусти ещё одну проверку.", c.PSI_NO_RUNS,
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
        title=f"Сравнение -- {full_url} ({params.strategy})",
        url=full_url,
        strategy=params.strategy,
        previous_checked_at=previous.get("checked_at", ""),
        current_checked_at=current.get("checked_at", ""),
        score_deltas=score_deltas,
        metric_deltas=metric_deltas,
        regressed=regressed,
    )
    verdict = "стало хуже" if regressed else "без регресса"
    return ActionResult.success(data=result, summary=f"Сравнение готово: {verdict}.")


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
        summary="Пороги Core Web Vitals сохранены.",
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
        summary=f"Категории по умолчанию сохранены: {', '.join(cats)}.",
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
        summary=f"Снимки будут храниться {params.retention_days} дн.",
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
        summary=f"Режим уведомлений: {params.notify_mode}.",
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
    state_txt = "включена" if params.enabled else "выключена"
    return ActionResult.success(
        data=await build_settings_state(ctx),
        summary=f"Автопроверка {state_txt}, час запуска {params.hour}:00 UTC.",
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
    return ActionResult.success(data=state, summary="Настройки загружены.")
