"""`cal detect <url>`: which platform adapter, if any, fits a venue's page.

Onboarding a venue starts here. The page is fetched once with the calendar's
honest user-agent, then:

1. **Blocked?** A 401/403/429, or a bot-check interstitial ("Just a
   moment..."), returns a single `Match(adapter="blocked")`. Onboarding marks
   such a source blocked rather than writing a scraper against an interstitial
   - a parser built on a challenge page parses nothing, forever, silently.
2. **Signatures.** Every adapter module declares `SIGNATURES`, strings its
   platform leaves in a page (`src/adapters/__init__.py`). The share of an
   adapter's signatures found in the page (strings case-insensitively; a
   compiled regex by search) is its starting evidence. Adapters are discovered, not listed here, so a new adapter module
   takes part without editing this file.
3. **Probes.** Signatures are hints; probes confirm. Most cost nothing because
   the proof is already in the page (an EventON calendar's settings, an
   IndieCommerce FullCalendar block, Assabet's generator tag, JSON-LD Event
   blocks, `.ics` links, Drupal `<time>` rows that agree with themselves).
   Others take one request (the Tribe REST endpoint answering JSON, a
   Squarespace `?format=json`, the Localist API, an IndieCommerce calendar or
   Assabet listing the page only links to). Those are spent on the likeliest
   candidates first, and the whole detection stays within MAX_REQUESTS.

Scores are 0-1. A confirmed platform adapter scores at least 0.8; a confirmed
*generic* reader (JSON-LD, iCal, a Drupal listing) at most 0.75, because a
Tribe or EventON site also carries JSON-LD and `.ics` links and the platform
adapter reads it better. Unconfirmed matches score by signatures alone, lower.

`fetch` is injectable so the tests run offline: a callable taking a URL and
returning `(status, text, final_url)`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from src.config import USER_AGENT
from src.scrapers.base_playwright_scraper import CHALLENGE_TITLES

logger = logging.getLogger(__name__)

Fetch = Callable[[str], Tuple[int, str, str]]

# One page fetch plus at most four probe requests per URL
MAX_REQUESTS = 5

# Readers that work on any site carrying their format. A platform adapter that
# also fits is the better reader, so these rank below any confirmed platform.
GENERIC = {"jsonld", "ical", "drupal_listing"}

BLOCKED_STATUSES = {401, 403, 429}

MIN_SCORE = 0.2


class DetectError(RuntimeError):
    """The page could not be read at all (not a block: a 404, a 500, no answer)."""


@dataclass
class Match:
    adapter: str
    score: float
    evidence: str
    suggested_url: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def http_fetch(url: str) -> Tuple[int, str, str]:
    """GET with the calendar's own user-agent. Never an impersonation of a browser."""
    import requests
    response = requests.get(url, timeout=20, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return response.status_code, response.text, response.url


# --------------------------------------------------------------------------- #
# The page and the request budget
# --------------------------------------------------------------------------- #

class _Page:
    def __init__(self, url: str, status: int, text: str, final_url: str):
        self.url = url
        self.status = status
        self.text = text or ""
        self.lower = self.text.lower()
        self.final_url = final_url or url
        parts = urlsplit(self.final_url)
        self.root = f"{parts.scheme}://{parts.netloc}"
        self.host = parts.netloc.lower()
        self.path = parts.path
        self._soup = None

    @property
    def soup(self) -> BeautifulSoup:
        if self._soup is None:
            self._soup = BeautifulSoup(self.text, "html.parser")
        return self._soup

    @property
    def title(self) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", self.text, re.IGNORECASE | re.DOTALL)
        return " ".join(match.group(1).split()) if match else ""


def _challenged(status: int, text: str) -> Optional[str]:
    """Why a response is a refusal or a bot check, or None if it is a page."""
    match = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.IGNORECASE | re.DOTALL)
    title = " ".join(match.group(1).split()) if match else ""
    if any(marker in title.lower() for marker in CHALLENGE_TITLES):
        return f"HTTP {status}, bot-check page titled {title!r}"
    if status in BLOCKED_STATUSES:
        return f"HTTP {status}" + (f" ({title!r})" if title else "")
    return None


class _Budget:
    """Probe requests, counted against MAX_REQUESTS (the page fetch is the first)."""

    def __init__(self, fetch: Fetch, limit: int, used: int = 1):
        self.fetch, self.limit, self.used = fetch, limit, used

    def left(self) -> int:
        return max(0, self.limit - self.used)

    def get(self, url: str) -> Optional[Tuple[int, str, str]]:
        if not self.left():
            return None
        self.used += 1
        try:
            return self.fetch(url)
        except Exception as e:      # a probe that cannot connect is a "no", not a crash
            logger.info(f"probe {url} failed: {e}")
            return 0, "", url


@dataclass
class _Finding:
    status: str                 # confirmed | refuted | hint | none
    evidence: str = ""
    suggested_url: Optional[str] = None


NONE = _Finding("none")


def _json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _with_query(url: str, **params) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in params]
    query += list(params.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def _refused_probe(response, url: str) -> Optional[_Finding]:
    status, text, _ = response
    why = _challenged(status, text)
    if why:
        return _Finding("hint", f"the probe of {url} was refused ({why})")
    return None


# --------------------------------------------------------------------------- #
# Probes, keyed by adapter name. Each returns a _Finding; with no budget left it
# reports what the page alone shows.
# --------------------------------------------------------------------------- #

def _probe_tribe(page: _Page, budget: _Budget) -> _Finding:
    hinted = any(s in page.lower for s in ("tribe-events", "tribe_events", "the-events-calendar",
                                           "tribe/events/v1", "tribe\\/events\\/v1"))
    if not hinted:
        return NONE
    if not budget.left():
        return _Finding("hint", "The Events Calendar markup on the page (REST endpoint not checked)")
    api = page.soup.find("link", rel=lambda r: r and "https://api.w.org/" in r, href=True)
    rest_root = api["href"] if api else f"{page.root}/wp-json/"
    url = urljoin(rest_root if rest_root.endswith("/") else rest_root + "/", "tribe/events/v1/events?per_page=1")
    response = budget.get(url)
    refused = _refused_probe(response, url)
    if refused:
        return refused
    status, text, _ = response
    data = _json(text)
    if status == 200 and isinstance(data, dict) and isinstance(data.get("events"), list):
        total = data.get("total", len(data["events"]))
        return _Finding("confirmed", f"Tribe REST API answers JSON at {url} ({total} events)")
    return _Finding("refuted", f"Tribe REST API did not answer JSON at {url} (HTTP {status})")


def _probe_squarespace(page: _Page, budget: _Budget) -> _Finding:
    if "squarespace" not in page.lower:
        return NONE
    if not budget.left():
        return _Finding("hint", "a Squarespace site (?format=json not checked)")
    url = _with_query(page.final_url, format="json")
    response = budget.get(url)
    refused = _refused_probe(response, url)
    if refused:
        return refused
    status, text, _ = response
    data = _json(text)
    if status != 200 or not isinstance(data, dict):
        return _Finding("refuted", f"?format=json did not answer JSON (HTTP {status})")
    collection = data.get("collection") or {}
    if "upcoming" in data or "past" in data or "event" in str(collection.get("typeName", "")):
        return _Finding("confirmed", f"?format=json is an events collection "
                                     f"({len(data.get('upcoming') or [])} upcoming, {len(data.get('past') or [])} past)")
    return _Finding("hint", "a Squarespace site, but this page is not an events collection "
                            "- find the events page and detect that")


def _probe_localist(page: _Page, budget: _Budget) -> _Finding:
    if "localist" not in page.lower:
        return NONE
    if not budget.left():
        return _Finding("hint", "Localist named on the page (API not checked)")
    url = f"{page.root}/api/2/events?pp=1"
    response = budget.get(url)
    refused = _refused_probe(response, url)
    if refused:
        return refused
    status, text, _ = response
    data = _json(text)
    if status == 200 and isinstance(data, dict) and isinstance(data.get("events"), list):
        total = (data.get("page") or {}).get("total_items")
        return _Finding("confirmed", f"Localist API answers JSON at {url}"
                                     + (f" ({total} events)" if total is not None else ""))
    return _Finding("refuted", f"no Localist API at {url} (HTTP {status})")


def _probe_ical(page: _Page, budget: _Budget) -> _Finding:
    feeds = []
    for node in page.soup.find_all(["a", "link"], href=True):
        href = node["href"].strip()
        low = href.lower()
        if (low.startswith("webcal:") or re.search(r"\.ics(\?|$)", low) or "ical=1" in low
                or (node.name == "link" and (node.get("type") or "").lower() == "text/calendar")):
            if "calendar.google.com/calendar/render" in low:
                continue            # an "add to Google" button, not a feed
            feeds.append("https:" + href[len("webcal:"):] if low.startswith("webcal:") else urljoin(page.final_url, href))
    for frame in page.soup.find_all("iframe", src=True):
        src = frame["src"]
        if "google.com/calendar/embed" in src or "calendar.google.com/calendar/embed" in src:
            for calendar_id in parse_qs(urlsplit(src).query).get("src", []):
                feeds.append(f"https://calendar.google.com/calendar/ical/{quote(calendar_id)}/public/basic.ics")
    feeds = list(dict.fromkeys(feeds))
    if not feeds:
        return NONE
    return _Finding("confirmed", f"{len(feeds)} iCalendar feed(s) linked or embedded, e.g. {feeds[0]}", feeds[0])


def _jsonld_events(page: _Page) -> int:
    count = 0
    for script in page.soup.find_all("script", type="application/ld+json"):
        data = _json(script.string or "")
        if data is None:
            try:
                data = json.loads(script.string or "", strict=False)
            except (ValueError, TypeError):
                continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                kind = node.get("@type")
                kinds = kind if isinstance(kind, list) else [kind]
                if any(isinstance(k, str) and k.endswith("Event") for k in kinds):
                    count += 1
                if "@graph" in node:
                    stack.append(node["@graph"])
                if isinstance(node.get("itemListElement"), list):
                    stack.extend(i.get("item", i) if isinstance(i, dict) else i for i in node["itemListElement"])
    return count


def _probe_jsonld(page: _Page, budget: _Budget) -> _Finding:
    count = _jsonld_events(page)
    if not count:
        return NONE
    return _Finding("confirmed", f"{count} schema.org Event block(s) in the page's JSON-LD")


def _probe_indiecommerce(page: _Page, budget: _Budget) -> _Finding:
    from src.adapters.indiecommerce import IndieCommerceAdapter as Reader
    calendar = f"{page.root}/events/calendar"
    if "fullcalendarview" in page.lower and "indiecommerce" in page.lower:
        items = Reader.calendar_items(page.text)
        if items is not None:
            view = Reader.settings(page.text).get("indiecommerce_events", {}).get("calander_view", "?")
            return _Finding("confirmed", f"IndieCommerce month calendar ({view}) with {len(items)} events "
                                         "in its FullCalendar settings", calendar)
    if "indiecommerce" not in page.lower:
        return NONE
    if not budget.left():
        return _Finding("hint", "an IndieCommerce store (its calendar not checked)", calendar)
    response = budget.get(calendar)
    refused = _refused_probe(response, calendar)
    if refused:
        refused.suggested_url = calendar
        return refused
    status, text, final = response
    items = Reader.calendar_items(text) if status == 200 else None
    if items is None:
        return _Finding("refuted", f"an IndieCommerce store, but {calendar} carries no calendar (HTTP {status})")
    return _Finding("confirmed", f"IndieCommerce calendar at {calendar} with {len(items)} events this month",
                    calendar)


def _assabet_listing(host_url: str) -> str:
    """The undated listing, which redirects to the current month. Never a
    month's own URL: an adapter started there would read that month forever."""
    parts = urlsplit(host_url)
    return f"{parts.scheme or 'https'}://{parts.netloc}/calendar/event-listing/"


def _probe_assabet(page: _Page, budget: _Budget) -> _Finding:
    generator = page.soup.find("meta", attrs={"name": "generator"})
    on_assabet = (page.host.endswith("assabetinteractive.com")
                  or "assabet" in (generator.get("content", "") if generator else "").lower())
    if on_assabet:
        cards = len(page.soup.select("div.listing-event"))
        return _Finding("confirmed", f"an Assabet Interactive calendar ({cards} listing cards on this page)",
                        _assabet_listing(page.final_url))

    hosts = re.findall(r"https?://[a-z0-9-]+\.assabetinteractive\.com", page.lower)
    if not hosts:
        return NONE
    listing = _assabet_listing(hosts[0])
    if not budget.left():
        return _Finding("hint", "links to an Assabet Interactive calendar (not checked)", listing)
    response = budget.get(listing)
    refused = _refused_probe(response, listing)
    if refused:
        refused.suggested_url = listing
        return refused
    status, text, _ = response
    if status == 200 and ("assabet interactive" in text.lower() or "listing-event" in text):
        return _Finding("confirmed", f"embeds or links an Assabet Interactive calendar, read at {listing}",
                        listing)
    return _Finding("refuted", f"links to Assabet, but {listing} is not its listing (HTTP {status})")


def _probe_eventon(page: _Page, budget: _Budget) -> _Finding:
    if "evo_general_params" in page.text:
        from src.adapters.eventon import EventONAdapter as Reader
        try:
            _, sc = Reader.calendar_state(page.text)
        except (ValueError, TypeError):
            sc = None
        if sc:
            kind = sc.get("calendar_type") or "?"
            return _Finding("confirmed", f"an EventON calendar on the page (calendar_type {kind}), "
                                         "readable through its AJAX endpoint")
    if "eventon" in page.lower:
        return _Finding("hint", "EventON's assets are loaded, but no calendar is on this page "
                                "- find the calendar page and detect that")
    return NONE


def _probe_drupal_listing(page: _Page, budget: _Budget) -> _Finding:
    drupal = "drupal-settings-json" in page.lower or 'content="drupal' in page.lower
    if not drupal or "<time" not in page.lower:
        return NONE
    from src.adapters.drupal_listing import DrupalListingAdapter as Reader
    reader = Reader("detect", page.final_url, detail_pages=False)
    rows = [row for row in page.soup.select(reader.sel["row_selector"]) if row.select_one("time[datetime]")]
    if not rows:
        return NONE
    agreeing = sum(1 for row in rows
                   if Reader.read_time(row.select_one("time[datetime]"), quiet=True)[0] is not None)
    if not agreeing:
        from src import config
        return _Finding("hint", f"a Drupal View with {len(rows)} dated rows, but no <time> attribute "
                                f"agrees with its visible text in {config.TIMEZONE_NAME} - a venue in "
                                "another time zone, or a date format the adapter does not read")
    paged = " with a pager" if page.soup.select_one(reader.sel["next_selector"]) else ""
    return _Finding("confirmed", f"a Drupal View{paged}: {agreeing} of {len(rows)} rows carry a <time> "
                                 "whose UTC attribute and visible text agree")


PROBES: Dict[str, Callable[[_Page, _Budget], _Finding]] = {
    "tribe": _probe_tribe,
    "squarespace": _probe_squarespace,
    "localist": _probe_localist,
    "ical": _probe_ical,
    "jsonld": _probe_jsonld,
    "indiecommerce": _probe_indiecommerce,
    "assabet": _probe_assabet,
    "eventon": _probe_eventon,
    "drupal_listing": _probe_drupal_listing,
}


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

def _present(signature, page: _Page) -> bool:
    """A signature is a string (found case-insensitively) or a compiled regex."""
    if hasattr(signature, "search"):
        return bool(signature.search(page.text))
    return str(signature).lower() in page.lower


def _label(signature) -> str:
    return getattr(signature, "pattern", None) or str(signature)


def _score(name: str, signature_share: float, finding: _Finding) -> float:
    if finding.status == "confirmed":
        return (0.55 if name in GENERIC else 0.8) + 0.2 * signature_share
    if finding.status == "refuted":
        return 0.2 * signature_share
    if finding.status == "hint":
        return 0.3 + 0.3 * signature_share
    return 0.5 * signature_share


def detect(url: str, fetch: Optional[Fetch] = None) -> List[Match]:
    """Matches for the page at `url`, best first. See the module docstring."""
    from src import adapters

    fetch = fetch or http_fetch
    try:
        status, text, final_url = fetch(url)
    except Exception as e:
        raise DetectError(f"could not fetch {url}: {e}") from e
    page = _Page(url, status, text, final_url)

    why = _challenged(page.status, page.text)
    if why:
        return [Match("blocked", 1.0, f"{why} at {page.final_url}; do not write a scraper against it "
                                      "- register the source as blocked, or ask the venue")]
    if status >= 400:
        raise DetectError(f"HTTP {status} from {page.final_url}; check the URL")

    found = adapters.available()
    shares, hits = {}, {}
    for name, info in found.items():
        signatures = [s for s in info.signatures if s]
        hit = [_label(s) for s in signatures if _present(s, page)]
        hits[name] = hit
        shares[name] = len(hit) / len(signatures) if signatures else 0.0

    # Free pass: what the page alone proves
    findings = {name: PROBES[name](page, _Budget(fetch, 0, 0)) if name in PROBES else NONE
                for name in found}

    # Paid pass: spend the remaining requests on the likeliest unconfirmed candidates
    budget = _Budget(fetch, MAX_REQUESTS)
    pending = [name for name in found
               if name in PROBES and findings[name].status == "hint" and budget.left()]
    pending.sort(key=lambda n: (-shares[n], n))
    for name in pending:
        if not budget.left():
            break
        findings[name] = PROBES[name](page, budget)

    matches = []
    for name in found:
        finding = findings[name]
        score = round(min(1.0, _score(name, shares[name], finding)), 2)
        if score < MIN_SCORE:
            continue
        parts = []
        if hits[name]:
            parts.append(f"signatures {len(hits[name])}/{len(found[name].signatures)}: {', '.join(hits[name])}")
        if finding.evidence:
            parts.append(finding.evidence)
        suggested = finding.suggested_url or (page.final_url if page.final_url != url else None)
        matches.append(Match(name, score, "; ".join(parts) or "signatures only", suggested))

    matches.sort(key=lambda m: (-m.score, m.adapter in GENERIC, m.adapter))
    return matches
