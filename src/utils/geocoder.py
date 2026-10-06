"""Where a venue is: coordinates and city for events that don't say.

Lookup order, first answer wins:

  1. data/venues.yaml - venues you have pinned by hand, by name or alias.
  2. data/geocode-cache.json - earlier OpenStreetMap answers, misses included,
     so a venue is looked up once, not once a day.
  3. Nominatim (OpenStreetMap), only with GEOCODER=nominatim in the
     environment and only where the caller allows the network. The API never
     does; the scrape does.

Nominatim's usage policy is the contract here: an honest user-agent naming
this calendar (config.USER_AGENT), at most one request a second, and every
result cached. Its answers are confined to a box around the map's centre
(calendar.config.yaml `region.map_center`), and anything outside it is
rejected: a venue called "The Independent" has namesakes in other cities, and
a pin in the wrong state is worse than no pin.

A wrong pin is a smaller harm than a wrong date, but the same rule applies: no
answer is better than a guess. Unknown venues get no coordinates and the
validator falls back to the region's default city.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import requests
import yaml

from src import config
from src.config import ROOT

logger = logging.getLogger(__name__)

VENUES_PATH = ROOT / "data" / "venues.yaml"
CACHE_PATH = ROOT / "data" / "geocode-cache.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_REQUEST_INTERVAL_S = 1.0   # Nominatim's absolute maximum is one a second
REGION_RADIUS_KM = 150         # generous: a metro area plus its edges

# Fuzzy matching is only allowed for known names at least this long. Shorter
# ones ("once", "toad", "vfw") are too generic to match inside other names.
_MIN_FUZZY_KEY_LEN = 5

# A venue name that resolves to a road or a whole town is a guess, not a place.
_NOT_A_VENUE = {"highway", "boundary"}
_TOWN_TYPES = {"city", "town", "village", "hamlet", "municipality", "county",
               "state", "region", "country", "suburb", "neighbourhood", "quarter"}

_venues: Optional[dict] = None
_cache: Optional[dict] = None
_last_request_at = 0.0


def reload() -> None:
    """Forget what was read from disk (tests, or after editing venues.yaml)."""
    global _venues, _cache
    _venues = None
    _cache = None


def _normalize(name: Optional[str]) -> str:
    name = (name or "").replace("’", "'").lower()
    return " ".join(name.split())


# --------------------------------------------------------------------------- #
# 1. data/venues.yaml
# --------------------------------------------------------------------------- #

def load_venues() -> dict:
    """{normalized name or alias: {"name", "lat", "lng", "city"}}.

    A malformed entry raises: a typo in a hand-kept table should stop the run
    loudly, not quietly put a venue in the ocean.
    """
    global _venues
    if _venues is not None:
        return _venues
    path = Path(VENUES_PATH)
    raw = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping of venue name to details")

    venues = {}
    for name, details in raw.items():
        details = details or {}
        lat, lng = details.get("lat"), details.get("lng")
        if (lat is None) != (lng is None):
            raise ValueError(f"{path}: {name!r} needs both lat and lng, or neither")
        if lat is not None:
            lat, lng = float(lat), float(lng)
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError(f"{path}: {name!r} has impossible coordinates {lat}, {lng}")
            if not in_region(lat, lng):
                logger.warning(f"{path}: {name!r} is outside the region's box; kept because you pinned it")
        entry = {"name": str(name), "lat": lat, "lng": lng, "city": details.get("city") or None}
        for key in [name, *(details.get("aliases") or [])]:
            venues[_normalize(str(key))] = entry
    _venues = venues
    return venues


def _match_venue_key(venue_name: Optional[str]) -> Optional[str]:
    """Resolve a scraped venue name to a key in venues.yaml.

    Matching order:
      1. Exact (case-insensitive) match on a name or alias.
      2. Whole-word partial match, preferring the longest (most specific) key,
         so "Middle East Downstairs" finds "middle east" and short generic keys
         never match as bare substrings.
    """
    key = _normalize(venue_name)
    if not key:
        return None
    venues = load_venues()
    if key in venues:
        return key

    best = None
    for known in venues:
        if len(known) < _MIN_FUZZY_KEY_LEN:
            continue
        if re.search(r"\b" + re.escape(known) + r"\b", key) or re.search(r"\b" + re.escape(key) + r"\b", known):
            if best is None or len(known) > len(best):
                best = known
    return best


# --------------------------------------------------------------------------- #
# The region's box
# --------------------------------------------------------------------------- #

def region_box(radius_km: float = REGION_RADIUS_KM) -> Optional[Tuple[float, float, float, float]]:
    """(south, west, north, east) around region.map_center, or None if unset."""
    center = (config.RAW.get("region") or {}).get("map_center")
    if not center or len(center) != 2:
        return None
    lng, lat = float(center[0]), float(center[1])
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lng - dlng, lat + dlat, lng + dlng)


def in_region(lat: float, lng: float) -> bool:
    box = region_box()
    if box is None:
        return True  # nothing to check against; only network results need it
    south, west, north, east = box
    return south <= lat <= north and west <= lng <= east


# --------------------------------------------------------------------------- #
# 2 and 3. The cache and Nominatim
# --------------------------------------------------------------------------- #

def _load_cache() -> dict:
    global _cache
    if _cache is None:
        path = Path(CACHE_PATH)
        try:
            _cache = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError) as e:
            logger.warning(f"Ignoring unreadable {path}: {e}")
            _cache = {}
    return _cache


def _save_cache() -> None:
    path = Path(CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # Sorted, so the committed file's diff shows only what was added
    tmp.write_text(json.dumps(_load_cache(), indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def network_enabled() -> bool:
    return os.environ.get("GEOCODER", "").strip().lower() == "nominatim"


def _query_for(venue_name: Optional[str], street_address: Optional[str]) -> Tuple[Optional[str], str]:
    """What to ask OpenStreetMap: a street address with a house number if
    there is one, else the venue's name."""
    if street_address and re.search(r"\d", street_address):
        return street_address.strip(), "address"
    if venue_name and venue_name.strip():
        return venue_name.strip(), "name"
    return None, ""


def _city_of(address: dict) -> Optional[str]:
    for field in ("city", "town", "village", "hamlet", "municipality"):
        if address.get(field):
            return address[field]
    return None


def _nominatim(query: str, kind: str) -> Optional[dict]:
    """One rate-limited Nominatim search. Raises on network failure, so the
    caller does not cache a miss that was really an outage."""
    global _last_request_at
    box = region_box()
    wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    params = {"q": query, "format": "jsonv2", "limit": 5, "addressdetails": 1}
    if box:
        south, west, north, east = box
        params.update(viewbox=f"{west},{north},{east},{south}", bounded=1)
    try:
        response = requests.get(NOMINATIM_URL, params=params, timeout=20,
                                headers={"User-Agent": config.USER_AGENT})
    finally:
        _last_request_at = time.monotonic()
    response.raise_for_status()

    for result in response.json():
        lat, lng = float(result["lat"]), float(result["lon"])
        if not in_region(lat, lng):
            continue
        category, kind_of = result.get("category"), result.get("type")
        if kind == "name" and (category in _NOT_A_VENUE or (category == "place" and kind_of in _TOWN_TYPES)):
            continue
        return {"lat": round(lat, 6), "lng": round(lng, 6), "city": _city_of(result.get("address") or {})}
    return None


def _geocode(venue_name: Optional[str], street_address: Optional[str], allow_network: bool) -> Optional[dict]:
    query, kind = _query_for(venue_name, street_address)
    if not query:
        return None
    key = f"{kind}:{_normalize(query)}"
    cache = _load_cache()
    if key in cache:
        return cache[key]
    if not (allow_network and network_enabled()):
        return None
    if region_box() is None:
        logger.warning("GEOCODER=nominatim needs region.map_center in calendar.config.yaml; not geocoding")
        return None
    try:
        place = _nominatim(query, kind)
    except Exception as e:
        logger.warning(f"Geocoding {query!r} failed, will retry next run: {e}")
        return None
    cache[key] = place  # None records a miss; delete the entry to retry it
    _save_cache()
    return place


# --------------------------------------------------------------------------- #
# What the rest of the code calls
# --------------------------------------------------------------------------- #

def lookup(venue_name: Optional[str] = None, street_address: Optional[str] = None,
           *, allow_network: bool = True) -> Optional[dict]:
    """{"lat", "lng", "city"} for a venue, any of which may be None, or None."""
    matched = _match_venue_key(venue_name)
    if matched:
        return load_venues()[matched]
    return _geocode(venue_name, street_address, allow_network)


def get_venue_coordinates(
    venue_name: Optional[str] = None,
    street_address: Optional[str] = None,
    *,
    allow_network: bool = True,
) -> Tuple[Optional[float], Optional[float]]:
    """(latitude, longitude) for a venue, or (None, None) if unknown."""
    place = lookup(venue_name, street_address, allow_network=allow_network)
    if place and place.get("lat") is not None:
        return place["lat"], place["lng"]
    return None, None


def _named_city(text: str) -> Optional[str]:
    """A known city written as an address's city: followed by a comma, a state
    code, a ZIP or the end. "San Jose Ave" is a street, not San Jose."""
    known = {c for c in config.CITIES if c}
    known |= {v["city"] for v in load_venues().values() if v.get("city")}
    for city in sorted(known, key=len, reverse=True):
        pattern = r"(?:^|[,\s])(?i:" + re.escape(city) + r")(?=\s*,|\s+[A-Z]{2}\b|\s+\d{5}\b|\s*$)"
        if re.search(pattern, text):
            return city
    return None


def get_venue_city(
    venue_name: Optional[str] = None,
    street_address: Optional[str] = None,
    *,
    allow_network: bool = True,
) -> Optional[str]:
    """The city a venue is in, so scrapers that leave `city` blank don't all
    get the region's default city.

    Resolution order:
      1. The venue's city in venues.yaml.
      2. A known city (config cities, venues.yaml cities) named in the address
         or venue name as an address's city.
      3. The city OpenStreetMap gave for it (cache, or network if enabled).
      4. None: the caller decides the default.
    """
    matched = _match_venue_key(venue_name)
    if matched and load_venues()[matched].get("city"):
        return load_venues()[matched]["city"]

    for text in (street_address, venue_name):
        city = _named_city(text or "")
        if city:
            return city

    if not matched:
        place = _geocode(venue_name, street_address, allow_network)
        if place and place.get("city"):
            return place["city"]
    return None


def add_coordinates_to_event(event_dict: dict) -> dict:
    """Fill latitude/longitude on an event dict that lacks them."""
    if event_dict.get("latitude") and event_dict.get("longitude"):
        return event_dict
    lat, lng = get_venue_coordinates(event_dict.get("venue_name"), event_dict.get("street_address"))
    if lat is not None and lng is not None:
        event_dict["latitude"] = lat
        event_dict["longitude"] = lng
    return event_dict
