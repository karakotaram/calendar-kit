"""The geocoder: hand-pinned venues first, then cached OpenStreetMap answers,
then - only when switched on - Nominatim itself, politely and inside the region.
"""
import json

import pytest

import src.config as config
from src.utils import geocoder

VENUES = """
The Fillmore:
  lat: 37.78404
  lng: -122.43293
  city: San Francisco
  aliases: [Fillmore Auditorium]
Fox Theater:
  city: Oakland
Toad:
  lat: 37.80
  lng: -122.27
"""


@pytest.fixture
def geo(tmp_path, monkeypatch):
    venues = tmp_path / "venues.yaml"
    venues.write_text(VENUES)
    monkeypatch.setattr(geocoder, "VENUES_PATH", venues)
    monkeypatch.setattr(geocoder, "CACHE_PATH", tmp_path / "geocode-cache.json")
    monkeypatch.setitem(config.RAW["region"], "map_center", [-122.27, 37.80])
    monkeypatch.setattr(config, "CITIES", ["San Francisco", "Oakland", "San Jose", "Berkeley"])
    monkeypatch.delenv("GEOCODER", raising=False)
    monkeypatch.setattr(geocoder.time, "sleep", lambda s: None)
    geocoder.reload()
    yield tmp_path
    geocoder.reload()


class FakeNominatim:
    """Stands in for requests.get; answers from a table keyed by query."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        answer = self.answers[params["q"]]
        if isinstance(answer, Exception):
            raise answer

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return answer
        return Response()


def osm(lat, lon, city=None, category="amenity", type_="theatre"):
    return {"lat": str(lat), "lon": str(lon), "category": category, "type": type_,
            "address": {"city": city} if city else {}}


def test_pinned_venues_match_by_name_alias_and_whole_words(geo):
    assert geocoder.get_venue_coordinates("The Fillmore") == (37.78404, -122.43293)
    assert geocoder.get_venue_coordinates("fillmore auditorium") == (37.78404, -122.43293)
    assert geocoder.get_venue_coordinates("The Fillmore - Upstairs") == (37.78404, -122.43293)
    assert geocoder.get_venue_city("Fox Theater") == "Oakland"
    assert geocoder.get_venue_coordinates("Fox Theater") == (None, None), "a city-only entry has no pin"
    # Short names match exactly, never inside another name
    assert geocoder.get_venue_coordinates("Toad") == (37.80, -122.27)
    assert geocoder.get_venue_coordinates("Toad Hall Gallery") == (None, None)


def test_an_empty_or_missing_venues_file_is_fine(geo):
    (geo / "venues.yaml").write_text("# only comments\n")
    geocoder.reload()
    assert geocoder.get_venue_coordinates("The Fillmore") == (None, None)


def test_a_malformed_entry_fails_loudly(geo):
    (geo / "venues.yaml").write_text("Half Pinned:\n  lat: 37.8\n")
    geocoder.reload()
    with pytest.raises(ValueError, match="both lat and lng"):
        geocoder.get_venue_coordinates("anything")


def test_a_city_is_read_from_an_address_only_where_it_is_the_city(geo):
    assert geocoder.get_venue_city("Some Bar", "123 Main St, Berkeley, CA 94704") == "Berkeley"
    assert geocoder.get_venue_city("Some Bar", "2 Elm St San Jose CA") == "San Jose"
    assert geocoder.get_venue_city("Some Bar", "1600 San Jose Ave") is None, "a street, not the city"
    assert geocoder.get_venue_city("Oakland Museum of California") is None


def test_no_network_unless_switched_on(geo, offline):
    assert geocoder.get_venue_coordinates("Somewhere New", "500 Market St") == (None, None)
    assert geocoder.get_venue_city("Somewhere New", "500 Market St") is None
    assert not (geo / "geocode-cache.json").exists()


def test_nominatim_answers_are_cached_with_an_honest_user_agent(geo, monkeypatch):
    monkeypatch.setenv("GEOCODER", "nominatim")
    fake = FakeNominatim({"1805 Geary Blvd": [osm(37.7840, -122.4330, "San Francisco")]})
    monkeypatch.setattr(geocoder.requests, "get", fake)

    assert geocoder.get_venue_city("A New Club", "1805 Geary Blvd") == "San Francisco"
    assert geocoder.get_venue_coordinates("A New Club", "1805 Geary Blvd") == (37.784, -122.433)
    assert len(fake.calls) == 1, "the second question is answered from the cache"

    call = fake.calls[0]
    assert call["headers"]["User-Agent"] == config.USER_AGENT
    assert "Mozilla" not in config.USER_AGENT
    assert call["params"]["bounded"] == 1 and call["params"]["viewbox"]

    cache = json.loads((geo / "geocode-cache.json").read_text())
    assert cache == {"address:1805 geary blvd": {"lat": 37.784, "lng": -122.433, "city": "San Francisco"}}

    # A fresh process reads the cache file instead of asking again
    geocoder.reload()
    assert geocoder.get_venue_coordinates("A New Club", "1805 Geary Blvd") == (37.784, -122.433)
    assert len(fake.calls) == 1


def test_answers_outside_the_region_or_not_a_venue_are_rejected(geo, monkeypatch):
    monkeypatch.setenv("GEOCODER", "nominatim")
    fake = FakeNominatim({
        "The Independent": [osm(40.7128, -74.0060, "New York")],                       # another coast
        "Oakland": [osm(37.8044, -122.2712, "Oakland", category="place", type_="city")],  # a town, not a venue
    })
    monkeypatch.setattr(geocoder.requests, "get", fake)
    assert geocoder.get_venue_coordinates("The Independent") == (None, None)
    assert geocoder.get_venue_coordinates("Oakland") == (None, None)
    cache = json.loads((geo / "geocode-cache.json").read_text())
    assert cache == {"name:the independent": None, "name:oakland": None}, "misses are cached too"


def test_a_network_failure_is_not_remembered_as_a_miss(geo, monkeypatch):
    monkeypatch.setenv("GEOCODER", "nominatim")
    fake = FakeNominatim({"9 Pier St": ConnectionError("down")})
    monkeypatch.setattr(geocoder.requests, "get", fake)
    assert geocoder.get_venue_coordinates("Pier Nine", "9 Pier St") == (None, None)
    assert not (geo / "geocode-cache.json").exists()


def test_at_most_one_request_a_second(geo, monkeypatch):
    monkeypatch.setenv("GEOCODER", "nominatim")
    monkeypatch.setattr(geocoder.requests, "get", FakeNominatim({"1 A St": [], "2 B St": []}))
    clock = iter([100.0, 100.0, 100.2, 100.2, 101.2])
    monkeypatch.setattr(geocoder.time, "monotonic", lambda: next(clock))
    slept = []
    monkeypatch.setattr(geocoder.time, "sleep", slept.append)
    monkeypatch.setattr(geocoder, "_last_request_at", 0.0)

    geocoder.get_venue_coordinates(None, "1 A St")
    geocoder.get_venue_coordinates(None, "2 B St")
    assert slept == [pytest.approx(0.8)]


def test_the_api_never_geocodes_over_the_network(geo, monkeypatch):
    monkeypatch.setenv("GEOCODER", "nominatim")
    fake = FakeNominatim({})
    monkeypatch.setattr(geocoder.requests, "get", fake)
    assert geocoder.get_venue_coordinates("Unknown", "1 Nowhere Rd", allow_network=False) == (None, None)
    assert fake.calls == []
