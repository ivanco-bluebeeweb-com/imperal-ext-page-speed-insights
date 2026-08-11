"""Хелперы, общие для нескольких слоёв.

Тот же принцип, что shared.py у SEO Audit Engine: `code` -- обязательный
позиционный аргумент у `error()`, чтобы валидатор V32 (ищет буквальные
`ActionResult.error(`) видел структурный код на каждом пути ошибки.
"""

from __future__ import annotations

from imperal_sdk import ActionResult


def error(message: str, code: str, retryable: bool = False) -> ActionResult:
    return ActionResult.error(message, retryable, code=code)


def categorize(metric_name: str, value: float, thresholds: dict) -> str:
    """good | needs-improvement | poor по официальным порогам Google (или
    сохранённым пользовательским override -- всегда явно видимым в
    настройках, не зашитым только в код, см. models.DEFAULT_THRESHOLDS)."""
    name = metric_name.upper()
    if name == "LCP":
        good, poor = thresholds.get("lcp_good_ms", 2500), thresholds.get("lcp_poor_ms", 4000)
    elif name == "CLS":
        good, poor = thresholds.get("cls_good", 0.1), thresholds.get("cls_poor", 0.25)
    elif name == "INP":
        good, poor = thresholds.get("inp_good_ms", 200), thresholds.get("inp_poor_ms", 500)
    else:
        return "unknown"
    if value <= good:
        return "good"
    if value <= poor:
        return "needs-improvement"
    return "poor"
