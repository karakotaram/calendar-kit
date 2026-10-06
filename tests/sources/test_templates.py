"""The templates new sources are copied from, and the capture tools their tests use.

If src/scrapers/custom/_template.py or tests/sources/record.py breaks, every
source started from them starts broken, so they are held to the same rules as
a real source: a fabricated time, a skipped page or a network call fails here.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest
import requests

from src.quality.invariants import check_invariants
from src.scrapers.custom import _template as template
from tests.sources import record

LISTING = "https://www.example.org/events/"
VENUE = {"name": "Example Hall", "street": "1 Main St", "city": "Oakland", "zip": "94612"}
AS_OF = date(2026, 10, 6)          # a Tuesday

PAGE_1 = """<!doctype html><html><head><title>Events | Example Hall</title></head><body>
<article class="event">
  <h3 class="event-title"><a href="/events/late-set">Late Set</a></h3>
  <p class="event-date">Fri, Oct 9</p>
  <p class="event-time">Doors 7:00 PM / Show 8:30 PM</p>
  <div class="event-summary">Two sets of standards and originals.</div>
  <img src="/media/late-set.jpg">
</article>
<article class="event">
  <h3 class="event-title"><a href="/events/workshop">Printmaking Workshop</a></h3>
  <p class="event-date">Saturday, October 10, 2026</p>
  <p class="event-time">6:00 - 8:00 PM</p>
  <div class="event-summary">Bring an apron; materials provided.</div>
</article>
<article class="event">
  <h3 class="event-title"><a href="/events/fair">Harvest Fair</a></h3>
  <p class="event-date">Sun, Oct 11</p>
  <p class="event-time"></p>
  <div class="event-summary">Stalls, music and cider all afternoon.</div>
</article>
<article class="event">
  <h3 class="event-title"><a href="/events/picnic">Lakeside Picnic</a></h3>
  <p class="event-date">Sat, Oct 17</p>
  <p class="event-time">noon</p>
  <p class="event-place"><span class="name">Lake Merritt Amphitheater</span>
     <span class="street">1520 Lakeside Dr</span> <span class="city">Oakland</span></p>
</article>
<article class="event">
  <h3 class="event-title"><a href="/events/undated">A Show With No Date Yet</a></h3>
  <p class="event-date">Coming soon</p>
  <p class="event-time">8 PM</p>
</article>
<article class="event">
  <h3 class="event-title"><a href="/events/ambiguous">Open Mic</a></h3>
  <p class="event-date">Wed, Oct 14</p>
  <p class="event-time">7:30</p>
</article>
<article class="event">
  <h3 class="event-title"><a href="/events/wrong-weekday">Misprinted Night</a></h3>
  <p class="event-date">Mon, Oct 9</p>
  <p class="event-time">8 PM</p>
</article>
<article class="event">
  <h3 class="event-title"><a href="/events/cancelled">CANCELLED: Brass Night</a></h3>
  <p class="event-date">Thu, Oct 15</p>
  <p class="event-time">8 PM</p>
</article>
<a class="next" href="/events/?page=2">Next</a>
</body></html>"""

PAGE_2 = """<!doctype html><html><head><title>Events | Example Hall</title></head><body>
<article class="event">
  <h3 class="event-title"><a href="/events/new-year">New Year Recital</a></h3>
  <p class="event-date">Thu, Jan 7</p>
  <p class="event-time">7:30 p.m.</p>
</article>
</body></html>"""


def _events_by_title():
    events, next_url = template.parse_listing(PAGE_1, LISTING, venue=VENUE, as_of=AS_OF,
                                              source_name="Example Hall")
    return {e.title: e for e in events}, next_url


def test_listed_times_are_read_and_nothing_is_invented():
    by_title, next_url = _events_by_title()

    assert next_url == "https://www.example.org/events/?page=2"
    # The show, not the doors; a range's start borrows the end's meridiem
    assert by_title["Late Set"].start_datetime == datetime(2026, 10, 9, 20, 30)
    assert by_title["Printmaking Workshop"].start_datetime == datetime(2026, 10, 10, 18, 0)
    assert by_title["Lakeside Picnic"].start_datetime == datetime(2026, 10, 17, 12, 0)
    # A date with no time is all-day, never a guessed hour
    fair = by_title["Harvest Fair"]
    assert fair.all_day and fair.start_datetime == datetime(2026, 10, 11, 0, 0)
    assert not by_title["Late Set"].all_day


def test_undated_unreadable_and_cancelled_listings_are_skipped():
    by_title, _ = _events_by_title()

    assert "A Show With No Date Yet" not in by_title           # no date
    assert "Open Mic" not in by_title                          # 7:30 - morning or evening?
    assert "Misprinted Night" not in by_title                  # no nearby year has a Monday Oct 9
    assert not any("Brass Night" in t for t in by_title)       # cancelled
    assert len(by_title) == 4


def test_an_events_own_place_beats_the_venue_defaults():
    by_title, _ = _events_by_title()

    home = by_title["Late Set"]
    assert (home.venue_name, home.street_address, home.city) == ("Example Hall", "1 Main St", "Oakland")
    away = by_title["Lakeside Picnic"]
    assert (away.venue_name, away.street_address) == ("Lake Merritt Amphitheater", "1520 Lakeside Dr")
    assert away.zip_code is None                               # not the hall's ZIP
    # A listing with no summary gets a description built from facts
    assert away.description == "Lakeside Picnic at Lake Merritt Amphitheater"
    assert home.source_url == "https://www.example.org/events/late-set"
    assert home.image_url == "https://www.example.org/media/late-set.jpg"


def test_the_template_is_not_a_registered_source():
    """It is copied, never run: the underscore keeps it out of the registry."""
    from src.sources import SOURCES

    assert not any((s.module or "").endswith("._template") for s in SOURCES)
    with pytest.raises(ValueError, match="not registered"):
        template.Scraper()


def _stand_in_for_the_network(monkeypatch, pages: dict):
    state = {"up": True}

    def send(self, request, **kwargs):
        if not state["up"]:
            raise AssertionError(f"network call during replay: {request.url}")
        return record._requests_response(request, pages[request.url].encode(),
                                         "text/html; charset=utf-8", 200)

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setattr(template, "PAGE_DELAY_S", 0)
    return state


def test_a_recorded_run_replays_offline_page_for_page(tmp_path, monkeypatch, serve):
    """record captures every page the scraper read; serve answers from it alone."""
    network = _stand_in_for_the_network(monkeypatch, {
        LISTING: PAGE_1,
        "https://www.example.org/events/?page=2": PAGE_2,
    })
    scraper = template.Scraper("Example Hall", LISTING, VENUE, as_of=AS_OF)

    with record.capturing(tmp_path / "capture") as manifest:
        live = scraper.run()
    assert set(manifest) == {LISTING, "https://www.example.org/events/?page=2"}

    network["up"] = False
    serve(tmp_path / "capture")
    replayed = template.Scraper("Example Hall", LISTING, VENUE, as_of=AS_OF).run()

    # Both pages were read: page 2's January date took the year its weekday names
    assert [(e.title, e.start_datetime) for e in replayed] == [(e.title, e.start_datetime) for e in live]
    assert datetime(2027, 1, 7, 19, 30) in [e.start_datetime for e in replayed]
    assert len(replayed) == 5
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in replayed],
                                          now=datetime(2026, 10, 6)) if v.severity == "error"]
    assert not errors


def test_replay_refuses_what_it_did_not_record(tmp_path, serve):
    page = tmp_path / "listing.html"
    page.write_text(PAGE_2)
    serve({"example.org/events/": page})

    assert "New Year Recital" in requests.get(LISTING, timeout=5).text
    with pytest.raises(AssertionError, match="no saved response"):
        requests.get("https://elsewhere.example.net/", timeout=5)


def test_a_date_in_the_query_does_not_break_replay():
    """A capture made on one day still answers a scraper asking with another day's date."""
    assert (record.url_key("https://x.example/api?start_date=2026-10-06&page=2")
            == record.url_key("https://x.example/api?page=2&start_date=2027-03-01"))
    assert (record.url_key("https://x.example/api?page=2")
            != record.url_key("https://x.example/api?page=3"))
