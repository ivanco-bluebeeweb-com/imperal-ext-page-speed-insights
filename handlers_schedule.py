"""Ежедневная автопроверка -- будильник, а не сам прогон.

Тот же принцип, что seo_auto_audit у SEO Audit Engine: `@ext.schedule`
фиксируется на момент выкладки приложения, поэтому расписание внутри
хранилища (settings.schedule) -- час запуска настраивается человеком в любой
момент без передеплоя, а cron просто спрашивает "уже пора?" каждый час.
"""

from __future__ import annotations

import time

from imperal_sdk import ActionResult

import psi_client as psi
import storage as st
from app import ext
from core import fetch_connected_sites, run_and_save
from models import DEFAULT_THRESHOLDS

TICK_CRON = "10 * * * *"


def _now_parts(ts: float | None = None) -> tuple[str, int]:
    t = time.gmtime(ts if ts is not None else time.time())
    return time.strftime("%Y-%m-%d", t), t.tm_hour


async def _due(ctx) -> tuple[bool, dict]:
    settings = await st.get_settings(ctx)
    schedule = settings.get("schedule") or {}
    if not schedule.get("enabled"):
        return False, schedule
    today, hour = _now_parts()
    want = int(schedule.get("hour", 3))
    if hour < want:
        return False, schedule
    if str(schedule.get("last_run_date") or "") == today:
        return False, schedule
    return True, schedule


async def _sites_to_check(ctx, schedule: dict) -> list[str]:
    """Явный список из настроек -- единственный источник.

    ПОЧЕМУ НЕ ПАДАЕМ НА Sites Registry, ЕСЛИ ПУСТО (важное отличие от
    первого черновика этого модуля). `ctx.extensions.call` достаёт только до
    `@ext.expose`-поверхностей другого приложения, никогда до его
    `@chat.function` (подтверждено комментарием в Sites Registry
    handlers.py). Sites Registry сегодня экспонирует только `ping` и
    `upsert_site` -- никакого read-only `list_sites` IPC там нет. Тихо звать
    несуществующую IPC-поверхность и получать пустой список -- то самое
    "выдуманная возможность", которую нельзя допускать. Поэтому: пусто в
    настройках значит пусто, с явным логом, а не тихая деградация до
    несуществующего источника.
    """
    return list(schedule.get("sites") or [])


@ext.schedule("psi_connected_sites_refresh", TICK_CRON)
async def psi_connected_sites_refresh(ctx) -> None:
    """Keeps the sidebar's connected-sites cache warm with NO button and NO
    chat message -- the whole point of this tick.

    WHY A SEPARATE SCHEDULE INSTEAD OF A BUTTON. `ctx.extensions.call` made
    from inside a *panel render* has been observed to reach the target
    extension with an empty user context (kernel-side gap, see
    storage.py's cache comment) -- so the sidebar can never refresh itself
    live. A real `@ext.schedule` tick is, like `list_connected_sites`
    itself, a normal call path with a populated context, so it is the
    correct place to do this automatically instead of asking a human to
    click Refresh every time a site gets connected or disconnected
    elsewhere.
    """
    sites, problems = await fetch_connected_sites(ctx)
    await st.cache_connected_sites(ctx, sites, problems)


@ext.schedule("psi_auto_check", TICK_CRON)
async def psi_auto_check(ctx) -> None:
    """Будильник: спрашивает "уже пора?" и обычно тихо уходит.

    Отметка last_run_date ставится ДО прогона -- по той же причине, что и у
    SEO Audit Engine: упавшая проверка не должна повторяться на каждом тике.
    """
    due, schedule = await _due(ctx)
    if not due:
        return

    sites = await _sites_to_check(ctx, schedule)
    if not sites:
        await ctx.log("psi_auto_check skipped: no sites known", "info")
        return

    today, _hour = _now_parts()
    schedule = dict(schedule)
    schedule["last_run_date"] = today
    await st.save_settings(ctx, {"schedule": schedule})

    settings = await st.get_settings(ctx)
    categories = settings.get("default_categories") or ["performance"]
    notify_mode = settings.get("notify_mode", "regressions")
    thresholds = settings.get("thresholds") or DEFAULT_THRESHOLDS

    regressions: list[str] = []
    failures: list[str] = []
    checked = 0

    for domain in sites:
        for strategy in ("mobile",):  # автопрогон по умолчанию мобильный -- он почти всегда хуже
            try:
                doc = await run_and_save(ctx, domain, strategy, categories)
                checked += 1
                pair = await st.latest_two(ctx, doc["url"], strategy)
                if len(pair) == 2:
                    prev_perf = (pair[1].get("scores") or {}).get("performance")
                    curr_perf = (pair[0].get("scores") or {}).get("performance")
                    if prev_perf is not None and curr_perf is not None and curr_perf < prev_perf - 0.05:
                        regressions.append(f"{domain} ({strategy}): {round(prev_perf*100)} -> {round(curr_perf*100)}")
            except psi.ProviderError as exc:
                failures.append(f"{domain}: {exc}")
                await ctx.log(f"psi_auto_check failed for {domain}: {exc.code}: {exc}", "warning")

    if notify_mode == "off":
        return
    if notify_mode == "regressions" and not regressions and not failures:
        return

    lines = [f"Page Speed Insights auto-check: {checked} check(s) done."]
    if regressions:
        lines.append("Speed regression: " + "; ".join(regressions))
    if failures:
        lines.append("Failed to check: " + "; ".join(failures))
    await ctx.deliver_chat_message("\n".join(lines), msg_type="system")
