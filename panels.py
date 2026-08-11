"""Panel UI: left nav + ONE center panel, screens selected by `view`.

Тот же паттерн, что у Media Studio/Asana/Notion Connector: ровно один
владелец центрального слота (`psi`), экран выбирается параметром `view`,
чтобы два панели не спорили за slot="center" при обнаружении при старте
сессии.

    ui.Call("__panel__psi")                              -> список снимков
    ui.Call("__panel__psi", view="snapshot", snapshot_id=..) -> карточка снимка
    ui.Call("__panel__psi", view="compare", url=.., strategy=..) -> сравнение
    ui.Call("__panel__psi", view="settings")             -> App settings

ОБЯЗАТЕЛЬНОЕ ПРАВИЛО (UI_INTERFACE_STANDARD.md, применено ДО кода в
PREPARATION.md раздел 11): РОВНО ОДНА кнопка "App settings" в левом
сайдбаре (`psi_nav`), рендерящая ВСЁ настраиваемое приложения одним
экраном в центральном слоте -- ключ, пороги, категории Lighthouse,
retention, notify mode и расписание, а не разбросанное по карточкам.
"""

from __future__ import annotations

from imperal_sdk import ui

from app import ext
import storage as st
from models import CATEGORIES, DEFAULT_THRESHOLDS, NOTIFY_MODES, STRATEGIES

_CATEGORY_ROW = "Lighthouse категории"
_SCORE_COLOR = {"good": "green", "needs-improvement": "yellow", "poor": "red", "unknown": "gray"}


def _score_pct(v: float) -> str:
    return f"{round(v * 100)}"


def _snapshot_row(s: dict) -> ui.ListItem:
    perf = (s.get("scores") or {}).get("performance")
    subtitle = s.get("strategy", "")
    if perf is not None:
        subtitle = f"{subtitle} · Performance {_score_pct(perf)}"
    return ui.ListItem(
        id=s.get("id", ""),
        title=s.get("url", "(без URL)"),
        subtitle=subtitle,
        meta=s.get("checked_at", ""),
        badge=ui.Badge(
            label="полевые данные" if s.get("has_field_data") else "только lab",
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
async def psi_nav_panel(ctx) -> ui.UINode:
    rows = await st.list_snapshots(ctx, limit=50)

    check_form = ui.Card(
        title="Проверить скорость",
        content=ui.Form(
            action="check_site_speed",
            submit_label="Проверить",
            children=[
                ui.Input(param_name="url", placeholder="Домен или URL, например g4s.md"),
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
        title=f"История проверок ({len(items)})",
        children=[ui.List(items=items, searchable=True) if items
                  else ui.Text("Проверок ещё не было.", variant="caption")],
    )

    # РОВНО ОДНА secondary-кнопка "App settings" -- обязательное правило.
    settings_button = ui.Button(
        "App settings", icon="Settings", variant="secondary",
        on_click=ui.Call("__panel__psi", view="settings"),
    )

    return ui.Stack(direction="v", gap=3, children=[
        check_form,
        ui.Divider(),
        list_section,
        ui.Divider(),
        settings_button,
    ])


# ── Экран снимка ────────────────────────────────────────────────────────

def _metric_rows(metrics: list[dict]) -> list[ui.UINode]:
    out = []
    for m in metrics:
        out.append(ui.KeyValue(
            label=f"{m.get('name', '')} ({m.get('source', '')})",
            value=f"{m.get('value', 0)} {m.get('unit', '')}",
            badge=ui.Badge(label=m.get("category", "unknown"),
                           color=_SCORE_COLOR.get(m.get("category", "unknown"), "gray")),
        ))
    return out


async def _snapshot_view(ctx, snapshot_id: str) -> ui.UINode:
    s = await st.get_snapshot(ctx, snapshot_id)
    if not s:
        return ui.Empty(message="Этот снимок не найден -- возможно, его уже удалили при очистке retention.")

    scores_row = ui.Stack(direction="h", gap=3, children=[
        ui.Stat(label=name.capitalize(), value=_score_pct(v))
        for name, v in (s.get("scores") or {}).items()
    ])

    field = s.get("field_metrics") or []
    lab = s.get("lab_metrics") or []

    children: list[ui.UINode] = [
        ui.Header(text=s.get("url", ""), level=3,
                  subtitle=f"{s.get('strategy', '')} · {s.get('checked_at', '')}"),
        scores_row,
    ]

    if field:
        children.append(ui.Section(title="Полевые данные (реальные пользователи, CrUX)",
                                    children=_metric_rows(field)))
    else:
        children.append(ui.Alert(
            title="Нет полевых данных",
            message="У этой страницы недостаточно трафика в CrUX для полевых метрик -- показан только лабораторный прогон.",
            type="info",
        ))

    children.append(ui.Section(title="Лабораторный прогон (Lighthouse)", children=_metric_rows(lab)))

    opps = s.get("opportunities") or []
    if opps:
        children.append(ui.Section(
            title="Что чинить",
            children=[
                ui.KeyValue(
                    label=o.get("title", ""),
                    value=f"~{round(o.get('savings_ms', 0))} мс",
                )
                for o in opps
            ],
        ))

    children.append(ui.Row(gap=2, children=[
        ui.Button("Сравнить с прошлым разом", variant="secondary",
                  on_click=ui.Call("__panel__psi", view="compare",
                                   url=s.get("url", ""), strategy=s.get("strategy", ""))),
        ui.Button("Назад", variant="ghost", on_click=ui.Call("__panel__psi")),
    ]))

    return ui.Stack(direction="v", gap=3, children=children)


# ── Экран сравнения ─────────────────────────────────────────────────────

async def _compare_view(ctx, url: str, strategy: str) -> ui.UINode:
    from handlers import compare_speed_snapshots
    from models import CompareSnapshotsParams

    result = await compare_speed_snapshots(ctx, CompareSnapshotsParams(url=url, strategy=strategy or "mobile"))
    if result.status != "success":
        return ui.Alert(title="Сравнение недоступно", message=result.error or "нужно хотя бы два снимка", type="warning")

    data = result.data
    rows = [
        ui.KeyValue(label=f"Score: {name}", value=f"{delta:+.2f}")
        for name, delta in (data.score_deltas or {}).items()
    ] + [
        ui.KeyValue(label=f"Метрика: {name}", value=f"{delta:+.0f}")
        for name, delta in (data.metric_deltas or {}).items()
    ]

    return ui.Stack(direction="v", gap=3, children=[
        ui.Header(text=f"Сравнение · {url}", level=3,
                  subtitle=f"{data.previous_checked_at} → {data.current_checked_at}"),
        ui.Alert(
            title="Регресс" if data.regressed else "Без регресса",
            message="Что-то из метрик просело сильнее порога." if data.regressed
                     else "Показатели стабильны или улучшились.",
            type="warning" if data.regressed else "success",
        ),
        ui.Stack(direction="v", gap=2, children=rows),
        ui.Button("Назад", variant="ghost", on_click=ui.Call("__panel__psi")),
    ])


# ── Экран App settings (правило: ВСЁ настраиваемое в одном месте) ──────

async def _settings_view(ctx) -> ui.UINode:
    from handlers import get_speed_settings
    from models import GetScheduleParams

    result = await get_speed_settings(ctx, GetScheduleParams())
    state = result.data if result.status == "success" else None

    key_connected = bool(state.key_connected) if state else False
    thresholds = state.thresholds if state else None
    default_categories = state.default_categories if state else ["performance"]
    retention_days = state.retention_days if state else 30
    notify_mode = state.notify_mode if state else "regressions"
    schedule = state.schedule if state else None

    children: list[ui.UINode] = [
        ui.Header(text="App settings", level=2,
                  subtitle="Все настройки Page Speed Insights в одном месте"),
    ]

    # 1. Ключ
    children.append(ui.Section(
        title="Google PageSpeed Insights API key",
        children=[
            ui.Alert(
                title="Ключ подключён" if key_connected else "Ключ не настроен",
                message=(
                    "Проверки используют твой собственный дневной лимит Google."
                    if key_connected else
                    "Получить бесплатный ключ: console.cloud.google.com -> APIs & "
                    "Services -> включить 'PageSpeed Insights API' -> Credentials -> "
                    "Create API key."
                ),
                type="success" if key_connected else "warning",
            ),
            ui.Form(
                action="connect_pagespeed",
                submit_label="Проверить и сохранить",
                children=[ui.Password(param_name="api_key", placeholder="Google API key")],
            ),
        ] + ([ui.Button("Отключить ключ", variant="danger", size="sm",
                        on_click=ui.Call("disconnect_pagespeed"))] if key_connected else []),
    ))

    # 2. Пороги Core Web Vitals
    t = thresholds
    children.append(ui.Section(
        title="Пороги Core Web Vitals (по умолчанию -- официальные пороги Google)",
        children=[
            ui.Form(
                action="save_speed_thresholds",
                submit_label="Сохранить пороги",
                children=[
                    ui.Input(param_name="lcp_good_ms", placeholder="LCP good, мс",
                             value=str(t.lcp_good_ms if t else DEFAULT_THRESHOLDS["lcp_good_ms"])),
                    ui.Input(param_name="lcp_poor_ms", placeholder="LCP poor, мс",
                             value=str(t.lcp_poor_ms if t else DEFAULT_THRESHOLDS["lcp_poor_ms"])),
                    ui.Input(param_name="cls_good", placeholder="CLS good",
                             value=str(t.cls_good if t else DEFAULT_THRESHOLDS["cls_good"])),
                    ui.Input(param_name="cls_poor", placeholder="CLS poor",
                             value=str(t.cls_poor if t else DEFAULT_THRESHOLDS["cls_poor"])),
                    ui.Input(param_name="inp_good_ms", placeholder="INP good, мс",
                             value=str(t.inp_good_ms if t else DEFAULT_THRESHOLDS["inp_good_ms"])),
                    ui.Input(param_name="inp_poor_ms", placeholder="INP poor, мс",
                             value=str(t.inp_poor_ms if t else DEFAULT_THRESHOLDS["inp_poor_ms"])),
                ],
            ),
        ],
    ))

    # 3. Категории Lighthouse по умолчанию
    children.append(ui.Section(
        title="Категории Lighthouse по умолчанию (для автопроверок)",
        children=[
            ui.Form(
                action="save_speed_categories",
                submit_label="Сохранить категории",
                children=[
                    ui.MultiSelect(
                        param_name="categories",
                        value=default_categories,
                        options=[{"value": c, "label": c} for c in CATEGORIES],
                    ),
                ],
            ),
        ],
    ))

    # 4. Retention
    children.append(ui.Section(
        title="Хранение сырого Lighthouse JSON",
        children=[
            ui.Form(
                action="save_speed_retention",
                submit_label="Сохранить",
                children=[
                    ui.Slider(param_name="retention_days", min=1, max=365,
                              value=retention_days, label="Дней хранить сырой ответ"),
                ],
            ),
        ],
    ))

    # 5. Notify mode
    children.append(ui.Section(
        title="Уведомления",
        children=[
            ui.Form(
                action="save_speed_notify_mode",
                submit_label="Сохранить",
                children=[
                    ui.Select(
                        param_name="notify_mode", value=notify_mode,
                        options=[{"value": m, "label": m} for m in NOTIFY_MODES],
                    ),
                ],
            ),
        ],
    ))

    # 6. Расписание
    sc = schedule
    children.append(ui.Section(
        title="Автоматическая ежедневная проверка",
        children=[
            ui.Text(
                "Расписание -- будильник, а не сам прогон: реальный час "
                "хранится тут и меняется без выкладки приложения.",
                variant="caption",
            ),
            ui.Form(
                action="save_speed_schedule",
                submit_label="Сохранить расписание",
                children=[
                    ui.Toggle(label="Включить автопроверку", param_name="enabled",
                              value=bool(sc.enabled) if sc else False),
                    ui.Slider(param_name="hour", min=0, max=23,
                              value=sc.hour if sc else 3, label="Час запуска (UTC)"),
                    ui.TextArea(param_name="sites", placeholder="Сайты через запятую (обязательно -- нет автосписка)",
                                value=", ".join(sc.sites) if sc and sc.sites else ""),
                ],
            ),
        ],
    ))

    children.append(ui.Button("Назад", variant="ghost", on_click=ui.Call("__panel__psi")))

    return ui.Stack(direction="v", gap=4, children=children)


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
        return await _settings_view(ctx)
    if view == "snapshot":
        return await _snapshot_view(ctx, str(kwargs.get("snapshot_id") or ""))
    if view == "compare":
        return await _compare_view(ctx, str(kwargs.get("url") or ""), str(kwargs.get("strategy") or ""))

    rows = await st.list_snapshots(ctx, limit=50)
    if not rows:
        return ui.Empty(message="Проверок ещё не было -- запусти первую из левой панели.")
    return ui.Stack(direction="v", gap=3, children=[
        ui.Header(text="История проверок", level=2),
        ui.List(items=[_snapshot_row(s) for s in rows], searchable=True),
    ])
