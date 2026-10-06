"""Record a source's real traffic once; replay it in its tests forever.

    .venv/bin/python -m tests.sources.record "<Source Name>"
    .venv/bin/python -m tests.sources.record "<Source Name>" --force   # replace today's capture

This is `cal scrape "<Source Name>"` (the same output: the events, the
invariants, the shape) with every HTTP response the scraper reads saved,
gzipped, under tests/fixtures/<slug_>/<YYYY-MM-DD>/, beside a manifest.json
mapping each URL to its file. A test replays exactly that traffic, offline,
with the `serve` fixture in tests/sources/conftest.py:

    def test_reads_the_whole_listing(serve):
        serve("the_rockwell/2026-10-06")
        events = BY_NAME["The Rockwell"].load().run()

Why not `cal scrape --save-fixture`: it saves only the registry URL's page,
and most sources parse something else - an API, a feed, page 2. This keeps
all of it, from the same single live run. Every live run is another round of
requests to a venue that never asked to be scraped.

Captured: anything through requests (requests.get, a Session) and
urllib.request.urlopen. Not captured: a Playwright browser's traffic. For a
browser-driven source, save the rendered HTML and test the parse step on it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Tuple, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
MANIFEST = "manifest.json"

_DATELIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_TYPES = (("json", ".json"), ("calendar", ".ics"), ("xml", ".xml"), ("html", ".html"),
          ("javascript", ".js"), ("text", ".txt"))
_BY_EXTENSION = {".json": "application/json", ".ics": "text/calendar; charset=utf-8",
                 ".xml": "application/xml", ".html": "text/html; charset=utf-8",
                 ".htm": "text/html; charset=utf-8", ".js": "application/javascript",
                 ".txt": "text/plain; charset=utf-8"}


def url_key(url: str) -> str:
    """A URL with its query sorted and date-valued parameters masked.

    The fallback match for replay: a scraper that puts today's date in a query
    (`start_date=2026-10-06`) still finds the capture on a later day.
    """
    parts = urlsplit(url)
    query = sorted((k, "<date>" if _DATELIKE.match(v) else v)
                   for k, v in parse_qsl(parts.query, keep_blank_values=True))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def _extension(content_type: str) -> str:
    content_type = (content_type or "").lower()
    return next((ext for marker, ext in _TYPES if marker in content_type), ".bin")


def _resolve(path: Union[str, Path]) -> Path:
    path = Path(path)
    return path if path.is_absolute() else FIXTURES / path


def _read(path: Path) -> bytes:
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


# --------------------------------------------------------------------------- #
# record
# --------------------------------------------------------------------------- #

@contextmanager
def capturing(target: Path) -> Iterator[dict]:
    """Inside the block, every HTTP response is also written to `target`.

    Yields the manifest ({url: {file, status, content_type}}), which is
    written to target/manifest.json on exit. Identical bodies share a file.
    """
    import requests
    import urllib.request
    import urllib.response

    target.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    names: dict = {}

    def keep(url: str, body: bytes, content_type: str, status: int) -> None:
        digest = hashlib.sha1(body).hexdigest()
        if digest not in names:
            names[digest] = f"{len(names) + 1:02d}{_extension(content_type)}.gz"
            (target / names[digest]).write_bytes(gzip.compress(body))
        manifest[url] = {"file": names[digest], "status": status, "content_type": content_type}

    original_send = requests.Session.send
    original_urlopen = urllib.request.urlopen

    def send(self, request, **kwargs):
        response = original_send(self, request, **kwargs)
        # Redirect hops are skipped; the final response is kept under both the
        # URL that was asked for and the one that answered.
        if not response.is_redirect:
            keep(request.url, response.content, response.headers.get("Content-Type", ""),
                 response.status_code)
        return response

    def urlopen(url, *args, **kwargs):
        response = original_urlopen(url, *args, **kwargs)
        body = response.read()
        asked = url if isinstance(url, str) else url.full_url
        keep(asked, body, response.headers.get("Content-Type", ""), response.status)
        return urllib.response.addinfourl(io.BytesIO(body), response.headers, response.url, response.status)

    requests.Session.send = send
    urllib.request.urlopen = urlopen
    try:
        yield manifest
    finally:
        requests.Session.send = original_send
        urllib.request.urlopen = original_urlopen
        (target / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #

class _Routes:
    """Where each saved response is, and how to find it from a requested URL."""

    def __init__(self, capture: Union[str, Path, dict]):
        self.exact: dict = {}
        self.keyed: dict = {}
        self.substrings: list = []
        if isinstance(capture, dict):
            for fragment, file in capture.items():
                path = _resolve(file)
                kind = _BY_EXTENSION.get(Path(path.name.removesuffix(".gz")).suffix, "")
                self.substrings.append((fragment, (path, kind, 200)))
            self.substrings.sort(key=lambda route: -len(route[0]))
            return
        folder = _resolve(capture)
        manifest_path = folder / MANIFEST
        if not manifest_path.exists():
            raise FileNotFoundError(f"no capture at {manifest_path}: record one with "
                                    "`python -m tests.sources.record \"<Source Name>\"`")
        for url, entry in json.loads(manifest_path.read_text()).items():
            answer = (folder / entry["file"], entry.get("content_type", ""), entry.get("status", 200))
            self.exact[url] = answer
            self.keyed.setdefault(url_key(url), answer)

    def find(self, url: str) -> Tuple[bytes, str, int]:
        answer = self.exact.get(url) or self.keyed.get(url_key(url))
        if answer is None:
            answer = next((a for fragment, a in self.substrings if fragment in url), None)
        if answer is None:
            known = list(self.exact)[:8] or [fragment for fragment, _ in self.substrings]
            raise AssertionError(
                f"no saved response for {url}\n  saved: " + "\n         ".join(known)
                + "\nThe scraper asked for something the capture does not hold. Re-record, "
                  "or if the URL carries the clock, stop the scraper reading it.")
        path, content_type, status = answer
        return _read(path), content_type, status


def _requests_response(request, body: bytes, content_type: str, status: int):
    """A requests.Response as the real HTTP adapter would have built it."""
    import requests
    from requests.structures import CaseInsensitiveDict
    from requests.utils import get_encoding_from_headers

    response = requests.Response()
    response.status_code = status
    response.headers = CaseInsensitiveDict({"Content-Type": content_type} if content_type else {})
    response.encoding = get_encoding_from_headers(response.headers)
    response._content = body
    response._content_consumed = True
    response.url = request.url
    response.request = request
    response.reason = "OK" if status < 400 else "Replayed error"
    return response


@contextmanager
def replaying(capture: Union[str, Path, dict]) -> Iterator[None]:
    """Inside the block, HTTP requests are answered from saved files.

    `capture` is a folder written by `capturing` (relative to tests/fixtures/,
    or absolute), or a dict of {URL substring: saved file}. Anything it has no
    answer for raises, so a test can never reach the network.
    """
    import requests
    import urllib.error
    import urllib.request
    import urllib.response
    from email.message import Message

    routes = _Routes(capture)
    original_send = requests.Session.send
    original_urlopen = urllib.request.urlopen

    def send(self, request, **kwargs):
        return _requests_response(request, *routes.find(request.url))

    def urlopen(url, *args, **kwargs):
        asked = url if isinstance(url, str) else url.full_url
        body, content_type, status = routes.find(asked)
        headers = Message()
        if content_type:
            headers["Content-Type"] = content_type
        if status >= 400:
            raise urllib.error.HTTPError(asked, status, "Replayed error", headers, io.BytesIO(body))
        return urllib.response.addinfourl(io.BytesIO(body), headers, asked, status)

    requests.Session.send = send
    urllib.request.urlopen = urlopen
    try:
        yield
    finally:
        requests.Session.send = original_send
        urllib.request.urlopen = original_urlopen


# --------------------------------------------------------------------------- #

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.sources.record",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("source", help="the registry name, exactly")
    parser.add_argument("--force", action="store_true", help="replace an existing capture from today")
    args = parser.parse_args(argv)

    from src import cli
    from src.sources import BY_NAME

    source = BY_NAME.get(args.source)
    if source is None or not source.is_scraped:
        print(f"✗ {args.source!r} is not a registered, active source. Try: cal sources")
        return 2
    if source.kind == "playwright":
        print("! a browser-driven source: its traffic goes through the browser and will not be captured")

    folder = source.slug.replace("-", "_")
    target = FIXTURES / folder / f"{datetime.now():%Y-%m-%d}"
    if target.exists() and any(target.iterdir()) and not args.force:
        print(f"✗ {target.relative_to(FIXTURES.parent.parent)} already exists; pass --force to replace it")
        return 2
    if target.exists():
        for old in target.iterdir():
            old.unlink()

    with capturing(target) as manifest:
        status = cli.main(["scrape", args.source])

    size = sum(p.stat().st_size for p in target.iterdir())
    print(f"\n✓ captured {len(manifest)} response(s), {size / 1024:.0f} KB gzipped, in "
          f"{target.relative_to(FIXTURES.parent.parent)}/")
    if size > 2 * 1024 * 1024:
        print("! that is large for a repository; consider a shorter capture (an adapter's `days` "
              "param, if it has one)")
    print(f'  replay it in a test with:  serve("{folder}/{target.name}")')
    return status


if __name__ == "__main__":
    sys.exit(main())
