"""The documentation and the registry must not be allowed to rot into traps.

Every claim these tests check was, at some point in Cambridge Calendar's life,
a sentence that had quietly stopped being true: a `pytest tests/` command with
no test directory, an endpoint that returned 500, an event count off by a
factor of four, a monitoring list four scrapers behind reality, a 185-line
scraper that nothing ever ran.

Prose that nothing verifies decays. These are the checks that keep the
CLAUDE.md map, the skills and the registry honest.
"""
import importlib
import inspect
import pkgutil
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".git", ".pytest_cache", "node_modules", "dist", "build"}


def markdown_files():
    return [p for p in REPO.rglob("*.md") if not SKIP_DIRS.intersection(p.relative_to(REPO).parts)]


def test_all_markdown_links_resolve():
    """CLAUDE.md, the README and the skills are maps; a map with dead links
    sends the reader, usually Claude, searching instead of reading."""
    broken = []
    for doc in markdown_files():
        for match in re.finditer(r"\[[^\]]+\]\(([^)#\s][^)\s]*?)(#[^)]*)?\)", doc.read_text()):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (doc.parent / target).resolve().exists():
                broken.append(f"{doc.relative_to(REPO)} -> {target}")
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)


def test_every_registry_file_parses_and_explains_itself():
    """A registry file that does not parse stops every scrape at import. A
    blocked or retired source with no notes is a mystery a year later: why is
    it off, and should anyone try again? registry/_example.yaml is checked too,
    so the example stays one that works."""
    from src.sources import REGISTRY_DIR, _from_yaml

    problems = []
    for path in sorted(REGISTRY_DIR.glob("*.yaml")):
        try:
            source = _from_yaml(path)
        except Exception as e:                       # report every file, not the first
            problems.append(f"{path.name}: {e}")
            continue
        if source.status in ("blocked", "retired") and not source.notes:
            problems.append(f"{path.name}: status {source.status} but no notes saying why")
        if not path.name.startswith("_") and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", path.stem):
            problems.append(f"{path.name}: name the file <kebab-case-slug>.yaml")
    assert not problems, "\n  ".join(["registry problems:"] + problems)


def test_every_custom_scraper_module_is_registered():
    """A scraper on disk that no registry file names is dead code.

    Cambridge's Longfellow House scraper was exactly this for months: 185 lines
    of working Playwright code that nothing ever called, producing zero events
    and raising no alarm. Files starting with "_" (the template) are exempt.
    """
    from src.sources import SOURCES

    custom = REPO / "src" / "scrapers" / "custom"
    on_disk = {p.stem for p in custom.glob("*.py") if not p.name.startswith("_")}
    registered = {s.module.rsplit(".", 1)[-1] for s in SOURCES
                  if s.adapter == "custom" and s.module and s.module.startswith("src.scrapers.custom.")}
    unregistered = on_disk - registered
    assert not unregistered, (
        f"custom scrapers no registry file names: {sorted(unregistered)}. "
        "Register each with `cal add --adapter custom`, or delete it.")
    assert not any((s.module or "").endswith("._template") for s in SOURCES), \
        "the template is copied, never registered"


def test_every_adapter_declares_itself():
    """`cal detect`, `cal adapters` and the registry loader read these five
    names from every adapter module; one missing is a crash or a silent miss."""
    from src import adapters
    from src.sources import KIND_ORDER

    problems = []
    for info in pkgutil.iter_modules(adapters.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"src.adapters.{info.name}")
        missing = [n for n in ("ADAPTER", "KIND", "CLASS", "PARAMS", "SIGNATURES") if not hasattr(module, n)]
        if missing:
            problems.append(f"{info.name}: missing {', '.join(missing)}")
            continue
        if module.KIND not in KIND_ORDER:
            problems.append(f"{info.name}: KIND {module.KIND!r} is not one of {tuple(KIND_ORDER)}")
        if not inspect.isclass(getattr(module, module.CLASS, None)):
            problems.append(f"{info.name}: CLASS {module.CLASS!r} is not a class in the module")
        if not isinstance(module.PARAMS, dict):
            problems.append(f"{info.name}: PARAMS must be a dict of name -> meaning")
        if not isinstance(module.SIGNATURES, (list, tuple)):
            problems.append(f"{info.name}: SIGNATURES must be a list of strings")
    assert not problems, "\n  ".join(["adapter problems:"] + problems)


def test_registry_names_match_what_scrapers_emit():
    """A registry name that disagrees with its scraper's source_name silently
    unmonitors that source: events land under one name, monitoring watches another."""
    from src.sources import SOURCES

    mismatched = []
    for source in SOURCES:
        if not source.is_scraped:
            continue
        emitted = source.load().source_name
        if emitted != source.name:
            mismatched.append(f"{source.name!r} registered but its scraper emits {emitted!r}")
    assert not mismatched, "\n  ".join(["registry/scraper name mismatch:"] + mismatched)


def test_monitoring_covers_every_registered_source():
    """ci_monitor must see everything the pipeline runs.

    In Cambridge it once did not: its list was a second hand-kept copy and had
    drifted four scrapers behind, leaving them unmonitored. Both now derive
    from the registry, and this test is what keeps it that way.
    """
    from src.agents.ci_monitor import REGISTERED_SOURCES
    from src.sources import SOURCES

    unmonitored = {s.name for s in SOURCES} - set(REGISTERED_SOURCES)
    assert not unmonitored, f"sources with no freshness monitoring: {sorted(unmonitored)}"


@pytest.mark.parametrize("check", ["clock_stamped", "tz_aware", "timestamp_pileup"])
def test_stored_data_satisfies_documented_invariants(check):
    """The absolute invariants (src/quality/invariants.py), enforced against the
    published data. They need no per-source baseline and hold for every event."""
    import json

    events = json.loads((REPO / "data" / "events.json").read_text())
    if not events:
        pytest.skip("nothing published yet")

    if check == "clock_stamped":
        # Real listings are on the minute. Sub-minute precision only comes from a
        # clock reading, which is what put 117 Cambridge events on one day.
        bad = [e for e in events if re.search(r"T\d{2}:\d{2}:(?!00)", e["start_datetime"])]
        assert not bad, f"{len(bad)} events carry a scrape timestamp, e.g. {bad[0]['start_datetime']}"

    elif check == "tz_aware":
        # Everything is naive local wall clock; mixing raises TypeError on comparison.
        aware = re.compile(r"([+-]\d{2}:?\d{2}|Z)$")
        bad = [e[f] for e in events for f in ("start_datetime", "end_datetime")
               if isinstance(e.get(f), str) and aware.search(e[f])]
        assert not bad, f"{len(bad)} tz-aware datetimes, e.g. {bad[0]}"

    elif check == "timestamp_pileup":
        # 8 was the highest legitimate per-source value Cambridge observed; 117 was the bug.
        from collections import Counter
        (source, when), count = Counter((e["source_name"], e["start_datetime"]) for e in events).most_common(1)[0]
        assert count <= 20, f"{count} events from {source} all start at {when} - date fallback?"
