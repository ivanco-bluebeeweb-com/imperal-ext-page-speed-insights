"""Общее ядро, используемое ОБОИМИ входами -- chat-функциями (handlers.py) и
IPC-поверхностью (handlers_ipc.py) -- одна логика проверки, два вызывающих
пути, без дублирования.

Вынесено из handlers.py отдельным модулем, чтобы ни один файл не перевалил
за ~300 строк (PREPARATION.md / внутренний стандарт качества кода этого
репозитория), и чтобы handlers_schedule.py могло звать `_run_and_save`
напрямую без затягивания всех chat.function-регистраций через импорт
handlers.py.
"""

from __future__ import annotations

import codes as c
import psi_client as psi
import storage as st
from models import (
    DEFAULT_THRESHOLDS, MetricValue, Opportunity, ScheduleState,
    SettingsState, SpeedSnapshot, ThresholdsState,
)
from shared import categorize


def normalize_url(url: str) -> str:
    """Голый домен -> https://домен. Уже полный URL остаётся как есть."""
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"https://{u}"
    return u


async def get_api_key(ctx) -> str | None:
    return await ctx.secrets.get("pagespeed_api_key")


async def build_settings_state(ctx) -> SettingsState:
    """Собирает SettingsState из хранилища -- общее ядро для get_speed_settings
    и каждого save_speed_* хендлера, чтобы ответ записи и ответ чтения были
    ровно той же формой (narrator/audit ledger видят одну сущность, а не
    разные срезы одних и тех же данных)."""
    raw = await st.get_settings(ctx)
    key = await get_api_key(ctx)
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


async def run_and_save(ctx, url: str, strategy: str, categories: list[str]) -> dict:
    """Общее ядро: вызов Google + разбор + сохранение снимка.
    Используется и из chat-функции, и из IPC-поверхности -- одна логика,
    два входа."""
    key = await get_api_key(ctx)
    if not key:
        raise psi.ProviderError(
            "Ключ Google PageSpeed Insights не подключён. Подключи его через "
            "connect_pagespeed.", c.PSI_NO_KEY,
        )
    full_url = normalize_url(url)
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


def to_metric_values(raw: list[dict]) -> list[MetricValue]:
    return [MetricValue(**m) for m in raw]


def to_opportunities(raw: list[dict]) -> list[Opportunity]:
    return [Opportunity(**o) for o in raw]


def doc_to_snapshot(doc: dict) -> SpeedSnapshot:
    return SpeedSnapshot(
        id=doc.get("id", ""),
        title=f"{doc.get('url', '')} ({doc.get('strategy', '')})",
        url=doc.get("url", ""),
        strategy=doc.get("strategy", ""),
        categories=doc.get("categories") or [],
        scores=doc.get("scores") or {},
        field_metrics=to_metric_values(doc.get("field_metrics") or []),
        lab_metrics=to_metric_values(doc.get("lab_metrics") or []),
        has_field_data=bool(doc.get("has_field_data")),
        opportunities=to_opportunities(doc.get("opportunities") or []),
        checked_at=doc.get("checked_at", ""),
    )
