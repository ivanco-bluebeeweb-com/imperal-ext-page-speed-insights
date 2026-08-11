"""Параметры инструментов и сущности результата.

Официальные пороги "good/needs improvement/poor" -- по решению владельца
продукта используются пороги Google, не свои придуманные (см.
PREPARATION.md, раздел 12): LCP <=2.5s good / <=4s needs improvement / >4s
poor; CLS <=0.1 / <=0.25 / >0.25; INP <=200ms / <=500ms / >500ms.
Задаются как настройка (видны и подтверждаемы в UI), но со ЭТИМИ значениями
по умолчанию -- не зашиты в код без возможности увидеть.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from imperal_sdk import sdl

STRATEGIES = ("mobile", "desktop")
CATEGORIES = ("performance", "accessibility", "best-practices", "seo", "pwa")
NOTIFY_MODES = ("all", "regressions", "off")

# Официальные пороги Google Core Web Vitals (мс для LCP/INP, безразмерно
# для CLS) -- источник: web.dev/articles/lcp, web.dev/articles/cls,
# web.dev/articles/inp. "poor" начинается СТРОГО ПОСЛЕ needs_improvement.
DEFAULT_THRESHOLDS = {
    "lcp_good_ms": 2500,
    "lcp_poor_ms": 4000,
    "cls_good": 0.1,
    "cls_poor": 0.25,
    "inp_good_ms": 200,
    "inp_poor_ms": 500,
}


def _check_strategy(v: str) -> str:
    low = (v or "").strip().lower()
    if low and low not in STRATEGIES:
        raise ValueError(f"неизвестная стратегия '{v}'. Допустимо: {', '.join(STRATEGIES)}")
    return low or "mobile"


def _check_categories(values: list[str]) -> list[str]:
    if not values:
        return ["performance"]
    bad = [v for v in values if v.lower() not in CATEGORIES]
    if bad:
        raise ValueError(
            f"неизвестные категории {bad}. Допустимо: {', '.join(CATEGORIES)}"
        )
    return [v.lower() for v in values]


# --------------------------- параметры чат-функций ---------------------------

class CheckSiteSpeedParams(BaseModel):
    """Что и как проверять."""

    url: str = Field(..., description="Полный URL или домен страницы для проверки")
    strategy: str = Field(
        "mobile",
        description="Стратегия анализа: mobile или desktop. Пусто -> mobile.",
    )
    categories: list[str] = Field(
        default_factory=lambda: ["performance"],
        description="Категории Lighthouse: performance, accessibility, best-practices, seo, pwa",
    )

    @field_validator("strategy")
    @classmethod
    def _v_strategy(cls, v):
        return _check_strategy(v)

    @field_validator("categories")
    @classmethod
    def _v_categories(cls, v):
        return _check_categories(v)


class ListSnapshotsParams(BaseModel):
    url: str = Field("", description="Фильтр по URL/домену. Пусто -- все")
    strategy: str = Field("", description="Фильтр по стратегии. Пусто -- все")
    limit: int = Field(20, description="Сколько снимков вернуть", ge=1, le=100)


class GetSnapshotParams(BaseModel):
    snapshot_id: str = Field(..., description="ID снимка из list_speed_snapshots")


class CompareSnapshotsParams(BaseModel):
    url: str = Field(..., description="URL/домен, для которого сравнить два прогона")
    strategy: str = Field("mobile", description="Стратегия анализа: mobile или desktop")

    @field_validator("strategy")
    @classmethod
    def _v_strategy(cls, v):
        return _check_strategy(v)


class ConnectPagespeedParams(BaseModel):
    api_key: str = Field(..., description="Google PageSpeed Insights API key для проверки и сохранения")


class NoParams(BaseModel):
    pass


class SaveThresholdsParams(BaseModel):
    lcp_good_ms: int = Field(2500, description="LCP порог good (мс)", ge=1)
    lcp_poor_ms: int = Field(4000, description="LCP порог poor (мс)", ge=1)
    cls_good: float = Field(0.1, description="CLS порог good", ge=0)
    cls_poor: float = Field(0.25, description="CLS порог poor", ge=0)
    inp_good_ms: int = Field(200, description="INP порог good (мс)", ge=1)
    inp_poor_ms: int = Field(500, description="INP порог poor (мс)", ge=1)


class SaveCategoryTogglesParams(BaseModel):
    categories: list[str] = Field(
        default_factory=lambda: ["performance"],
        description="Категории Lighthouse, включённые по умолчанию для проверок по расписанию",
    )

    @field_validator("categories")
    @classmethod
    def _v_categories(cls, v):
        return _check_categories(v)


class SaveRetentionParams(BaseModel):
    retention_days: int = Field(30, description="Сколько дней хранить сырой Lighthouse JSON", ge=1, le=365)


class SaveNotifyModeParams(BaseModel):
    notify_mode: str = Field("regressions", description="all | regressions | off")

    @field_validator("notify_mode")
    @classmethod
    def _v_mode(cls, v):
        low = (v or "").strip().lower()
        if low and low not in NOTIFY_MODES:
            raise ValueError(f"неизвестный режим '{v}'. Допустимо: {', '.join(NOTIFY_MODES)}")
        return low or "regressions"


class SaveScheduleParams(BaseModel):
    enabled: bool = Field(False, description="Включить автоматическую ежедневную проверку")
    hour: int = Field(3, description="Час запуска (0-23, часовой пояс сервера)", ge=0, le=23)
    sites: str = Field("", description="Сайты через запятую или с новой строки. Пусто -- все сайты из Sites Registry")


class GetScheduleParams(BaseModel):
    pass


# --------------------------- сущности результата ---------------------------

class MetricValue(BaseModel):
    """Одна метрика с числом, категорией (good/needs-improvement/poor) и
    источником. Обычная BaseModel, не sdl.Entity: живёт только вложенной в
    список внутри SpeedSnapshot, у неё нет собственной идентичности в UI --
    тот же принцип, что list[dict] у AuditComparison.fixed/appeared/remains
    в SEO Audit Engine."""

    name: str = ""            # LCP, CLS, INP, TTFB, FCP...
    value: float = 0.0
    unit: str = ""            # ms, unitless, score
    category: str = ""        # good | needs-improvement | poor | unknown
    source: str = ""          # field (CrUX) | lab (Lighthouse)


class Opportunity(BaseModel):
    """Одна рекомендация Lighthouse с потенциальной экономией времени.
    Тоже обычная BaseModel -- вложенный элемент списка, не отдельная SDL-
    сущность (см. пояснение у MetricValue)."""

    id: str = ""
    title: str = ""
    description: str = ""
    savings_ms: float = 0.0
    score: float = 1.0


class SpeedSnapshot(sdl.Entity):
    """Один снимок проверки: скоры, метрики, opportunities, timestamp."""

    url: str = ""
    strategy: str = ""
    categories: list[str] = []
    scores: dict[str, float] = {}       # {"performance": 0.87, ...}
    field_metrics: list[MetricValue] = []
    lab_metrics: list[MetricValue] = []
    has_field_data: bool = False
    opportunities: list[Opportunity] = []
    checked_at: str = ""
    raw_ref: str = ""                   # ссылка на сырой JSON (для retention)


class SnapshotSummary(BaseModel):
    """Краткая карточка снимка для списков -- без полного тела метрик.
    Обычная BaseModel: живёт вложенной в SnapshotList.items, сам список
    (SnapshotList) -- вот та сущность, что несёт SDL id/title."""

    id: str = ""
    url: str = ""
    strategy: str = ""
    performance_score: float = 0.0
    checked_at: str = ""
    has_field_data: bool = False


class SnapshotList(sdl.Entity):
    items: list[SnapshotSummary] = []
    total: int = 0


class ComparisonResult(sdl.Entity):
    """Разница между двумя последними снимками одного url+strategy."""

    url: str = ""
    strategy: str = ""
    previous_checked_at: str = ""
    current_checked_at: str = ""
    score_deltas: dict[str, float] = {}      # {"performance": -0.05, ...}
    metric_deltas: dict[str, float] = {}     # {"LCP": +350.0, ...}
    regressed: bool = False


class ScheduleState(BaseModel):
    """Вложенный value-object внутри SettingsState -- обычная BaseModel, не
    sdl.Entity: у sdl.Entity id/title обязательны при КАЖДОМ создании
    экземпляра (подтверждено чтением исходника imperal_sdk.sdl.Entity), а
    это не имеет смысла для настроечного under-объекта без собственной
    идентичности в списках/панелях."""

    enabled: bool = False
    hour: int = 3
    sites: list[str] = []
    last_run_date: str = ""


class ThresholdsState(BaseModel):
    lcp_good_ms: int = 2500
    lcp_poor_ms: int = 4000
    cls_good: float = 0.1
    cls_poor: float = 0.25
    inp_good_ms: int = 200
    inp_poor_ms: int = 500


class SettingsState(sdl.Entity):
    """Всё настраиваемое приложения -- одна сущность для одного экрана
    App settings (UI_INTERFACE_STANDARD.md, правило 3: всё в одном месте).

    ЭТО единственный sdl.Entity во всей группе настроек -- id/title
    передаются явно в handler-е (get_speed_settings), а не как
    class-level default, потому что общий мутируемый default-инстанс на
    уровне класса это ещё и классическая pydantic/python ловушка общего
    изменяемого состояния между вызовами."""

    key_connected: bool = False
    thresholds: ThresholdsState = Field(default_factory=ThresholdsState)
    default_categories: list[str] = Field(default_factory=lambda: ["performance"])
    retention_days: int = 30
    notify_mode: str = "regressions"
    schedule: ScheduleState = Field(default_factory=ScheduleState)
