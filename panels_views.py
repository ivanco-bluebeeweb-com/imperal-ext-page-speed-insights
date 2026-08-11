"""Экраны центрального панеля `psi` -- вынесены из panels.py отдельным
модулем по тому же принципу, что и handlers.py/handlers_ipc.py: держать
файлы читаемыми (~300 строк), а не потому что это разные слои UI.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from imperal_sdk import ui

from models import CATEGORIES, DEFAULT_THRESHOLDS, NOTIFY_MODES
import storage as st


def _score_pct(v: float) -> str:
    return f"{round(v * 100)}"


def _site_name(url: str) -> str:
    """Turn a stored URL into a clean report label without inventing a brand."""
    host = (urlparse(url).hostname or url or "").removeprefix("www.")
    return " ".join(part.upper() if len(part) <= 3 else part.capitalize()
                    for part in host.replace("-", " ").replace(".", " ").split())


def _run_date_and_time(value: str) -> tuple[str, str]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d %b %Y"), parsed.strftime("%H:%M UTC")
    except (TypeError, ValueError):
        return value or "—", "—"


def _metric_cards(metrics: list[dict]) -> ui.UINode:
    return ui.Grid(
        columns=3,
        gap=2,
        children=[
            ui.Card(
                title=str(metric.get("name") or "Metric"),
                subtitle=str(metric.get("category") or "").replace("-", " ").title(),
                content=ui.Text(
                    f"{metric.get('value', 0)} {metric.get('unit', '')}".strip(),
                    variant="heading",
                ),
            )
            for metric in metrics
        ],
    )


async def snapshot_view(ctx, snapshot_id: str) -> ui.UINode:
    s = await st.get_snapshot(ctx, snapshot_id)
    if not s:
        return ui.Empty(message="This speed check was not found.")

    date_run, time_run = _run_date_and_time(str(s.get("checked_at") or ""))
    scores = s.get("scores") or {}
    field = s.get("field_metrics") or []
    lab = s.get("lab_metrics") or []
    metrics = lab or field

    children: list[ui.UINode] = [
        ui.Button(
            "Back to Page Speed Runs List", variant="ghost", icon="ArrowLeft",
            on_click=ui.Call("__panel__psi"),
        ),
        ui.Header(text=f"Detailed Speed Check for {_site_name(str(s.get('url') or ''))}", level=3),
        ui.KeyValue(columns=2, items=[
            {"key": "Device", "value": str(s.get("strategy") or "—").title()},
            {"key": "Date run", "value": date_run},
            {"key": "Time run", "value": time_run},
        ]),
    ]

    if scores:
        children.append(ui.Grid(
            columns=3,
            gap=2,
            children=[
                ui.Card(
                    title=name.capitalize(),
                    content=ui.Text(f"{_score_pct(value)}/100", variant="heading"),
                )
                for name, value in scores.items()
            ],
        ))

    if metrics:
        children.append(ui.Section(title="Metrics", children=[_metric_cards(metrics)]))

    opps = s.get("opportunities") or []
    if opps:
        children.append(ui.Section(
            title="What to fix",
            children=[ui.KeyValue(
                columns=1,
                items=[
                    {"key": o.get("title", ""), "value": f"~{round(o.get('savings_ms', 0))} ms"}
                    for o in opps
                ],
            )],
        ))

    return ui.Stack(direction="v", gap=3, children=children)


async def compare_view(ctx, url: str, strategy: str) -> ui.UINode:
    from handlers import compare_speed_snapshots
    from models import CompareSnapshotsParams

    result = await compare_speed_snapshots(ctx, CompareSnapshotsParams(url=url, strategy=strategy or "mobile"))
    if result.status != "success":
        return ui.Alert(title="Comparison unavailable", message=result.error or "need at least two snapshots", type="warning")

    data = result.data
    kv_items = [
        {"key": f"Score: {name}", "value": f"{delta:+.2f}"}
        for name, delta in (data.score_deltas or {}).items()
    ] + [
        {"key": f"Metric: {name}", "value": f"{delta:+.0f}"}
        for name, delta in (data.metric_deltas or {}).items()
    ]
    rows = [ui.KeyValue(columns=1, items=kv_items)] if kv_items else []

    return ui.Stack(direction="v", gap=3, children=[
        ui.Header(text=f"Comparison · {url}", level=3,
                  subtitle=f"{data.previous_checked_at} → {data.current_checked_at}"),
        ui.Alert(
            title="Regression" if data.regressed else "No regression",
            message="One or more metrics dropped past the threshold." if data.regressed
                     else "Metrics are stable or improved.",
            type="warning" if data.regressed else "success",
        ),
        ui.Stack(direction="v", gap=2, children=rows),
        ui.Button("Back", variant="ghost", on_click=ui.Call("__panel__psi")),
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
                  subtitle="Every Page Speed Insights setting in one place"),
    ]

    # 1. Key
    children.append(ui.Section(
        title="Google PageSpeed Insights API key",
        children=[
            ui.Alert(
                title="Key connected" if key_connected else "Key not configured",
                message=(
                    "Checks use your own daily Google quota."
                    if key_connected else
                    "Get a free key: console.cloud.google.com -> APIs & "
                    "Services -> enable 'PageSpeed Insights API' -> Credentials -> "
                    "Create API key."
                ),
                type="success" if key_connected else "warning",
            ),
            ui.Form(
                action="connect_pagespeed",
                submit_label="Verify and save",
                children=[ui.Password(param_name="api_key", placeholder="Google API key")],
            ),
        ] + ([ui.Button("Disconnect key", variant="danger", size="sm",
                        on_click=ui.Call("disconnect_pagespeed"))] if key_connected else []),
    ))

    # 2. Core Web Vitals thresholds
    t = thresholds
    children.append(ui.Section(
        title="Core Web Vitals thresholds (defaults -- Google's official thresholds)",
        children=[
            ui.Form(
                action="save_speed_thresholds",
                submit_label="Save thresholds",
                children=[
                    ui.Input(param_name="lcp_good_ms", placeholder="LCP good, ms",
                             value=str(t.lcp_good_ms if t else DEFAULT_THRESHOLDS["lcp_good_ms"])),
                    ui.Input(param_name="lcp_poor_ms", placeholder="LCP poor, ms",
                             value=str(t.lcp_poor_ms if t else DEFAULT_THRESHOLDS["lcp_poor_ms"])),
                    ui.Input(param_name="cls_good", placeholder="CLS good",
                             value=str(t.cls_good if t else DEFAULT_THRESHOLDS["cls_good"])),
                    ui.Input(param_name="cls_poor", placeholder="CLS poor",
                             value=str(t.cls_poor if t else DEFAULT_THRESHOLDS["cls_poor"])),
                    ui.Input(param_name="inp_good_ms", placeholder="INP good, ms",
                             value=str(t.inp_good_ms if t else DEFAULT_THRESHOLDS["inp_good_ms"])),
                    ui.Input(param_name="inp_poor_ms", placeholder="INP poor, ms",
                             value=str(t.inp_poor_ms if t else DEFAULT_THRESHOLDS["inp_poor_ms"])),
                ],
            ),
        ],
    ))

    # 3. Default Lighthouse categories
    children.append(ui.Section(
        title="Default Lighthouse categories (for automatic checks)",
        children=[
            ui.Form(
                action="save_speed_categories",
                submit_label="Save categories",
                children=[
                    ui.MultiSelect(
                        param_name="categories",
                        values=default_categories,
                        options=[{"value": c, "label": c} for c in CATEGORIES],
                    ),
                ],
            ),
        ],
    ))

    # 4. Retention
    children.append(ui.Section(
        title="Raw Lighthouse JSON retention",
        children=[
            ui.Form(
                action="save_speed_retention",
                submit_label="Save",
                children=[
                    ui.Slider(param_name="retention_days", min=1, max=365,
                              value=retention_days, label="Days to keep the raw response"),
                ],
            ),
        ],
    ))

    # 5. Notify mode
    children.append(ui.Section(
        title="Notifications",
        children=[
            ui.Form(
                action="save_speed_notify_mode",
                submit_label="Save",
                children=[
                    ui.Select(
                        param_name="notify_mode", value=notify_mode,
                        options=[{"value": m, "label": m} for m in NOTIFY_MODES],
                    ),
                ],
            ),
        ],
    ))

    # 6. Schedule
    sc = schedule
    children.append(ui.Section(
        title="Automatic daily check",
        children=[
            ui.Text(
                "The schedule is an alarm clock, not the run itself: the real "
                "hour is stored here and can change without redeploying the app.",
                variant="caption",
            ),
            ui.Form(
                action="save_speed_schedule",
                submit_label="Save schedule",
                children=[
                    ui.Toggle(label="Enable automatic check", param_name="enabled",
                              value=bool(sc.enabled) if sc else False),
                    ui.Slider(param_name="hour", min=0, max=23,
                              value=sc.hour if sc else 3, label="Run hour (UTC)"),
                    ui.TextArea(param_name="sites", placeholder="Comma-separated sites (required -- no auto-list)",
                                value=", ".join(sc.sites) if sc and sc.sites else ""),
                ],
            ),
        ],
    ))

    children.append(ui.Button("Back", variant="ghost", on_click=ui.Call("__panel__psi")))

    return ui.Stack(direction="v", gap=4, children=children)
