"""Chat-функции + IPC-поверхность для других приложений.

СТРУКТУРА ФАЙЛА (одним модулем, как у Sites Registry -- приложение
небольшое, разделение на handlers_read/handlers_write было бы искусственным
на этом объёме).

ГЛАВНЫЙ IPC-КОНТРАКТ, вокруг которого построен весь план (PREPARATION.md,
раздел про интеграцию): `check_site_speed_ipc` -- @ext.expose, без чата,
однонаправленная зависимость (SEO Audit Engine узнаёт про это приложение,
это приложение НЕ знает про SEO Audit Engine), best-effort деградация на
стороне ВЫЗЫВАЮЩЕГО (тот же принцип, что list_connected_sites у Sites
Registry / WordPress Hub).
"""

from __future__ import annotations

from imperal_sdk import ActionResult

import codes as c
import psi_client as psi
import storage as st
from app import chat, ext
from models import (
    DEFAULT_THRESHOLDS, CheckSiteSpeedParams, ListSnapshotsParams,
    GetSnapshotParams, CompareSnapshotsParams, ConnectPagespeedParams,
    NoParams, SaveThresholdsParams, SaveCategoryTogglesParams,
    SaveRetentionParams, SaveNotifyModeParams, SaveScheduleParams,
    GetScheduleParams, SpeedSnapshot, MetricValue, Opportunity,
    SnapshotSummary, SnapshotList, ComparisonResult, SettingsState,
    ThresholdsState, ScheduleState,
)
from shared import error as _error, categorize


def _normalize_url(url: str) -> str:
    """Голый домен -> https://домен. Уже полный URL остаётся как есть."""
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"https://{u}"
    return u


async def _get_api_key(ctx) -> str | None:
    return await ctx.secrets.get("pagespeed_api_key")


async def _build_settings_state(ctx) -> SettingsState:
    """Собирает SettingsState из хранилища -- общее ядро для get_speed_settings
    и каждого save_speed_* хендлера, чтобы ответ записи и ответ чтения были
    ровно той же формой (narrator/audit ledger видят одну сущность, а не
    разные срезы одних и тех же данных)."""
    raw = await st.get_settings(ctx)
    key = await _get_api_key(ctx)
    thresholds = raw.get("thresholds") or DEFAULT_THRESHOLDS
    schedule = raw.get("schedule") or {}
    return SettingsState(
        id="settings",
        title="Page Speed Insights -- настройки",
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
    event="page-speed-insights.connect",
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
        data=await _build_settings_state(ctx),
        summary="Ключ Google PageSpeed Insights подключён и проверен.",
        refresh_panels=["psi_settings"],
    )


@chat.function(
    "disconnect_pagespeed",
    description="Disconnect Page Speed Insights: deletes the saved API key. Existing snapshots stay.",
    action_type="write",
    chain_callable=True,
    effects=["secret.delete"],
    event="page-speed-insights.disconnect",
    data_model=SettingsState,
)
async def disconnect_pagespeed(ctx, params: NoParams) -> ActionResult:
    """Delete the saved key. Past snapshots are untouched -- only future
    checks are blocked until a key is connected again."""
    await ctx.secrets.delete("pagespeed_api_key")
    return ActionResult.success(
        data=await _build_settings_state(ctx),
        summary="Ключ отключён. Прежние снимки скорости остались доступны.",
        refresh_panels=["psi_settings"],
    )


# ──────────────────────────────────────────────────────────────────────────
# Основная проверка
# ──────────────────────────────────────────────────────────────────────────

async def _run_and_save(ctx, url: str, strategy: str, categories: list[str]) -> dict:
    """Общее ядро: вызов Google + разбор + сохранение снимка.
    Используется и из chat-функции, и из IPC-поверхности -- одна логика,
    два входа."""
    key = await _get_api_key(ctx)
    if not key:
        raise psi.ProviderError(
            "Ключ Google PageSpeed Insights не подключён. Подключи его через "
            "connect_pagespeed.", c.PSI_NO_KEY,
        )
    full_url = _normalize_url(url)
    if not full_url:
        raise psi.ProviderError("Не указан URL для проверки.", c.PSI_NO_URL)

    payload = await psi.run_pagespeed(ctx, key, full_url, strategy=strategy, categories=categories)

    thresholds = (await st.get_settings(ctx)).get("thresholds") or DEFAULT_THRESHOLDS
    scores = psi.extract_scores(payload)
    lab_raw = psi.extract_lab_metrics(payload)
    field_raw, has_field_data = psi.extract_field_metrics(payload)
    for m in lab_raw + field_raw:
        m["category"] = categorize(m["name"], m["value"], thresholds)
    opportunities = psi.extract_opportunities(payload)

    doc = {
        "url": full_url,
        "strategy": strategy,
        "categories": categories,
        "scores": scores,
        "field_metrics": field_raw,
        "lab_metrics": lab_raw,
        "has_field_data": has_field_data,
        "opportunities": opportunities,
        "checked_at": st.now_iso(),
    }
    snapshot_id = await st.save_snapshot(ctx, doc)
    doc["id"] = snapshot_id
    return doc


def _to_metric_values(raw: list[dict]) -> list[MetricValue]:
    return [MetricValue(**m) for m in raw]


def _to_opportunities(raw: list[dict]) -> list[Opportunity]:
    return [Opportunity(**o) for o in raw]


def _doc_to_snapshot(doc: dict) -> SpeedSnapshot:
    return SpeedSnapshot(
        id=doc.get("id", ""),
        title=f"{doc.get('url', '')} ({doc.get('strategy', '')})",
        url=doc.get("url", ""),
        strategy=doc.get("strategy", ""),
        categories=doc.get("categories") or [],
        scores=doc.get("scores") or {},
        field_metrics=_to_metric_values(doc.get("field_metrics") or []),
        lab_metrics=_to_metric_values(doc.get("lab_metrics") or []),
        has_field_data=bool(doc.get("has_field_data")),
        opportunities=_to_opportunities(doc.get("opportunities") or []),
        checked_at=doc.get("checked_at", ""),
    )


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
    event="page-speed-insights.check",
    data_model=SpeedSnapshot,
)
async def check_site_speed(ctx, params: CheckSiteSpeedParams) -> ActionResult:
    """Run one real Google PageSpeed Insights check and persist it as a
    history snapshot. Raises no exception outward -- provider errors become
    a structured ActionResult.error via psi.ProviderError.code/.retryable."""
    try:
        doc = await _run_and_save(ctx, params.url, params.strategy, params.categories)
    except psi.ProviderError as exc:
        return _error(str(exc), exc.code, exc.retryable)
    perf = doc["scores"].get("performance")
    perf_txt = f"{round(perf * 100)}/100" if perf is not None else "н/д"
    return ActionResult.success(
        data=_doc_to_snapshot(doc),
        summary=f"Проверка {doc['url']} ({doc['strategy']}) готова: Performance {perf_txt}.",
        refresh_panels=["psi_snapshots"],
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
    return ActionResult.success(data=_doc_to_snapshot(doc), summary="Снимок загружен.")


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
    full_url = _normalize_url(params.url)
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
    event="page-speed-insights.save_thresholds",
    data_model=SettingsState,
)
async def save_speed_thresholds(ctx, params: SaveThresholdsParams) -> ActionResult:
    """Overwrite the good/needs-improvement/poor thresholds. Defaults are
    Google's own official Core Web Vitals thresholds, editable here, never
    silently hidden inside the code."""
    thresholds = params.model_dump()
    await st.save_settings(ctx, {"thresholds": thresholds})
    return ActionResult.success(
        data=await _build_settings_state(ctx),
        summary="Пороги Core Web Vitals сохранены.",
        refresh_panels=["psi_settings"],
    )


@chat.function(
    "save_speed_categories",
    description="Save which Lighthouse categories run by default (performance always included).",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_categories",
    data_model=SettingsState,
)
async def save_speed_categories(ctx, params: SaveCategoryTogglesParams) -> ActionResult:
    """Save which Lighthouse categories run by default on every future check.
    'performance' is always kept even if omitted -- Core Web Vitals live there."""
    cats = list(dict.fromkeys(["performance"] + params.categories))
    await st.save_settings(ctx, {"default_categories": cats})
    return ActionResult.success(
        data=await _build_settings_state(ctx),
        summary=f"Категории по умолчанию сохранены: {', '.join(cats)}.",
        refresh_panels=["psi_settings"],
    )


@chat.function(
    "save_speed_retention",
    description="Save how many days to keep raw Lighthouse snapshots before automatic cleanup.",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_retention",
    data_model=SettingsState,
)
async def save_speed_retention(ctx, params: SaveRetentionParams) -> ActionResult:
    """Save the raw-snapshot retention window in days; the daily schedule
    tick purges anything older than this."""
    await st.save_settings(ctx, {"retention_days": params.retention_days})
    return ActionResult.success(
        data=await _build_settings_state(ctx),
        summary=f"Снимки будут храниться {params.retention_days} дн.",
        refresh_panels=["psi_settings"],
    )


@chat.function(
    "save_speed_notify_mode",
    description="Save when to notify: all runs, only regressions, or off.",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_notify_mode",
    data_model=SettingsState,
)
async def save_speed_notify_mode(ctx, params: SaveNotifyModeParams) -> ActionResult:
    """Save when the scheduled auto-check should notify: every run, only
    regressions, or never."""
    await st.save_settings(ctx, {"notify_mode": params.notify_mode})
    return ActionResult.success(
        data=await _build_settings_state(ctx),
        summary=f"Режим уведомлений: {params.notify_mode}.",
        refresh_panels=["psi_settings"],
    )


@chat.function(
    "save_speed_schedule",
    description="Turn on/off the daily automatic speed check and set its hour and site list (empty = every site in Sites Registry).",
    action_type="write",
    chain_callable=True,
    effects=["settings.update"],
    event="page-speed-insights.save_schedule",
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
        data=await _build_settings_state(ctx),
        summary=f"Автопроверка {state_txt}, час запуска {params.hour}:00 UTC.",
        refresh_panels=["psi_settings"],
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
    state = await _build_settings_state(ctx)
    return ActionResult.success(data=state, summary="Настройки загружены.")


# ──────────────────────────────────────────────────────────────────────────
# IPC: другие приложения зовут это приложение без чата
# ──────────────────────────────────────────────────────────────────────────

@ext.expose("ping", action_type="read")
async def expose_ping(ctx, **kwargs) -> dict:
    """Read-only проверка присутствия -- по образцу Sites Registry: не
    трогает ctx.store, отвечает {"ok": True} для любого достижимого вызова.
    Позволяет вызывающему приложению (SEO Audit Engine) отличить
    "не установлено" от "установлено, но нет ключа" без реальной проверки."""
    return {"ok": True}


@ext.expose("check_site_speed_ipc", action_type="write")
async def expose_check_site_speed(ctx, url: str = "", strategy: str = "mobile",
                                    categories: list[str] | None = None, **kwargs) -> dict:
    """Прямой in-process IPC для других приложений (SEO Audit Engine и
    любой будущий потребитель) -- без чата. Возвращает
    {"ok": True, "scores": {...}, "field_metrics": [...], "lab_metrics": [...],
    "has_field_data": bool, "top_opportunities": [...]} при успехе, или
    {"ok": False, "error": "...", "retryable": bool} -- ВСЕГДА словарь, чтобы
    вызывающая сторона могла тихо деградировать (best-effort, как того
    требует контракт IPC в этом репозитории), не ловя исключение."""
    if not url:
        return {"ok": False, "error": "url is required", "retryable": False}
    try:
        doc = await _run_and_save(ctx, url, strategy, categories or ["performance"])
    except psi.ProviderError as exc:
        await ctx.log(f"check_site_speed_ipc failed for {url}: {exc.code}: {exc}", "warning")
        return {"ok": False, "error": str(exc), "retryable": exc.retryable}
    return {
        "ok": True,
        "url": doc["url"],
        "strategy": doc["strategy"],
        "scores": doc["scores"],
        "field_metrics": doc["field_metrics"],
        "lab_metrics": doc["lab_metrics"],
        "has_field_data": doc["has_field_data"],
        "top_opportunities": doc["opportunities"][:5],
        "checked_at": doc["checked_at"],
    }
