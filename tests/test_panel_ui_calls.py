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
async def test_nav_panel_renders_without_raising():
    from imperal_sdk.testing import MockContext, MockSecretStore

    import panels

    ctx = MockContext()
    ctx.secrets = MockSecretStore({})

    node = await panels.psi_nav_panel(ctx)
    assert node is not None
