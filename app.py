"""Extension declaration, secrets, lifecycle hooks.

WHY BYOK (bring-your-own-key), same pattern as Media Studio/Trello.

Google's PageSpeed Insights API v5 is free but quota-gated per API key
(25,000 queries/day, ~4/sec documented default -- see
developers.google.com/speed/docs/insights/v5/get-started). It is not a paid
third-party service like Magnific, but it is still the USER'S OWN quota, not
a shared Imperal-wide budget -- so the same BYOK shape applies: the user
pastes their own key once, Vault-encrypted via `ctx.secrets`, and every call
runs against their own daily allowance. The API can technically be called
without a key at all, but then the anonymous, much stricter, non-adjustable
shared quota applies -- not something this app can offer honestly across
many tenants sharing one process.

WHY write_mode="both" (matches Media Studio's connect_magnific reasoning).

Declaring the secret `write_mode="user"` only would mean only the platform's
generic Secrets screen could ever write it -- there would be no place in
this app itself to explain what a PageSpeed Insights key is, where to get
one, or whether a pasted key actually works before it's relied on later.
`write_mode="both"` keeps the generic Secrets screen working AND lets this
app's own `connect_pagespeed` validate the key against Google's API first,
so a bad paste is rejected immediately instead of failing silently on the
first real check.
"""

from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "page-speed-insights",
    version="0.1.0",
    display_name="Page Speed Insights",
    description=(
        "Real Core Web Vitals (LCP, CLS, INP) and Lighthouse scores for any "
        "site, via Google's official PageSpeed Insights API -- using your "
        "own free Google API key. Tracks history over time, flags "
        "regressions against Google's own official thresholds, and exposes "
        "an inter-extension check other apps (like SEO Audit Engine) can "
        "call automatically as part of a technical SEO pipeline."
    ),
    icon="icon.svg",
    actions_explicit=True,
    capabilities=["pagespeed:read", "pagespeed:write"],
)

chat = ChatExtension(
    ext,
    tool_name="page-speed-insights",
    description="Real Core Web Vitals and Lighthouse scores for any site, with history and regression alerts.",
)

ext.secret(
    name="pagespeed_api_key",
    description=(
        "Your Google PageSpeed Insights API key -- console.cloud.google.com "
        "-> APIs & Services -> Library -> enable 'PageSpeed Insights API' -> "
        "Credentials -> Create API key. Free; your own daily quota applies "
        "(25,000 requests/day by default)."
    ),
    required=True,
    write_mode="both",
    max_bytes=200,
    rotation_hint_days=180,
)(lambda: None)


@ext.health_check
async def health_check(ctx) -> bool:
    """Basic liveness check -- confirms the store surface is reachable."""
    await ctx.store.query("speed_snapshots", limit=1)
    return True
