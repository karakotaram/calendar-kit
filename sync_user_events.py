#!/usr/bin/env python3
"""Publish reader submissions from the Google Form's response sheet.

    python sync_user_events.py

1. Reads rows not yet marked uploaded (src/scrapers/google_sheets.py documents
   the columns, the credentials and the optional approval step).
2. Cleans and validates them like scraped events, and deduplicates them.
3. Adds each one to data/events.json as "User Submitted" - unless a stored
   submission already has the same title on the same date. That rule is the
   guard against double-publishing when a run saved the events but failed to
   mark the sheet: the next run reads those rows again and skips them.
4. Marks the rows uploaded in the sheet.
5. Lists what it published in the GitHub Actions job summary, when run there.

Exits quietly when submissions are not configured; fails loudly when they are
but the sheet can't be read.
"""
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from src import config
from src.models.event import Event
from src.scrapers.google_sheets import GoogleSheetsScraper, SOURCE_NAME
from src.utils.deduplicator import EventDeduplicator
from src.utils.storage import sort_events
from src.utils.validator import EventValidator

logger = logging.getLogger("sync_user_events")

EVENTS_FILE = Path("data/events.json")


def configured() -> bool:
    return config.FEATURES["submissions"] and bool(config.SUBMISSIONS_SHEET_ID)


def load_existing_events(path: Path) -> List[dict]:
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning(f"{path} not found, starting with an empty list")
        return []
    return data["events"] if isinstance(data, dict) else data


def submission_key(event: dict) -> tuple:
    """Same title (any case) on the same date: already published."""
    return ((event.get('title') or '').lower(), str(event.get('start_datetime') or '')[:10])


def sync(scraper: GoogleSheetsScraper, events_path: Path = EVENTS_FILE) -> List[dict]:
    """Run one sync. Returns the events it added."""
    events = scraper.scrape_events()
    logger.info(f"Fetched {len(events)} new submissions")
    if not events:
        return []

    validator = EventValidator()
    validated = []
    for event in events:
        event = validator.clean_and_enhance(event)
        is_valid, error = validator.validate_event(event)
        if is_valid:
            validated.append(event)
        else:
            logger.warning(f"Rejected submission '{event.title}': {error}")
    if not validated:
        logger.info("No submissions passed validation")
        return []

    now = datetime.utcnow()
    new_events = [
        Event.from_create(e).model_copy(update={"scraped_at": now, "last_updated": now}).model_dump(mode='json')
        for e in EventDeduplicator().deduplicate_events(validated)
    ]

    existing = load_existing_events(events_path)
    stored = {submission_key(e) for e in existing if e.get('source_name') == SOURCE_NAME}
    added = []
    for event in new_events:
        if submission_key(event) in stored:
            logger.info(f"Already published, skipping: {event['title']}")
            continue
        stored.add(submission_key(event))
        added.append(event)

    if added:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(events_path, 'w') as f:
            json.dump(sort_events(existing + added), f, indent=2, default=str)
        logger.info(f"Added {len(added)} submissions to {events_path}")

    # Marked even when every row was a duplicate: they are published either way
    rows = scraper.get_processed_row_indices()
    if rows:
        scraper.mark_as_uploaded(rows)
    return added


def write_job_summary(added: List[dict]) -> None:
    """The review list for whoever moderates: GitHub shows it on the run page."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path or not added:
        return
    lines = [f"### {len(added)} reader submission{'s' if len(added) != 1 else ''} published", ""]
    for e in added:
        where = e.get('venue_name') or e.get('street_address') or 'no venue given'
        lines.append(f"- **{e['title']}** - {str(e['start_datetime'])[:16].replace('T', ' ')}, "
                     f"{where} ([link]({e['source_url']}))")
    lines += ["", "Remove one by deleting it from `data/events.json` (it will not come back: "
              "its sheet row is marked uploaded)."]
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    os.makedirs('logs', exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(), logging.FileHandler('logs/user_events_sync.log', mode='a')],
    )
    if not configured():
        print("Submissions are not configured (features.submissions and submissions.sheet_id "
              "in calendar.config.yaml); nothing to sync.")
        return 0

    added = sync(GoogleSheetsScraper())
    write_job_summary(added)
    print(f"Published {len(added)} reader submissions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
