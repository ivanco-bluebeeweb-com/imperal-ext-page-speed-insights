"""Google PageSpeed Insights API v5 client -- runPagespeed.

Контракт подтверждён по официальной документации Google
(developers.google.com/speed/docs/insights/v5/reference/pagespeedapi/runpagespeed),
не выдуман:

  GET https://www.googleapis.com/pagespeedonline/v5/runPagespeed
  params: url (required), key, strategy (mobile|desktop),
          category (repeatable: PERFORMANCE|ACCESSIBILITY|BEST_PRACTICES|SEO|PWA)

Ответ содержит top-level `loadingExperience` (полевые данные CrUX для
конкретной страницы, есть только если у страницы достаточно трафика) и
`lighthouseResult` (всегда есть -- лабораторный прогон). `category` внутри
`loadingExperience.metrics[key].category` -- это официальные
"FAST"/"AVERAGE"/"SLOW" ярлыки Google, но мы пересчитываем свою
good/needs-improvement/poor категорию из сырых чисел по настраиваемым
порогам (см. models.DEFAULT_THRESHOLDS), чтобы порог был виден и управляем
в этом приложении, а не скрыт внутри чужого ярлыка.

ПОЧЕМУ РАЗБОР ОТВЕТА ЗАЩИТНЫЙ (тот же принцип, что magnific_client.py у
Media Studio): Google документирует форму ответа, но реальные ключи внутри
`lighthouseResult.audits` по опыту сообщества иногда отсутствуют для
некоторых категорий/страниц (например когда категория не запрошена или
метрика недоступна). Отсутствующий ключ -- это "нет данных", а не "0" --
никогда не подставляем 0 молча.
"""

from __future__ import annotations

import asyncio

BASE_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# Google category query param -> наши lowercase-имена в models.CATEGORIES
_CATEGORY_TO_GOOGLE = {
    "performance": "PERFORMANCE",
    "accessibility": "ACCESSIBILITY",
    "best-practices": "BEST_PRACTICES",
    "seo": "SEO",
    "pwa": "PWA",
}

# audit id в lighthouseResult.audits -> наше стабильное имя метрики
_LAB_METRIC_AUDITS = {
    "largest-contentful-paint": "LCP",
    "cumulative-layout-shift": "CLS",
    "interactive": "TTI",
    "first-contentful-paint": "FCP",
    "speed-index": "SI",
    "total-blocking-time": "TBT",
    "server-response-time": "TTFB",
}

# CrUX loadingExperience.metrics key -> наше стабильное имя метрики
_FIELD_METRIC_KEYS = {
    "LARGEST_CONTENTFUL_PAINT_MS": "LCP",
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": "CLS",
    "INTERACTION_TO_NEXT_PAINT": "INP",
    "FIRST_CONTENTFUL_PAINT_MS": "FCP",
    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": "TTFB",
}


class ProviderError(Exception):
    def __init__(self, message: str, code: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


async def validate_api_key(ctx, api_key: str) -> None:
    """Пробный вызов на example.com -- дёшево и стабильно доступно, только
    чтобы подтвердить, что ключ реально принимается Google, до сохранения
    (тот же паттерн, что validate_api_key в magnific_client.py)."""
    params = {"url": "https://example.com", "key": api_key, "category": "PERFORMANCE"}
    resp = await ctx.http.get(BASE_URL, params=params, timeout=20)
    if resp.status_code == 400:
        raise ProviderError("Google rejected this key (400 Bad Request).", "PSI_KEY_INVALID")
    if resp.status_code in (401, 403):
        raise ProviderError(
            "Google rejected this key -- check that the PageSpeed Insights API "
            "is enabled for this project in Google Cloud Console.", "PSI_KEY_INVALID",
        )
    if resp.status_code >= 400:
        raise ProviderError(f"Google returned status {resp.status_code} while verifying the key.", "PSI_PROVIDER_ERROR", True)


async def run_pagespeed(
    ctx, api_key: str, url: str, *, strategy: str = "mobile",
    categories: list[str] | None = None, max_retries: int = 2,
) -> dict:
    """Один вызов runPagespeed с backoff на 429/500 -- оба задокументированы
    Google как реальные, разные по смыслу коды (rate limit vs undocumented
    per-origin throttling), см. PREPARATION.md раздел 3."""
    cats = categories or ["performance"]
    params = [("url", url), ("key", api_key), ("strategy", strategy)]
    for cat in cats:
        google_cat = _CATEGORY_TO_GOOGLE.get(cat)
        if google_cat:
            params.append(("category", google_cat))

    last_exc: ProviderError | None = None
    for attempt in range(max_retries + 1):
        resp = await ctx.http.get(BASE_URL, params=params, timeout=60)
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception as exc:
                raise ProviderError(f"Google's response could not be parsed as JSON: {exc}", "PSI_UNEXPECTED_RESPONSE")
        if resp.status_code == 429:
            last_exc = ProviderError(
                "Google throttled checks (daily/per-second rate limit).",
                "PSI_RATE_LIMITED", True,
            )
        elif resp.status_code == 500:
            last_exc = ProviderError(
                "Google temporarily throttled checks for this site (undocumented throttling).",
                "PSI_THROTTLED", True,
            )
        elif resp.status_code == 400:
            raise ProviderError(f"Google could not process the URL '{url}' (400 Bad Request).", "PSI_BAD_URL")
        else:
            raise ProviderError(f"Google returned status {resp.status_code}.", "PSI_PROVIDER_ERROR", True)

        if attempt < max_retries:
            await asyncio.sleep(2 ** attempt * 3)
    raise last_exc


def extract_scores(payload: dict) -> dict[str, float]:
    """lighthouseResult.categories.<id>.score (0..1) -> {"performance": 0.87}."""
    lr = payload.get("lighthouseResult") or {}
    cats = lr.get("categories") or {}
    out: dict[str, float] = {}
    for key, google_key in _CATEGORY_TO_GOOGLE.items():
        entry = cats.get(google_key.lower()) or cats.get(google_key) or cats.get(key)
        if isinstance(entry, dict) and entry.get("score") is not None:
            out[key] = round(float(entry["score"]), 4)
    return out


def extract_lab_metrics(payload: dict) -> list[dict]:
    """Метрики из лабораторного прогона Lighthouse -- всегда доступны,
    в отличие от полевых. Значения в audits.<id>.numericValue (мс) или
    audits.<id>.score."""
    lr = payload.get("lighthouseResult") or {}
    audits = lr.get("audits") or {}
    out = []
    for audit_id, metric_name in _LAB_METRIC_AUDITS.items():
        a = audits.get(audit_id)
        if not isinstance(a, dict):
            continue
        val = a.get("numericValue")
        if val is None:
            continue
        unit = "unitless" if metric_name == "CLS" else "ms"
        out.append({"name": metric_name, "value": float(val), "unit": unit, "source": "lab"})
    return out


def extract_field_metrics(payload: dict) -> tuple[list[dict], bool]:
    """Полевые данные CrUX из loadingExperience -- НЕ ВСЕГДА присутствуют
    (страница может не иметь достаточного трафика). Возвращает (метрики,
    has_field_data) -- has_field_data=False значит честно "нет данных",
    не "0"."""
    le = payload.get("loadingExperience")
    if not isinstance(le, dict) or not le.get("metrics"):
        return [], False
    metrics = le.get("metrics") or {}
    out = []
    for google_key, metric_name in _FIELD_METRIC_KEYS.items():
        m = metrics.get(google_key)
        if not isinstance(m, dict) or m.get("percentile") is None:
            continue
        unit = "unitless" if metric_name == "CLS" else "ms"
        value = float(m["percentile"]) / 100.0 if metric_name == "CLS" else float(m["percentile"])
        out.append({"name": metric_name, "value": value, "unit": unit, "source": "field"})
    return out, bool(out)


def extract_opportunities(payload: dict, limit: int = 10) -> list[dict]:
    """Lighthouse audits с score < 0.9 и реальной экономией времени --
    ровно то, что показывает web.dev в разделе Opportunities."""
    lr = payload.get("lighthouseResult") or {}
    audits = lr.get("audits") or {}
    items = []
    for audit_id, a in audits.items():
        if not isinstance(a, dict):
            continue
        details = a.get("details") or {}
        savings = details.get("overallSavingsMs")
        score = a.get("score")
        if savings is None or score is None:
            continue
        if score >= 0.9:
            continue
        items.append({
            "id": audit_id,
            "title": a.get("title") or audit_id,
            "description": a.get("description") or "",
            "savings_ms": float(savings),
            "score": float(score),
        })
    items.sort(key=lambda x: x["savings_ms"], reverse=True)
    return items[:limit]
