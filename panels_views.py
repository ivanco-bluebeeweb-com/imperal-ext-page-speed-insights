"""Экраны центрального панеля `psi` -- вынесены из panels.py отдельным
модулем по тому же принципу, что и handlers.py/handlers_ipc.py: держать
файлы читаемыми (~300 строк), а не потому что это разные слои UI.
"""

from __future__ import annotations

from imperal_sdk import ui

from models import CATEGORIES, DEFAULT_THRESHOLDS, NOTIFY_MODES
import storage as st


def _score_pct(v: float) -> str:
    return f"{round(v * 100)}"


def _metric_rows(metrics: list[dict]) -> ui.UINode:
    """ui.KeyValue принимает только items=[{key,value}] + columns (подтверждено
    чтением исходника imperal_sdk.ui.KeyValue) -- нет отдельного badge-слота,
    поэтому категория (good/needs-improvement/poor) идёт текстом в value."""
    items = [
        {
            "key": f"{m.get('name', '')} ({m.get('source', '')})",
            "value": f"{m.get('value', 0)} {m.get('unit', '')} -- {m.get('category', 'unknown')}",
        }
        for m in metrics
    ]
    return ui.KeyValue(items=items, columns=1)


async def snapshot_view(ctx, snapshot_id: str) -> ui.UINode:
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
                                    children=[_metric_rows(field)]))
    else:
        children.append(ui.Alert(
            title="Нет полевых данных",
            message="У этой страницы недостаточно трафика в CrUX для полевых метрик -- показан только лабораторный прогон.",
            type="info",
        ))

    children.append(ui.Section(title="Лабораторный прогон (Lighthouse)", children=[_metric_rows(lab)]))

    opps = s.get("opportunities") or []
    if opps:
        children.append(ui.Section(
            title="Что чинить",
            children=[ui.KeyValue(
                columns=1,
                items=[
                    {"key": o.get("title", ""), "value": f"~{round(o.get('savings_ms', 0))} мс"}
                    for o in opps
                ],
            )],
        ))

    children.append(ui.Row(gap=2, children=[
        ui.Button("Сравнить с прошлым разом", variant="secondary",
                  on_click=ui.Call("__panel__psi", view="compare",
                                   url=s.get("url", ""), strategy=s.get("strategy", ""))),
        ui.Button("Назад", variant="ghost", on_click=ui.Call("__panel__psi")),
    ]))

    return ui.Stack(direction="v", gap=3, children=children)


async def compare_view(ctx, url: str, strategy: str) -> ui.UINode:
    from handlers import compare_speed_snapshots
    from models import CompareSnapshotsParams

    result = await compare_speed_snapshots(ctx, CompareSnapshotsParams(url=url, strategy=strategy or "mobile"))
    if result.status != "success":
        return ui.Alert(title="Сравнение недоступно", message=result.error or "нужно хотя бы два снимка", type="warning")

    data = result.data
    kv_items = [
        {"key": f"Score: {name}", "value": f"{delta:+.2f}"}
        for name, delta in (data.score_deltas or {}).items()
    ] + [
        {"key": f"Метрика: {name}", "value": f"{delta:+.0f}"}
        for name, delta in (data.metric_deltas or {}).items()
    ]
    rows = [ui.KeyValue(columns=1, items=kv_items)] if kv_items else []

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


async def settings_view(ctx) -> ui.UINode:
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
