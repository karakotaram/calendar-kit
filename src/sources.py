"""The source registry: layer 0 of the system.

Every source is one small YAML file in registry/. `scrape.py` runs what is
listed there, the CI monitor watches it, and the `cal` CLI enumerates it.
Adding a source means adding one file; nothing else keeps a copy of the list.

Why one file per source rather than one list: onboarding runs in parallel
(`/onboard` in Claude Code), and two workers appending to one file conflict.
Why one registry at all: Cambridge Calendar once kept the same list in two
places, they drifted, and four scrapers ran daily with no monitoring.

    # registry/the-rockwell.yaml
    name: The Rockwell                 # must equal the source_name its events carry
    adapter: tribe                     # a platform adapter (src/adapters/), or `custom`
    url: https://therockwell.org       # the venue's site or calendar page
    params: {}                         # adapter options, if any
    venue:                             # defaults for events that name no place
      name: The Rockwell
      street: 255 Elm St
      city: Somerville
      zip: "02144"
    runs_in_ci: true                   # false if the venue blocks GitHub's IP ranges
    status: active                     # active | blocked | retired (not run; kept as a record)
    notes: why anything above is unusual

A custom scraper names its code instead of an adapter:

    adapter: custom
    module: src.scrapers.custom.the_rockwell
    class: RockwellScraper

Scraper classes are imported lazily so that listing sources stays fast and does
not pull in Playwright.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal, Optional

import yaml

from src.config import ROOT

REGISTRY_DIR = ROOT / "registry"

# How a source is fetched. Also the run order: plain HTTP first so that the
# browser-driven scrapers start after the cheap ones have finished, and
# aggregators last so original sources win deduplication.
Kind = Literal["requests", "playwright", "aggregator", "manual"]

KIND_ORDER: dict[str, int] = {
    "requests": 0,
    "playwright": 1,
    "aggregator": 2,
    "manual": 3,
}

STATUSES = ("active", "blocked", "retired")


@dataclass(frozen=True)
class Source:
    """One event source. See the module docstring for the YAML it comes from."""

    name: str
    kind: Kind
    adapter: Optional[str] = None
    url: Optional[str] = None
    params: dict = field(default_factory=dict)
    venue: dict = field(default_factory=dict)
    module: Optional[str] = None
    cls: Optional[str] = None
    runs_in_ci: bool = True
    status: str = "active"
    notes: str = ""
    slug: str = ""

    def __hash__(self):
        return hash(self.name)

    @property
    def is_scraped(self) -> bool:
        """False for sources fed by something other than the scrape pipeline,
        and for sources documented as blocked or retired."""
        return self.status == "active" and self.kind != "manual" and bool(self.adapter or self.cls)

    def load(self):
        """Instantiate the scraper. Raises for non-scraped sources."""
        if self.kind == "manual" or not (self.adapter or self.cls):
            raise ValueError(f"{self.name} has no scraper (kind={self.kind}): {self.notes}")
        if self.adapter and self.adapter != "custom":
            from src import adapters
            return adapters.get(self.adapter).cls(
                source_name=self.name, url=self.url, venue=self.venue, **self.params)
        return getattr(importlib.import_module(self.module), self.cls)()


# Reader submissions arrive through sync_user_events.py, not a scraper, and
# are always preserved by scrape.py rather than re-collected.
USER_SUBMITTED = Source(
    name="User Submitted", kind="manual", slug="user-submitted",
    notes="fed by sync_user_events.py from the submissions sheet; always "
          "preserved by scrape.py rather than re-collected")


def _from_yaml(path: Path) -> Source:
    data = yaml.safe_load(path.read_text()) or {}
    if not data.get("name"):
        raise ValueError(f"{path.name}: missing `name`")
    adapter = data.get("adapter")
    if adapter == "custom" and not (data.get("module") and data.get("class")):
        raise ValueError(f"{path.name}: a custom source needs `module` and `class`")
    status = data.get("status", "active")
    if status not in STATUSES:
        raise ValueError(f"{path.name}: status must be one of {STATUSES}")

    kind = data.get("kind")
    if not kind:
        if adapter and adapter != "custom":
            from src import adapters
            kind = adapters.get(adapter).kind
        else:
            kind = "requests"
    if kind not in KIND_ORDER:
        raise ValueError(f"{path.name}: kind must be one of {tuple(KIND_ORDER)}")

    return Source(
        name=data["name"], kind=kind, adapter=adapter, url=data.get("url"),
        params=data.get("params") or {}, venue=data.get("venue") or {},
        module=data.get("module"), cls=data.get("class"),
        runs_in_ci=bool(data.get("runs_in_ci", True)), status=status,
        notes=(data.get("notes") or "").strip(), slug=path.stem,
    )


def load_registry(directory: Path = REGISTRY_DIR) -> tuple[Source, ...]:
    files = sorted(p for p in directory.glob("*.yaml") if not p.name.startswith("_"))
    sources = [_from_yaml(p) for p in files]
    names = [s.name for s in sources]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"source names must be unique; repeated: {sorted(dupes)}")
    return tuple(sources) + (USER_SUBMITTED,)


SOURCES: tuple[Source, ...] = load_registry()
BY_NAME: dict[str, Source] = {s.name: s for s in SOURCES}


def in_run_order(*, is_ci: bool = False) -> Iterator[Source]:
    """Scraped sources, cheapest transport first, aggregators last.

    In CI, sources that block GitHub's IP ranges are omitted; scrape.py preserves
    their existing events instead of dropping them.
    """
    runnable = [s for s in SOURCES if s.is_scraped and (s.runs_in_ci or not is_ci)]
    return iter(sorted(runnable, key=lambda s: KIND_ORDER[s.kind]))


def skipped_in_ci() -> list[str]:
    """Names whose events CI must preserve rather than re-scrape."""
    return [s.name for s in SOURCES if s.is_scraped and not s.runs_in_ci]


def preserved_always() -> list[str]:
    """Names never produced by the scrape pipeline; their events must survive."""
    return [s.name for s in SOURCES if s.kind == "manual"]
