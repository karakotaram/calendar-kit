"""TEMPLATE for one source's tests. Replace this docstring with the source's.

    cp tests/sources/_template.py tests/sources/test_<slug_>.py

The leading underscore keeps pytest from collecting this file. Replace every
REPLACE below; the placeholder values fail loudly if one is missed.

What the docstring of the copy should say, so the next person can trust it:

    <Venue Name>: <adapter, or "custom scraper">, registry/<slug>.yaml.

    Capture recorded <YYYY-MM-DD> with `python -m tests.sources.record
    "<Venue Name>"`: <N> events, <first date> to <last date>. That day the
    venue's own site listed <what you saw: "about 60 shows through March,
    3 pages">, so <N> is the whole listing, not page one of it.

A browser-driven (playwright) source has no capture: its traffic goes through
the browser. Save the rendered page (`page.content()`, gzipped) into
tests/fixtures/<slug_>/ and test the parse step on it with
`tests.conftest.read_fixture`, as test_templates.py does with inline HTML.
"""
from __future__ import annotations

from datetime import datetime

from src.quality.invariants import check_invariants
from src.sources import BY_NAME

SOURCE = "REPLACE: the registry name, exactly"
CAPTURE = "REPLACE: <slug_>/<YYYY-MM-DD>"        # under tests/fixtures/
CAPTURED_ON = datetime(1970, 1, 1)                # REPLACE: the capture's date


def scrape(serve):
    serve(CAPTURE)
    scraper = BY_NAME[SOURCE].load()
    # A custom scraper built from src/scrapers/custom/_template.py reads
    # yearless dates against `as_of`; pin it so the capture parses the same
    # way next year:  scraper.as_of = CAPTURED_ON.date()
    return scraper.run()


def test_reads_the_whole_listing(serve):
    """Every event in the capture, first to last.

    The count is what `record` printed, after you checked it against the
    venue's own site. A scraper that reads page one only (Cambridge: The
    Middle East published 20 of 227 shows for months) fails here.
    """
    events = scrape(serve)

    assert len(events) == -1                                          # REPLACE
    assert min(e.start_datetime for e in events) == datetime(1970, 1, 1)   # REPLACE
    assert max(e.start_datetime for e in events) == datetime(1970, 1, 1)   # REPLACE


def test_spot_checked_events_match_the_venue(serve):
    """Checked by hand against the venue's own pages on <YYYY-MM-DD>:

      - "<evening title>": <Fri Oct 9>, <7:30 PM>    <its URL>
      - "<second title>":  <date>, <time>            <its URL>

    One is an evening event: that is the one a time-zone bug moves to the next
    day. If the listing spans a daylight-saving change, check one on each side.
    """
    by_title = {e.title: e for e in scrape(serve)}

    evening = by_title["REPLACE: an evening event's title"]
    assert evening.start_datetime == datetime(1970, 1, 1, 19, 30)    # REPLACE
    assert not evening.all_day

    # REPLACE: a second spot check; and if the venue lists any date without a
    # time, one of those, which must be all-day at 00:00:
    #   untimed = by_title["..."]
    #   assert untimed.all_day and untimed.start_datetime == datetime(2026, 11, 20)


def test_output_satisfies_the_invariants(serve):
    """No clock-stamped starts, no pileups, no impossible dates, no page chrome.
    `now` is the capture's date, so the check does not age with the fixture."""
    events = [e.model_dump(mode="json") for e in scrape(serve)]
    errors = [v for v in check_invariants(events, now=CAPTURED_ON) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
