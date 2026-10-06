"""Platform adapters: one reader per website platform, configured per venue.

Most venue websites run on a handful of platforms (The Events Calendar for
WordPress, Squarespace, Localist, IndieCommerce, Assabet, EventON, iCal feeds,
schema.org JSON-LD), and each platform publishes its events the same way for
every venue on it. An adapter is written once and reused by a registry row.

Each module here defines, at module level:

    ADAPTER = "tribe"            # the name a registry file uses
    KIND = "requests"            # or "playwright"
    CLASS = "TribeEventsAdapter" # the scraper class in the module
    SIGNATURES = [...]           # strings or compiled regexes `cal detect` looks for in a page
    PARAMS = {...}               # the registry `params` it accepts, name -> meaning

The class's constructor takes `source_name`, `url`, `venue` (a dict of
defaults: name, street, city, zip) and the adapter's own params as keyword
arguments.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    kind: str
    cls: type
    module: str
    signatures: tuple = ()
    params: dict = field(default_factory=dict)
    summary: str = ""


@lru_cache(maxsize=None)
def available() -> dict[str, AdapterInfo]:
    found = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        name = getattr(module, "ADAPTER", None)
        if not name:
            continue
        if name in found:
            raise ValueError(f"adapter name {name!r} is defined twice")
        doc = (module.__doc__ or "").strip()
        found[name] = AdapterInfo(
            name=name,
            kind=getattr(module, "KIND", "requests"),
            cls=getattr(module, getattr(module, "CLASS", "Adapter")),
            module=module.__name__,
            signatures=tuple(getattr(module, "SIGNATURES", ())),
            params=dict(getattr(module, "PARAMS", {})),
            summary=doc.splitlines()[0] if doc else "",
        )
    return found


def get(name: str) -> AdapterInfo:
    adapters = available()
    if name not in adapters:
        raise ValueError(f"unknown adapter {name!r}; available: {sorted(adapters)}")
    return adapters[name]


def names() -> list[str]:
    return sorted(available())


def find(name: str) -> Optional[AdapterInfo]:
    return available().get(name)
