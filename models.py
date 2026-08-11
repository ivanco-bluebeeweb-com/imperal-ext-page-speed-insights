"""Tool parameters and result entities.

Official "good/needs improvement/poor" thresholds -- by product owner
decision we use Google's own thresholds, not invented ones (see
PREPARATION.md, section 12): LCP <=2.5s good / <=4s needs improvement / >4s
poor; CLS <=0.1 / <=0.25 / >0.25; INP <=200ms / <=500ms / >500ms.
Exposed as a setting (visible and confirmable in the UI), but WITH these
default values -- not hardcoded without any way to see them.
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
        raise ValueError(f"unknown strategy '{v}'. Allowed: {', '.join(STRATEGIES)}")
    return low or "mobile"


def _check_categories(values: list[str]) -> list[str]:
    if not values:
        return ["performance"]
    bad = [v for v in values if v.lower() not in CATEGORIES]
    if bad:
        raise ValueError(
            f"unknown categories {bad}. Allowed: {', '.join(CATEGORIES)}"
        )
    return [v.lower() for v in values]


# --------------------------- chat function parameters ---------------------------

class CheckSiteSpeedParams(BaseModel):
    """What and how to check."""

    url: str = Field(..., description="Full URL or domain of the page to check")
    strategy: str = Field(
        "mobile",
        description="Analysis strategy: mobile or desktop. Empty -> mobile.",
    )
    categories: list[str] = Field(
        default_factory=lambda: ["performance"],
        description="Lighthouse categories: performance, accessibility, best-practices, seo, pwa",
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
    url: str = Field("", description="Filter by URL/domain. Empty -- all")
    strategy: str = Field("", description="Filter by strategy. Empty -- all")
    limit: int = Field(20, description="How many snapshots to return", ge=1, le=100)


class GetSnapshotParams(BaseModel):
    snapshot_id: str = Field(..., description="Snapshot ID from list_speed_snapshots")


class CompareSnapshotsParams(BaseModel):
    url: str = Field(..., description="URL/domain to compare the two latest runs for")
    strategy: str = Field("mobile", description="Analysis strategy: mobile or desktop")

    @field_validator("strategy")
    @classmethod
    def _v_strategy(cls, v):
        return _check_strategy(v)


class ConnectPagespeedParams(BaseModel):
    api_key: str = Field(..., description="Google PageSpeed Insights API key to verify and save")


class NoParams(BaseModel):
    pass


class SaveThresholdsParams(BaseModel):
    lcp_good_ms: int = Field(2500, description="LCP good threshold (ms)", ge=1)
    lcp_poor_ms: int = Field(4000, description="LCP poor threshold (ms)", ge=1)
    cls_good: float = Field(0.1, description="CLS good threshold", ge=0)
    cls_poor: float = Field(0.25, description="CLS poor threshold", ge=0)
    inp_good_ms: int = Field(200, description="INP good threshold (ms)", ge=1)
    inp_poor_ms: int = Field(500, description="INP poor threshold (ms)", ge=1)


class SaveCategoryTogglesParams(BaseModel):
    categories: list[str] = Field(
        default_factory=lambda: ["performance"],
        description="Lighthouse categories enabled by default for scheduled checks",
    )

    @field_validator("categories")
    @classmethod
    def _v_categories(cls, v):
        return _check_categories(v)


class SaveRetentionParams(BaseModel):
    retention_days: int = Field(30, description="How many days to keep raw Lighthouse JSON", ge=1, le=365)


class SaveNotifyModeParams(BaseModel):
    notify_mode: str = Field("regressions", description="all | regressions | off")

    @field_validator("notify_mode")
    @classmethod
    def _v_mode(cls, v):
        low = (v or "").strip().lower()
        if low and low not in NOTIFY_MODES:
            raise ValueError(f"unknown mode '{v}'. Allowed: {', '.join(NOTIFY_MODES)}")
        return low or "regressions"


class SaveScheduleParams(BaseModel):
    enabled: bool = Field(False, description="Turn on the automatic daily check")
    hour: int = Field(3, description="Run hour (0-23, server timezone)", ge=0, le=23)
    sites: str = Field("", description="Sites, comma or newline separated. Empty -- all sites from Sites Registry")


class GetScheduleParams(BaseModel):
    pass


# --------------------------- сущности результата ---------------------------

class MetricValue(BaseModel):
    """One metric with a number, category (good/needs-improvement/poor) and
    source. Regular BaseModel, not sdl.Entity: it only lives nested in a
    list inside SpeedSnapshot, it has no own identity in the UI -- same
    principle as list[dict] for AuditComparison.fixed/appeared/remains
    in SEO Audit Engine."""

    name: str = ""            # LCP, CLS, INP, TTFB, FCP...
    value: float = 0.0
    unit: str = ""            # ms, unitless, score
    category: str = ""        # good | needs-improvement | poor | unknown
    source: str = ""          # field (CrUX) | lab (Lighthouse)


class Opportunity(BaseModel):
    """One Lighthouse recommendation with potential time savings.
    Also a regular BaseModel -- a nested list element, not a separate SDL
    entity (see the explanation on MetricValue)."""

    id: str = ""
    title: str = ""
    description: str = ""
    savings_ms: float = 0.0
    score: float = 1.0


class SpeedSnapshot(sdl.Entity):
    """One speed check snapshot: scores, metrics, opportunities, timestamp."""

    url: str = ""
    strategy: str = ""
    categories: list[str] = []
    scores: dict[str, float] = {}       # {"performance": 0.87, ...}
    field_metrics: list[MetricValue] = []
    lab_metrics: list[MetricValue] = []
    has_field_data: bool = False
    opportunities: list[Opportunity] = []
    checked_at: str = ""
    status: str = "completed"           # running | completed | failed
    raw_ref: str = ""                   # ссылка на сырой JSON (для retention)


class SnapshotSummary(BaseModel):
    """A compact snapshot card for lists -- without the full metrics body.
    Regular BaseModel: lives nested in SnapshotList.items, the list itself
    (SnapshotList) is the entity that carries the SDL id/title."""

    id: str = ""
    url: str = ""
    strategy: str = ""
    performance_score: float = 0.0
    checked_at: str = ""
    has_field_data: bool = False
    status: str = "completed"


class SnapshotList(sdl.Entity):
    items: list[SnapshotSummary] = []
    total: int = 0


class ComparisonResult(sdl.Entity):
    """The difference between the two most recent snapshots of one url+strategy."""

    url: str = ""
    strategy: str = ""
    previous_checked_at: str = ""
    current_checked_at: str = ""
    score_deltas: dict[str, float] = {}      # {"performance": -0.05, ...}
    metric_deltas: dict[str, float] = {}     # {"LCP": +350.0, ...}
    regressed: bool = False


class ScheduleState(BaseModel):
    """A nested value-object inside SettingsState -- a regular BaseModel, not
    an sdl.Entity: an sdl.Entity requires id/title on EVERY instantiation
    (confirmed by reading the imperal_sdk.sdl.Entity source), which makes no
    sense for a settings sub-object with no own identity in lists/panels."""

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
    """Everything configurable in the app -- one entity for one App settings
    screen (UI_INTERFACE_STANDARD.md, rule 3: everything in one place).

    THIS is the only sdl.Entity in the whole settings group -- id/title are
    passed explicitly in the handler (get_speed_settings), not as a
    class-level default, because a shared mutable default instance at the
    class level is also a classic pydantic/python shared-mutable-state
    trap between calls."""

    key_connected: bool = False
    thresholds: ThresholdsState = Field(default_factory=ThresholdsState)
    default_categories: list[str] = Field(default_factory=lambda: ["performance"])
    retention_days: int = 30
    notify_mode: str = "regressions"
    schedule: ScheduleState = Field(default_factory=ScheduleState)
