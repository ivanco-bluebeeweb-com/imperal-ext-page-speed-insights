# Page Speed Insights

Real Core Web Vitals (LCP, CLS, INP) and Lighthouse scores for any site, via
Google's official [PageSpeed Insights API v5](https://developers.google.com/speed/docs/insights/v5/get-started)
(`runPagespeed`) -- using your own free Google API key. Tracks a history of
snapshots per URL/strategy, compares runs, and flags regressions against
Google's own official Core Web Vitals thresholds.

## Why this exists

SEO Audit Engine already flags "slow response" using a crude signal
(server TTFB / elapsed milliseconds) -- honestly commented in its own code
as "not Core Web Vitals, just a coarse signal". Real Core Web Vitals are
what Google's ranking systems and PageSpeed Insights at web.dev actually
use, and nothing in this portfolio measured them until now.

## BYOK (bring your own key)

Google's PageSpeed Insights API is free but quota-gated per key (documented
default: 25,000 queries/day, ~4/sec). You paste your own key once (validated
against Google before saving), and every check runs against your own daily
allowance -- not a shared Imperal-wide budget.

## Official Core Web Vitals thresholds (defaults, editable in App settings)

| Metric | good | needs improvement | poor |
|---|---|---|---|
| LCP | <= 2.5s | <= 4s | > 4s |
| CLS | <= 0.1 | <= 0.25 | > 0.25 |
| INP | <= 200ms | <= 500ms | > 500ms |

Source: web.dev/articles/lcp, web.dev/articles/cls, web.dev/articles/inp.

## Tools

- `connect_pagespeed` / `disconnect_pagespeed` -- manage the Google API key.
- `check_site_speed` -- run a fresh check for a URL (mobile and/or desktop).
- `list_speed_snapshots` -- history of past checks, optionally filtered.
- `get_speed_snapshot` -- one snapshot in full (scores, metrics, opportunities).
- `compare_speed_snapshots` -- diff the two most recent runs for a URL+strategy.
- `save_speed_thresholds` / `save_speed_categories` / `save_speed_retention` /
  `save_speed_notify_mode` / `save_speed_schedule` / `get_speed_settings` --
  the App settings screen's own save/read handlers (one central screen, per
  `UI_INTERFACE_STANDARD.md`).

## Inter-extension surface (how other apps call this one)

- `@ext.expose("check_site_speed_ipc")` -- a WRITE IPC surface: runs a fresh
  check and returns `{scores, field_metrics, lab_metrics, top_opportunities}`.
  Any other extension can call it via `ctx.extensions.call("page-speed-insights",
  "check_site_speed_ipc", url=..., strategy=...)`, best-effort (this app being
  uninstalled/unconfigured should degrade the CALLER gracefully, never hard-fail
  it -- same convention as Sites Registry's `upsert_site`/`ping`).
- `@ext.expose("ping")` -- read-only liveness/installed check, no side effects.

This app never calls into SEO Audit Engine or any other app -- the dependency
is one-directional: consumers know about this app, this app does not know
about its consumers.

## Scheduled auto-check

`@ext.schedule` fires hourly and asks "is it due?" against settings stored
in this app's own store (hour, days, explicit site list) -- same alarm-clock
pattern as SEO Audit Engine's `seo_auto_audit`, so the schedule itself can be
changed anytime from the App settings screen without a redeploy. The site
list is explicit (set in App settings) -- there is no fallback discovery
against Sites Registry today, because Sites Registry does not expose a
read-only `list_sites` IPC surface (only `ping`/`upsert_site`).
