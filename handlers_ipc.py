"""IPC-поверхность для других приложений -- без чата.

ГЛАВНЫЙ IPC-КОНТРАКТ, вокруг которого построен весь план (PREPARATION.md,
раздел про интеграцию): `check_site_speed_ipc` -- @ext.expose, без чата,
однонаправленная зависимость (SEO Audit Engine узнаёт про это приложение,
это приложение НЕ знает про SEO Audit Engine), best-effort деградация на
стороне ВЫЗЫВАЮЩЕГО (тот же принцип, что list_connected_sites у Sites
Registry / WordPress Hub).

Отдельный файл от handlers.py: `ctx.extensions.call` реально достаёт только
@ext.expose-поверхности (подтверждено чтением платформы при работе над
handlers_schedule.py), поэтому граница чат/IPC -- это не стилистика, а
реальная граница контракта, и держать её отдельным маленьким файлом делает
эту границу видимой при чтении кода, а не спрятанной среди 12
chat-функций.
"""

from __future__ import annotations

import psi_client as psi
from app import ext
from core import run_and_save


@ext.expose("ping")
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
        doc = await run_and_save(ctx, url, strategy, categories or ["performance"])
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
