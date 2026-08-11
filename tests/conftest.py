"""Shared fixtures.

MockContext gives working store/http but has NO `secrets` by default
(confirmed: `dir(MockContext())` lists no `secrets` attribute) -- Media
Studio's tests/conftest.py hit the same gap and fixes it the same way.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def ctx():
    from imperal_sdk.testing import MockContext, MockSecretStore

    mock = MockContext()
    mock.secrets = MockSecretStore({})
    return mock


@pytest.fixture
def ctx_with_key(ctx):
    """Same as `ctx` but with a PageSpeed Insights API key already saved."""
    from imperal_sdk.testing import MockSecretStore
    ctx.secrets = MockSecretStore({"pagespeed_api_key": "test-key-123"})
    return ctx
