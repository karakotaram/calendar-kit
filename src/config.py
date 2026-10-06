"""This calendar's settings: everything that makes the deployment yours.

Read once from calendar.config.yaml at the repo root (or the file named by the
CALENDAR_CONFIG environment variable). Code that needs a region-specific fact -
the time zone, the cities, the site's name - asks here instead of hard-coding
it. Cambridge Calendar, which this kit is built from, had "Eastern" written
into the model, the validator, the API and the page builder.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytz
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("CALENDAR_CONFIG", ROOT / "calendar.config.yaml"))


def _load(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    for section in ("site", "region", "features", "submissions", "brand", "api"):
        data.setdefault(section, {})
    return data


RAW = _load(CONFIG_PATH)
_site, _region, _features = RAW["site"], RAW["region"], RAW["features"]

SITE_NAME: str = _site.get("name") or "Community Calendar"
SHORT_NAME: str = re.sub(r"[^A-Za-z0-9]", "", _site.get("short_name") or SITE_NAME) or "CommunityCalendar"
TAGLINE: str = _site.get("tagline") or ""
SITE_URL: str = (_site.get("url") or "").rstrip("/")
CONTACT_EMAIL: str = _site.get("contact_email") or ""

REGION_NAME: str = _region.get("name") or ""
TIMEZONE_NAME: str = _region.get("timezone") or "UTC"
TZ = pytz.timezone(TIMEZONE_NAME)
STATE: str = _region.get("state") or ""
DEFAULT_CITY: str = _region.get("default_city") or ""
CITIES: list = list(_region.get("cities") or [])

FEATURES: dict = {
    "map": bool(_features.get("map", True)),
    "chat": bool(_features.get("chat", False)),
    "submissions": bool(_features.get("submissions", True)),
    "editors_picks": bool(_features.get("editors_picks", True)),
}

SUBMISSIONS_SHEET_ID: str = RAW["submissions"].get("sheet_id") or ""

# Honest and the same everywhere: never claim to be an ordinary browser
# (docs/rules-and-edge-cases.md, "Identify honestly").
_reach = SITE_URL or (f"mailto:{CONTACT_EMAIL}" if CONTACT_EMAIL else "")
USER_AGENT: str = f"{SHORT_NAME}/1.0" + (f" (+{_reach})" if _reach else "")
