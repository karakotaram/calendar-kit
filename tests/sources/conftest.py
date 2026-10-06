"""Fixtures for the per-source tests."""
from __future__ import annotations

from contextlib import ExitStack

import pytest

from tests.sources.record import replaying


@pytest.fixture
def serve():
    """Answer the scraper's HTTP requests from saved files, offline.

        serve("the_rockwell/2026-10-06")                 # a capture from tests.sources.record
        serve({"/wp-json/tribe/": "the_rockwell/api.json.gz"})   # URL substring -> saved file

    Paths are relative to tests/fixtures/ unless absolute. A request it has no
    answer for raises, as the `offline` fixture does, so use one or the other.
    One capture per test; a second call replaces the first.
    """
    stack = ExitStack()

    def use(capture):
        nonlocal stack
        stack.close()
        stack = ExitStack()
        stack.enter_context(replaying(capture))

    yield use
    stack.close()
