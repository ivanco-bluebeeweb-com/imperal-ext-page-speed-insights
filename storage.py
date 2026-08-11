"""Хранилище: снимки проверок + настройки.

Одна коллекция снимков (`speed_snapshots`) плюс одна запись настроек
(`settings` collection, единственный документ с id="default") -- по тому же
паттерну, что SETTINGS_COLLECTION у SEO Audit Engine (schedule_settings.py):
то, что правит человек, живёт отдельно от того, что машина пишет при
каждом прогоне.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

SNAPSHOTS_COLLECTION = "speed_snapshots"
SETTINGS_COLLECTION = "settings"
SETTINGS_DOC_ID = "default"


def now_iso() -> str:
    """Микросекундная точность, а не только секунды.

    `latest_two` сортирует снимки по `checked_at`, чтобы понять, какой
    прогон новее. Секундной точности достаточно почти всегда, но не при
    двух проверках подряд в один и тот же URL+strategy в течение одной
    секунды -- тогда сортировка по равным строкам не может надёжно сказать,
    какая запись реально свежее, и сравнение может перепутать
    current/previous местами.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def save_snapshot(ctx, doc: dict) -> str:
    """Create one completed snapshot (used by scheduled/IPC callers)."""
    entity = await ctx.store.create(SNAPSHOTS_COLLECTION, doc)
    return entity.id


async def create_run(ctx, doc: dict) -> str:
    """Create a visible run before PageSpeed returns, with status=running."""
    entity = await ctx.store.create(SNAPSHOTS_COLLECTION, doc)
    return entity.id


async def update_run(ctx, run_id: str, patch: dict) -> dict:
    """Update the one existing run record in place as it completes or fails."""
    entity = await ctx.store.update(SNAPSHOTS_COLLECTION, run_id, patch)
    return entity.data | {"id": entity.id}


async def list_snapshots(ctx, *, url: str = "", strategy: str = "", limit: int = 20) -> list[dict]:
    page = await ctx.store.query(SNAPSHOTS_COLLECTION, order_by="-checked_at", limit=200)
    rows = [doc.data | {"id": doc.id} for doc in page.data]
    if url:
        norm = url.lower().strip()
        rows = [r for r in rows if norm in (r.get("url") or "").lower()]
    if strategy:
        rows = [r for r in rows if r.get("strategy") == strategy]
    return rows[:limit]


async def get_snapshot(ctx, snapshot_id: str) -> dict | None:
    doc = await ctx.store.get(SNAPSHOTS_COLLECTION, snapshot_id)
    if not doc:
        return None
    return doc.data | {"id": doc.id}


async def latest_two(ctx, url: str, strategy: str) -> list[dict]:
    """Последние два снимка для конкретного url+strategy, для сравнения."""
    rows = await list_snapshots(ctx, url=url, strategy=strategy, limit=200)
    exact = [r for r in rows if (r.get("url") or "").lower() == url.lower()]
    exact.sort(key=lambda r: r.get("checked_at") or "", reverse=True)
    return exact[:2]


async def purge_older_than(ctx, days: int) -> int:
    """Удаляет raw_ref-снимки старше N дней (retention). Возвращает
    количество удалённых -- вызывается из ежедневного расписания."""
    cutoff = time.time() - days * 86400
    page = await ctx.store.query(SNAPSHOTS_COLLECTION, limit=500)
    removed = 0
    for doc in page.data:
        checked_at = doc.data.get("checked_at") or ""
        try:
            ts = time.mktime(time.strptime(checked_at.split(".")[0] + "Z", "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            continue
        if ts < cutoff:
            await ctx.store.delete(SNAPSHOTS_COLLECTION, doc.id)
            removed += 1
    return removed


# --------------------------- настройки ---------------------------

async def get_settings(ctx) -> dict:
    """Единственный документ настроек. Пусто -- ещё не сохраняли ни разу,
    вызывающий код должен подставить свои DEFAULTS (не здесь -- у settings
    нет собственного понятия о том, что такое дефолт метрики/UI)."""
    try:
        doc = await ctx.store.get(SETTINGS_COLLECTION, SETTINGS_DOC_ID)
    except Exception:
        doc = None
    if not doc:
        return {}
    return dict(doc.data)


async def save_settings(ctx, patch: dict) -> dict:
    """Частичное обновление -- то, что не передано в `patch`, не трогаем.

    ``ctx.store.create`` не принимает doc_id как отдельный параметр (сигнатура
    ``create(collection, data)`` -- id генерируется сервером), поэтому, как и
    schedule_settings.py у SEO Audit Engine, сначала пробуем `update` по
    известному id, а если документа ещё нет (update бросает исключение) --
    создаём с id, вписанным прямо в сам payload.
    """
    current = await get_settings(ctx)
    merged = current | patch
    try:
        await ctx.store.update(SETTINGS_COLLECTION, SETTINGS_DOC_ID, merged)
    except Exception:
        await ctx.store.create(SETTINGS_COLLECTION, {"id": SETTINGS_DOC_ID, **merged})
    return merged
