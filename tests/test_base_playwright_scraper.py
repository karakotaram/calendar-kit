"""The Playwright base class must not report a refused page as an empty one.

`goto()` used to discard Playwright's response. A 403, or a Cloudflare
challenge, is still an HTML page; it parsed as zero listings and the run
recorded "ok, 0 events". Six sources sat like that in CI for weeks.

It must also launch headless unless a source opts into a visible window.

These use a fake page (and a fake launcher), so no browser is launched.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.models.event import EventCreate
from src.scrapers.base_playwright_scraper import BasePlaywrightScraper, ScrapeRefusedError


class _Response:
    def __init__(self, status: int, url: str):
        self.status = status
        self.url = url


class _Page:
    """Answers each URL with a scripted status; a missing entry means no response."""

    def __init__(self, statuses: dict, redirects: dict | None = None):
        self.statuses = statuses
        self.redirects = redirects or {}

    def goto(self, url, wait_until=None, timeout=None):
        if url not in self.statuses:
            return None
        return _Response(self.statuses[url], self.redirects.get(url, url))

    def close(self): ...


def _event() -> EventCreate:
    return EventCreate(title="A real listing", description="Something worth attending.",
                       start_datetime=datetime(2026, 10, 20, 19, 0),
                       source_url="https://venue.example/e/1", source_name="Fake Venue")


class _Scraper(BasePlaywrightScraper):
    def __init__(self, page, urls, found):
        super().__init__(source_name="Fake Venue", source_url=urls[0])
        self._fake, self._urls, self._found = page, urls, found

    def setup_browser(self):
        self._page = self._fake

    def cleanup_browser(self):
        self._page = None

    def scrape_events(self):
        # Like the real scrapers: navigate, parse whatever came back, and
        # swallow nothing — a challenge page simply has no listings on it.
        for url in self._urls:
            self.goto(url)
        return list(self._found)


def test_zero_events_after_a_403_is_a_failure():
    """The silent case: blocked, parsed the block page, returned nothing."""
    url = "https://venue.example/events"
    scraper = _Scraper(_Page({url: 403}), [url], found=[])

    with pytest.raises(ScrapeRefusedError) as raised:
        scraper.run()

    message = str(raised.value)
    assert "403" in message and url in message and "Fake Venue" in message


def test_the_message_names_where_a_redirect_landed():
    """Skip the Small Talk's old URL redirects; the refusal may come from the target."""
    url, landed = "https://venue.example/old", "https://venue.example/store"
    scraper = _Scraper(_Page({url: 503}, {url: landed}), [url], found=[])

    with pytest.raises(ScrapeRefusedError, match=r"503 .*old.*redirected to .*store"):
        scraper.run()


def test_an_empty_listing_that_loaded_is_not_a_failure():
    """A venue with nothing scheduled is legitimate and must stay `ok`."""
    url = "https://venue.example/events"
    assert _Scraper(_Page({url: 200}), [url], found=[]).run() == []


def test_events_found_despite_one_refused_page_are_kept():
    """A 404 on a secondary page does not discard what the primary page yielded."""
    urls = ["https://venue.example/events", "https://venue.example/events?page=2"]
    scraper = _Scraper(_Page({urls[0]: 200, urls[1]: 404}), urls, found=[_event()])

    events = scraper.run()

    assert len(events) == 1
    assert scraper.refused_navigations() == [(urls[1], urls[1], 404)]


def test_a_navigation_without_a_response_is_not_a_refusal():
    """Playwright returns None for same-document navigations; that is no status at all."""
    url = "https://venue.example/events#upcoming"
    assert _Scraper(_Page({}), [url], found=[]).run() == []


def test_each_run_starts_with_a_clean_record():
    """A scraper object reused across runs must not carry yesterday's 403 forward."""
    url = "https://venue.example/events"
    page = _Page({url: 403})
    scraper = _Scraper(page, [url], found=[])
    with pytest.raises(ScrapeRefusedError):
        scraper.run()

    page.statuses[url] = 200
    assert scraper.run() == []
    assert scraper.navigations == [(url, url, 200)]


# --------------------------------------------------------------------------- #
# A visible browser window is opt-in
# --------------------------------------------------------------------------- #

class _Launcher:
    """Stands in for sync_playwright(): records how the browser was launched."""

    def __init__(self):
        self.launched, self.routes = {}, []
        launcher = self

        class _Context:
            def route(self, pattern, handler):
                launcher.routes.append(pattern)

            def new_page(self):
                return object()

        class _Browser:
            def new_context(self, **options):
                launcher.context_options = options
                return _Context()

        class _Chromium:
            def launch(self, **options):
                launcher.launched = options
                return _Browser()

        class _Playwright:
            chromium = _Chromium()

        self._playwright = _Playwright()

    def __call__(self):
        return self

    def start(self):
        return self._playwright


class _Bare(BasePlaywrightScraper):
    def scrape_events(self):
        return []


def _launch(monkeypatch, scraper) -> _Launcher:
    import playwright.sync_api

    launcher = _Launcher()
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", launcher)
    scraper.setup_browser()
    return launcher


def test_the_browser_is_headless_unless_a_source_opts_in(monkeypatch):
    """A visible window was a per-source exception Cambridge's owner chose for
    two bookstores. It must never become anyone's default: a scraper that does
    not ask for one gets a headless browser with its own user-agent."""
    scraper = _Bare(source_name="Fake Venue", source_url="https://venue.example/events")
    assert scraper.headless is True and scraper.user_agent is None

    launched = _launch(monkeypatch, scraper)

    assert launched.launched["headless"] is True
    assert "user_agent" not in launched.context_options, "the browser's own, honest user-agent"
    for flag in ("--disable-blink-features=AutomationControlled", "--enable-automation"):
        assert flag not in launched.launched["args"]


def test_a_window_is_opened_only_when_asked_for(monkeypatch):
    """headless=False opens a window that behaves like an ordinary browser, so
    it does not block the page's own assets (bot checks load theirs)."""
    scraper = _Bare(source_name="Fake Venue", source_url="https://venue.example/events", headless=False)
    launched = _launch(monkeypatch, scraper)
    assert launched.launched["headless"] is False
    assert launched.routes == []


def test_every_playwright_adapter_defaults_to_headless():
    """Each browser-driven adapter, built from a registry row with no params,
    runs headless; a window needs a human to set its opt-in param."""
    import inspect

    from src import adapters

    checked = 0
    for info in adapters.available().values():
        if info.kind != "playwright":
            continue
        required = [n for n, p in inspect.signature(info.cls).parameters.items()
                    if p.kind is p.KEYWORD_ONLY and p.default is p.empty]
        if required:
            continue
        scraper = info.cls(source_name="Fake Venue", url="https://venue.example/events", venue={})
        assert scraper.headless is True, f"{info.name} opens a window by default"
        checked += 1
    assert checked, "no playwright adapter was checked"
