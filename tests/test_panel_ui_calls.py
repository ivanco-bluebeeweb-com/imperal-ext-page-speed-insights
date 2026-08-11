"""Структурная защита панели: каждый вызов ui.* обязан совпадать с реальной
сигнатурой SDK.

ПОЧЕМУ ЭТОТ ТЕСТ СУЩЕСТВУЕТ (реальный инцидент 11.08.2026). Экран
"App settings" вызывал `ui.MultiSelect(..., value=default_categories, ...)`,
но настоящая сигнатура (imperal_sdk/ui/input_components.py) знает только
`values=` -- `value` там не существует. `imperal validate` это не поймал (он
проверяет контракт расширения, а не то, что вызовы ui.* внутри panels*.py
соответствуют сигнатурам SDK), 22 существующих теста тоже прошли (ни один не
рендерил именно settings_view с реальными аргументами) -- баг дошёл до
пользователя как ПУСТОЙ ЭКРАН при клике на "App settings" в живой панели.

Тот же паттерн уже есть у SEO Audit Engine
(tests/test_panels.py::test_every_ui_call_matches_the_sdk_signature) -- здесь
он адаптирован под то, что у Page Speed Insights экраны разнесены по ДВУМ
файлам (panels.py -- нав + диспетчер, panels_views.py -- сами экраны), а не
живут в одном panels.py.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re

import pytest

from imperal_sdk import ui

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _ui_call_problems(path: pathlib.Path) -> list[str]:
    problems: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "ui"):
            continue

        fn = getattr(ui, node.func.attr, None)
        if fn is None:
            problems.append(f"{path.name}:{node.lineno}: ui.{node.func.attr} не существует")
            continue
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            continue
        if any(p.kind == p.VAR_KEYWORD for p in params.values()):
            continue
        for kw in node.keywords:
            if kw.arg and kw.arg not in params:
                problems.append(
                    f"{path.name}:{node.lineno}: ui.{node.func.attr}(...{kw.arg}=...) "
                    f"-- такого аргумента нет; реальные параметры: "
                    f"{', '.join(params)}"
                )
    return problems


@pytest.mark.parametrize("filename", ["panels.py", "panels_views.py"])
def test_every_ui_call_matches_the_sdk_signature(filename: str):
    problems = _ui_call_problems(_ROOT / filename)
    assert not problems, "вызовы ui.* расходятся с SDK:\n  " + "\n  ".join(problems)


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["", "settings", "snapshot", "compare"])
async def test_every_center_view_renders_without_raising(view: str):
    """Живой рендер, не только статический анализ сигнатур.

    Статическая проверка выше ловит "аргумента не существует", но не ловит
    ошибки, видимые только при исполнении (например, обращение к полю,
    которого нет в данных). Именно так реальный баг с MultiSelect дошёл бы
    и до этого теста, если бы кто-то передал `value=None` вместо списка --
    поэтому рендерим каждый экран по-настоящему, с реалистичными параметрами.
    """
    from imperal_sdk.testing import MockContext, MockSecretStore

    import panels

    ctx = MockContext()
    ctx.secrets = MockSecretStore({})

    node = await panels.psi_panel(
        ctx, view=view, snapshot_id="missing-id", url="climtec.md", strategy="mobile",
    )
    assert node is not None


@pytest.mark.asyncio
async def test_nav_panel_renders_without_raising_before_connect():
    """Connect-first (тот же паттерн, что Aidentika Connector, 11.08.2026):
    пока ключ не подключён, сайдбар обязан рендериться и показать РОВНО
    карточку подключения -- не форму проверки, не историю."""
    from imperal_sdk.testing import MockContext, MockSecretStore

    import panels

    ctx = MockContext()
    ctx.secrets = MockSecretStore({})

    node = await panels.psi_nav_panel(ctx)
    assert node is not None
    dumped = str(node)
    assert "connect_pagespeed" in dumped
    assert "check_site_speed" not in dumped  # форма проверки ещё не должна показываться


@pytest.mark.asyncio
async def test_nav_panel_renders_without_raising_after_connect():
    """После подключения -- форма проверки и история должны появиться."""
    from imperal_sdk.testing import MockContext, MockSecretStore

    import panels

    import storage as st

    ctx = MockContext()
    ctx.secrets = MockSecretStore({"pagespeed_api_key": "fake-key-for-test"})
    snapshot_id = await st.save_snapshot(ctx, {
        "url": "https://example.com",
        "strategy": "mobile",
        "checked_at": "2026-08-11T16:30:00Z",
        "scores": {"performance": 0.82},
    })

    node = await panels.psi_nav_panel(ctx)
    assert node is not None
    dumped = str(node)
    assert "check_site_speed" in dumped
    assert "View latest analysis" in dumped
    assert "View details" in dumped
    assert snapshot_id in dumped
    assert dumped.count("view': 'snapshot'") >= 2  # latest shortcut + per-result button


@pytest.mark.asyncio
async def test_nav_panel_keeps_visible_fallback_when_secret_service_fails(monkeypatch):
    """Initial sidebar hydration must never disappear if Vault is temporarily unavailable."""
    from imperal_sdk.testing import MockContext, MockSecretStore
    import panels

    async def unavailable_key(_ctx):
        raise RuntimeError("simulated Vault outage")

    monkeypatch.setattr(panels, "get_api_key", unavailable_key)
    ctx = MockContext()
    ctx.secrets = MockSecretStore({})

    node = await panels.psi_nav_panel(ctx, host_navigation_state="initial")
    dumped = str(node)
    assert "Page Speed Insights could not load" in dumped
    assert "saved-key service did not respond" in dumped


@pytest.mark.asyncio
async def test_nav_panel_keeps_visible_fallback_when_history_store_fails(monkeypatch):
    """A history read failure must keep the connected sidebar visible and usable."""
    from imperal_sdk.testing import MockContext, MockSecretStore
    import panels

    async def unavailable_history(_ctx, *, limit):
        raise RuntimeError("simulated store outage")

    monkeypatch.setattr(panels.st, "list_snapshots", unavailable_history)
    ctx = MockContext()
    ctx.secrets = MockSecretStore({"pagespeed_api_key": "test-key"})

    node = await panels.psi_nav_panel(ctx, host_navigation_state="initial")
    dumped = str(node)
    assert "Page Speed Insights" in dumped
    assert "Check site speed" in dumped
    assert "Check history could not load" in dumped


@pytest.mark.asyncio
async def test_every_panel_accepts_arbitrary_platform_kwargs():
    """ПОЧЕМУ ЭТОТ ТЕСТ СУЩЕСТВУЕТ (реальный инцидент 11.08.2026, репортован
    Владом как "пустой сайдбар мигает на долю секунды и пропадает").

    Root cause: SDK's @ext.panel decorator wraps every handler as
    `async def wrapper(ctx, **params): result = await func(ctx, **params)`
    (imperal_sdk/extension.py) -- the platform CAN and DOES call any panel
    with extra keyword params (navigation state, view args, etc). `psi_nav_panel`
    was declared as `async def psi_nav_panel(ctx) -> ui.UINode` -- no **kwargs.
    The FIRST safe/param-less render (skeleton) showed fine, but the real
    render with platform params raised TypeError, and the client silently
    fell back to the generic Imperal Cloud welcome screen -- exactly what
    the screenshot showed. Every other left-nav panel in this repo's sibling
    apps (Aidentika Connector, Asana, Notion, SEO Audit Engine, Slack,
    Trello -- checked directly) already takes **kwargs; this one didn't.

    This test calls EVERY @ext.panel handler with an arbitrary extra kwarg
    and fails loudly if any of them doesn't accept it.
    """
    from imperal_sdk.testing import MockContext, MockSecretStore
    import panels

    ctx = MockContext()
    ctx.secrets = MockSecretStore({})

    # Call the SOURCE functions, not app.ext.tools[...].func: the latter is
    # the SDK wrapper, which always accepts **params and would mask the bug.
    panel_handlers = {
        "psi_nav_panel": panels.psi_nav_panel,
        "psi_panel": panels.psi_panel,
    }
    for name, handler in panel_handlers.items():
        try:
            await handler(ctx, some_platform_param="x")
        except TypeError as exc:
            pytest.fail(
                f"{name} does not accept arbitrary platform kwargs -- "
                f"the platform CAN call any panel with extra params, and this "
                f"crash makes the sidebar disappear after its first skeleton "
                f"render: {exc}"
            )


# --- события, которые ДОЛЖНЫ включать автообновление сайдбара --------------

def test_sidebar_refresh_events_match_emitted_handler_events():
    """ПОЧЕМУ ЭТОТ ТЕСТ СУЩЕСТВУЕТ (реальный инцидент 11.08.2026): после
    успешного connect_pagespeed контент в панели не обновлялся сам. Причина:
    handlers.py эмитил `event="page-speed-insights.connect"` (короткое имя),
    а `psi_nav`'s `refresh="on_event:...` слушал `page-speed-insights.\n    connect_pagespeed` (полное имя функции) -- имена никогда не совпадали, и
    подписка не срабатывала ни разу. То же было с disconnect/check.

    Тест жёстко фиксирует: каждое событие, объявленное в on_event-списке
    `psi_nav`, ДОЛЖНО реально эмититься каким-то chat.function в handlers.py
    -- иначе подписка на автообновление тихо мертва.
    """
    handlers_src = (_ROOT / "handlers.py").read_text(encoding="utf-8")
    panels_src = (_ROOT / "panels.py").read_text(encoding="utf-8")

    emitted = set(re.findall(r'event="(page-speed-insights\.[a-z_]+)"', handlers_src))
    assert emitted, "не нашла ни одного event= в handlers.py -- проверь регекс/файл"

    # refresh может быть разбит на несколько строковых литералов подряд —
    # склеиваем через AST (Python конкатенирует смежные строковые константы
    # в один Constant при парсинге), а не хрупкий regex по кавычкам.
    tree = ast.parse(panels_src)
    refresh_value = None
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "refresh":
            refresh_value = ast.literal_eval(node.value) if isinstance(node.value, (ast.Constant, ast.JoinedStr)) else None
            if refresh_value is None and isinstance(node.value, ast.BinOp):
                # конкатенация строковых литералов через + не ожидается тут,
                # но на случай смежных строковых констант ast уже склеил их
                # в один Constant при парсинге -- fallback не нужен.
                pass
            if isinstance(refresh_value, str) and refresh_value.startswith("on_event:"):
                break
            refresh_value = None

    assert refresh_value, "не нашла refresh=\"on_event:...\" у панели psi_nav"
    subscribed = set(refresh_value.removeprefix("on_event:").split(","))

    missing = subscribed - emitted
    assert not missing, (
        f"psi_nav подписан на события, которые НИКТО не эмитит: {missing} -- "
        f"сайдбар никогда не обновится сам после этих действий. "
        f"Реально эмитируемые события: {sorted(emitted)}"
    )
