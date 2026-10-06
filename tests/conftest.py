"""Shared test fixtures.

The important one is `offline`: it makes any network call from a scraper raise,
so a test that accidentally reaches the internet fails loudly instead of quietly
becoming slow, flaky, and dependent on a venue's uptime.
"""
from __future__ import annotations

import gzip
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(module: str, name: str) -> str:
    """Read a saved page. Fixtures are gzipped; raw HTML would bloat the repo."""
    path = FIXTURES / module / name
    if not path.exists():
        pytest.skip(f"fixture missing: {path.relative_to(Path(__file__).parent.parent)} "
                    f"— capture with `cal scrape <source> --save-fixture`")
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


@pytest.fixture
def fixture_html():
    return read_fixture


@pytest.fixture
def offline(monkeypatch):
    """Make any outbound HTTP call fail.

    Scrapers are meant to be pure — URL in, events out — so their tests should
    parse saved HTML and never touch a network. Anything that slips through
    should be an error, not a silent slowdown.
    """
    import requests
    import urllib.request

    def blocked(*args, **kwargs):
        raise AssertionError(
            "network call in a test — parse a fixture instead "
            "(see tests/conftest.py)")

    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests, "post", blocked)
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


@pytest.fixture
def zone(monkeypatch):
    """Run a test as if the calendar were in another time zone.

    Event times are stored as wall-clock time in the zone calendar.config.yaml
    names. Fixtures saved from Cambridge Calendar's venues are Eastern, so the
    adapter tests that parse them call `zone("America/New_York")`; a source of
    your own region's needs nothing.
    """
    import pytz

    import src.config as config
    import src.models.event as event_module

    def use(name: str):
        tz = pytz.timezone(name)
        monkeypatch.setattr(event_module, "LOCAL_TZ", tz)
        monkeypatch.setattr(config, "TZ", tz)
        monkeypatch.setattr(config, "TIMEZONE_NAME", name)
        return tz

    return use


# --------------------------------------------------------------------------- #
# Engine tests run against Cambridge Calendar's sources and data, not yours
# --------------------------------------------------------------------------- #

DEMO_REGISTRY = FIXTURES / "demo_registry"


@pytest.fixture
def demo_registry(monkeypatch):
    """Swap in Cambridge Calendar's 41 sources (names and kinds only).

    The engine's tests - dedup ranking, the gate, monitoring - need a realistic
    registry to reason about, and must not change meaning as you register your
    own sources. These entries cannot be run; they exist to be ranked.
    """
    from src import sources
    import src.utils.deduplicator as dedup

    demo = sources.load_registry(DEMO_REGISTRY)
    by_name = {s.name: s for s in demo}
    monkeypatch.setattr(sources, "SOURCES", demo)
    monkeypatch.setattr(sources, "BY_NAME", by_name)
    monkeypatch.setattr(dedup, "BY_NAME", by_name)
    import src.agents.ci_monitor as ci_monitor
    import src.agents.health_monitor as health_monitor
    monkeypatch.setattr(health_monitor, "BY_NAME", by_name)
    monkeypatch.setattr(ci_monitor, "SOURCES", demo)
    return by_name


def sample_events() -> list:
    """Cambridge Calendar's published events on 2026-10-06 (descriptions trimmed)."""
    import json
    return json.loads(read_fixture("sample", "cambridge-events-2026-10-06.json.gz"))


def incident_events(name: str) -> list:
    """A run that really shipped, saved because it broke a rule (tests/fixtures/incidents)."""
    import json
    return json.loads(read_fixture("incidents", name))
