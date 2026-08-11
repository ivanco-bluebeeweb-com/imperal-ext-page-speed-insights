"""Panel UI: left nav + ONE center panel, screens selected by `view`.

Тот же паттерн, что у Media Studio/Asana/Notion Connector: ровно один
владелец центрального слота (`psi`), экран выбирается параметром `view`,
чтобы два панели не спорили за slot="center" при обнаружении при старте
сессии.

    ui.Call("__panel__psi")                              -> список снимков
    ui.Call("__panel__psi", view="snapshot", snapshot_id=..) -> карточка снимка
    ui.Call("__panel__psi", view="compare", url=.., strategy=..) -> сравнение
    ui.Call("__panel__psi", view="settings")             -> App settings

CONNECT-FIRST (тот же паттерн, что Aidentika Connector, по прямой просьбе
Влада 11.08.2026 "сделай такой же интерфейс как в приложении айдентика"):
пока ключ не подключён, `psi_nav_panel` рендерит РОВНО ОДНУ вещь -- карточку
"Подключить Page Speed Insights" с формой api_key. Никакая форма проверки,
история или кнопка настроек не показываются раньше времени -- они всё равно
ничего не сделают без ключа. После успешного connect_pagespeed форма
подключения уходит НАВСЕГДА (по той же стоящей норме -- переставать
рендерить то, что больше не нужно), сайдбар показывает компактную карточку
"Подключено" + форму проверки + историю.

ОБЯЗАТЕЛЬНОЕ ПРАВИЛО (UI_INTERFACE_STANDARD.md, применено ДО кода в
PREPARATION.md раздел 11): РОВНО ОДНА кнопка "App settings" в левом
сайдбаре (`psi_nav`), рендерящая ВСЁ настраиваемое приложения одним
экраном в центральном слоте -- ключ, пороги, категории Lighthouse,
retention, notify mode и расписание, а не разбросанное по карточкам.

Экраны самого центрального панеля (`_snapshot_view`/`_compare_view`/
`_settings_view`) живут в panels_views.py отдельным модулем -- то же
разделение по объёму, что handlers.py/handlers_ipc.py/core.py.
"""

from __future__ import annotations

from imperal_sdk import ui

from app import ext
import storage as st
from core import get_api_key
from models import STRATEGIES
from panels_views import compare_view, settings_view, snapshot_view


def _score_pct(v: float) -> str:
    return f"{round(v * 100)}"


def _connect_card() -> ui.UINode:
    """До подключения ключа -- ровно ОДНО, что можно сделать (тот же паттерн,
    что у Aidentika: connect_aidentika_-форма первой и единственной вещью в
    сайдбаре, пока ключа нет -- никакой формы проверки/истории/настроек,
    которые всё равно ничего не сделают без ключа)."""
    return ui.Card(
        title="Connect Page Speed Insights",
        subtitle="Bring your own Google key -- checks run on your own quota",
        content=ui.Stack(direction="v", gap=2, children=[
            ui.Text(
                "Free key: console.cloud.google.com -> APIs & Services -> "
                "enable 'PageSpeed Insights API' -> Credentials -> Create API key. "
                "The key is verified before saving.",
                variant="caption",
            ),
            ui.Link(label="Open console.cloud.google.com",
                     href="https://console.cloud.google.com/apis/library/pagespeedonline.googleapis.com"),
            ui.Form(
                action="connect_pagespeed",
                submit_label="Verify and connect",
                children=[ui.Password(param_name="api_key", placeholder="Google API key")],
            ),
        ]),
    )


def _snapshot_row(s: dict) -> ui.ListItem:
    perf = (s.get("scores") or {}).get("performance")
    subtitle = s.get("strategy", "")
    if perf is not None:
        subtitle = f"{subtitle} · Performance {_score_pct(perf)}"
    return ui.ListItem(
        id=s.get("id", ""),
        title=s.get("url", "(no URL)"),
        subtitle=subtitle,
        meta=s.get("checked_at", ""),
        badge=ui.Badge(
            label="field data" if s.get("has_field_data") else "lab only",
            color="green" if s.get("has_field_data") else "gray",
        ),
        on_click=ui.Call("__panel__psi", view="snapshot", snapshot_id=s.get("id", "")),
    )


# ── Левый сайдбар: список снимков + ОДНА кнопка App settings ──────────────

@ext.panel(
    "psi_nav",
    slot="left",
    title="Page Speed Insights",
    icon="Gauge",
    default_width=320,
    min_width=260,
    max_width=460,
    refresh="on_event:page-speed-insights.check_site_speed,page-speed-insights.connect_pagespeed,"
            "page-speed-insights.disconnect_pagespeed",
)
async def psi_nav_panel(ctx, **kwargs) -> ui.UINode:
    key = await get_api_key(ctx)
    if not key:
        # До подключения -- ровно ОДНА карточка, ничего больше (тот же
        # принцип, что у Aidentika: не рендерить форму проверки/историю,
        # которые всё равно ничего не сделают без ключа).
        return ui.Stack(direction="v", gap=3, children=[
            _connect_card(),
            ui.Alert(
                title="Key not connected",
                message="Connect Google PageSpeed Insights to start running speed checks.",
                type="info",
            ),
        ])

    rows = await st.list_snapshots(ctx, limit=50)

    connected_card = ui.Card(
        title="Page Speed Insights",
        subtitle="Connected",
        content=ui.Stack(direction="v", gap=2, children=[
            ui.Text("Google key saved and verified.", variant="caption"),
            ui.Button(
                "App settings", icon="Settings", variant="secondary", size="sm",
                on_click=ui.Call("__panel__psi", view="settings"),
            ),
        ]),
    )

    check_form = ui.Card(
        title="Check site speed",
        content=ui.Form(
            action="check_site_speed",
            submit_label="Check",
            children=[
                ui.Input(param_name="url", placeholder="Domain or URL, e.g. g4s.md"),
                ui.Select(
                    param_name="strategy",
                    value="mobile",
                    options=[{"value": s, "label": s} for s in STRATEGIES],
                ),
            ],
        ),
    )

    items = [_snapshot_row(s) for s in rows]
    list_section = ui.Section(
        title=f"Check history ({len(items)})",
        children=[ui.List(items=items, searchable=True) if items
                  else ui.Text("No checks yet.", variant="caption")],
    )

    return ui.Stack(direction="v", gap=3, children=[
        connected_card,
        check_form,
        ui.Divider(),
        list_section,
    ])


# ── ОДИН центральный панель, переключаемый по view ──────────────────────

@ext.panel(
    "psi",
    slot="center",
    title="Page Speed Insights",
    center_overlay=True,
    refresh="manual",
)
async def psi_panel(ctx, **kwargs) -> ui.UINode:
    view = str(kwargs.get("view") or "").strip().lower()

    if view == "settings":
        return await settings_view(ctx)
    if view == "snapshot":
        return await snapshot_view(ctx, str(kwargs.get("snapshot_id") or ""))
    if view == "compare":
        return await compare_view(ctx, str(kwargs.get("url") or ""), str(kwargs.get("strategy") or ""))

    rows = await st.list_snapshots(ctx, limit=50)
    if not rows:
        return ui.Empty(message="No checks yet -- run your first one from the left sidebar.")
    return ui.Stack(direction="v", gap=3, children=[
        ui.Header(text="Check history", level=2),
        ui.List(items=[_snapshot_row(s) for s in rows], searchable=True),
    ])
