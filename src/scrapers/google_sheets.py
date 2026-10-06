"""Reader submissions: the response sheet of a Google Form.

Not a registered source. sync_user_events.py reads it and stores the events as
"User Submitted", which scrape.py always preserves.

Setup
-----
- calendar.config.yaml `submissions.sheet_id`: the response sheet's id, the
  long string in its URL (docs.google.com/spreadsheets/d/<id>/edit).
- GOOGLE_SERVICE_ACCOUNT_JSON (environment): a Google Cloud service account's
  JSON key, with the Sheets API enabled. Share the sheet with the account's
  email as an Editor; the sync writes back to the last two columns.

Columns
-------
The first tab, row 1 headers, one submission per row. A Google Form writes
A-L in the order of its questions, so create the questions in this order;
add M and N to the sheet by hand.

    A  Timestamp        written by the form
    B  Event Name       required
    C  Date             required, with a year: a Form "Date" question
    D  Time             optional; empty makes an all-day listing
    E  Address          "Venue, 123 Street, City" or a bare street address
    F  Description
    G  Category         music, arts and culture, food and drink, theater,
                        lectures, sports, community, other
    H  Cost
    I  Family Friendly  yes / no
    J  Event URL        the "more info" link; the site's home if empty
    K  Image URL
    L  Contact Email    published with the event
    M  Approved         see moderation, below
    N  Uploaded         written by the sync: "Yes - <when>". Rows marked here
                        are never read again.

Moderation
----------
By default every new row is published on the next sync (Cambridge Calendar's
behaviour), and the sync writes "approved" into column M. With
SUBMISSIONS_REQUIRE_APPROVAL=1 in the environment, a row is published only
once someone types yes (or approved) into its Approved cell; until then it
waits.

Dates are never guessed (CLAUDE.md, rule 1): a row whose date cannot be read,
or names no year, is skipped and logged, and stays unmarked so a corrected
row is picked up on the next sync.
"""
import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional, Tuple

from dateutil import parser as date_parser

from src import config
from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

SOURCE_NAME = "User Submitted"

COLUMNS = ("timestamp", "event_name", "date", "time", "address", "description", "category",
           "cost", "family_friendly", "event_url", "image_url", "contact_email", "approved", "uploaded")
APPROVED_COL, UPLOADED_COL = "M", "N"

# Category mapping from form values to EventCategory
CATEGORY_MAP = {
    'music': EventCategory.MUSIC,
    'arts and culture': EventCategory.ARTS_CULTURE,
    'arts & culture': EventCategory.ARTS_CULTURE,
    'food and drink': EventCategory.FOOD_DRINK,
    'food & drink': EventCategory.FOOD_DRINK,
    'theater': EventCategory.THEATER,
    'theatre': EventCategory.THEATER,
    'lectures': EventCategory.LECTURES,
    'sports': EventCategory.SPORTS,
    'community': EventCategory.COMMUNITY,
    'other': EventCategory.OTHER,
}

YES = {"yes", "y", "true", "1", "approved", "x"}

# A year no real submission has: what dateutil fills in when none is written
_NO_YEAR = 1900


def require_approval() -> bool:
    return os.environ.get("SUBMISSIONS_REQUIRE_APPROVAL", "").strip().lower() in YES


class GoogleSheetsScraper(BaseScraper):
    """Reads not-yet-uploaded rows from the submissions sheet."""

    def __init__(self, sheet_id: str = None, credentials_json: str = None, service=None):
        self.sheet_id = sheet_id or config.SUBMISSIONS_SHEET_ID
        super().__init__(
            source_name=SOURCE_NAME,
            source_url=f"https://docs.google.com/spreadsheets/d/{self.sheet_id}",
        )
        self.credentials_json = credentials_json
        self._service = service
        self._sheet_name = None
        self._processed_rows = []

    def _get_sheets_service(self):
        if self._service:
            return self._service
        if not self.sheet_id:
            raise ValueError("No submissions sheet: set submissions.sheet_id in calendar.config.yaml")

        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_json = self.credentials_json or os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        if not creds_json:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not set: the sync needs a service "
                             "account key with edit access to the submissions sheet")
        try:
            creds_dict = json.loads(creds_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in GOOGLE_SERVICE_ACCOUNT_JSON: {e}")

        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        self._service = build('sheets', 'v4', credentials=credentials, cache_discovery=False)
        logger.info("Google Sheets API service initialized")
        return self._service

    def _get_first_sheet_name(self) -> str:
        if self._sheet_name:
            return self._sheet_name
        spreadsheet = self._get_sheets_service().spreadsheets().get(
            spreadsheetId=self.sheet_id, fields='sheets.properties.title').execute()
        sheets = spreadsheet.get('sheets', [])
        if not sheets:
            raise ValueError("No sheets found in spreadsheet")
        self._sheet_name = sheets[0]['properties']['title']
        logger.info(f"Using sheet: {self._sheet_name}")
        return self._sheet_name

    def fetch_pending_rows(self) -> List[dict]:
        """Rows not yet uploaded (and, if moderation is on, approved)."""
        sheet_name = self._get_first_sheet_name()
        result = self._get_sheets_service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"'{sheet_name}'!A2:{UPLOADED_COL}").execute()
        rows = result.get('values', [])
        logger.info(f"Found {len(rows)} total rows in sheet")

        moderated = require_approval()
        pending, awaiting = [], 0
        for row_idx, row in enumerate(rows):
            row = list(row) + [''] * (len(COLUMNS) - len(row))
            data = {name: (row[i] or '').strip() for i, name in enumerate(COLUMNS)}
            if not data['event_name'] or data['uploaded'].lower().startswith('yes'):
                continue
            if moderated and data['approved'].lower() not in YES:
                awaiting += 1
                continue
            data['row_index'] = row_idx + 2  # 1-indexed, after the header row
            pending.append(data)

        if awaiting:
            logger.info(f"{awaiting} submissions are waiting for approval (column {APPROVED_COL})")
        logger.info(f"Found {len(pending)} submissions to publish")
        return pending

    def mark_as_uploaded(self, row_indices: List[int] = None):
        """Write 'approved' and 'Yes - <when>' into columns M and N."""
        indices = row_indices or self._processed_rows
        if not indices:
            return
        service = self._get_sheets_service()
        sheet_name = self._get_first_sheet_name()
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        for row_idx in indices:
            try:
                service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"'{sheet_name}'!{APPROVED_COL}{row_idx}:{UPLOADED_COL}{row_idx}",
                    valueInputOption='RAW',
                    body={'values': [['approved', f'Yes - {now}']]},
                ).execute()
            except Exception as e:
                logger.error(f"Failed to mark row {row_idx} as uploaded: {e}")
        logger.info(f"Marked {len(indices)} rows as uploaded")

    def parse_datetime(self, date_str: str, time_str: str) -> Tuple[Optional[datetime], bool]:
        """(start, all_day), or (None, False) when the date can't be read.

        The date must carry its own year; a missing time makes the listing
        all-day rather than inventing midnight.
        """
        if not date_str:
            return None, False
        try:
            day = date_parser.parse(date_str, default=datetime(_NO_YEAR, 1, 1))
        except (ValueError, OverflowError) as e:
            logger.warning(f"Unreadable date '{date_str}': {e}")
            return None, False
        if day.year == _NO_YEAR:
            logger.warning(f"Date '{date_str}' names no year")
            return None, False
        day = day.replace(hour=0, minute=0, second=0, microsecond=0)
        if not time_str:
            return day, True
        try:
            clock = date_parser.parse(time_str, default=day)
        except (ValueError, OverflowError) as e:
            logger.warning(f"Unreadable time '{time_str}': {e}")
            return None, False
        return day.replace(hour=clock.hour, minute=clock.minute), False

    def parse_category(self, category_str: str) -> EventCategory:
        return CATEGORY_MAP.get((category_str or '').lower().strip(), EventCategory.OTHER)

    def parse_family_friendly(self, value: str) -> bool:
        return (value or '').lower().strip() in ('yes', 'true', '1', 'y')

    def parse_address(self, address: str) -> Tuple[Optional[str], Optional[str]]:
        """Split a free-text address into (venue_name, street_address).

        - "The Fillmore, 1805 Geary Blvd, San Francisco, CA 94115"
          -> ("The Fillmore", "1805 Geary Blvd, San Francisco, CA 94115")
        - "Lake Merritt Amphitheater (Lakeshore Ave & Brooklyn Ave, Oakland)"
          -> ("Lake Merritt Amphitheater", "Lakeshore Ave & Brooklyn Ave, Oakland")
        - "1805 Geary Blvd San Francisco, CA" -> (None, "1805 Geary Blvd San Francisco, CA")
        - "Dolores Park" -> ("Dolores Park", None)
        """
        if not address or not address.strip():
            return None, None
        address = address.strip()

        paren = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', address)
        if paren:
            return paren.group(1).strip(), paren.group(2).strip()

        parts = [p.strip() for p in address.split(',')]
        if len(parts) >= 2 and parts[0] and not re.match(r'^\d', parts[0]):
            return parts[0], ', '.join(parts[1:])

        if re.match(r'^\d', address):
            return None, address
        return address, None

    def scrape_events(self) -> List[EventCreate]:
        """The pending rows as events. Raises if the sheet can't be read:
        an empty result must mean "nothing new", never "could not look"."""
        events = []
        self._processed_rows = []

        for row in self.fetch_pending_rows():
            title = row['event_name'][:200]
            start, all_day = self.parse_datetime(row['date'], row['time'])
            if start is None:
                logger.warning(f"Skipping '{title}' (row {row['row_index']}) - no readable date "
                               f"(date={row['date']!r}, time={row['time']!r})")
                continue
            venue_name, street_address = self.parse_address(row['address'])
            event_url = row['event_url'] or None
            fields = dict(
                title=title,
                description=(row['description'] or title)[:2000],
                start_datetime=start,
                all_day=all_day,
                # The sheet is private: never send readers to it
                source_url=event_url or (config.SITE_URL + "/" if config.SITE_URL else self.source_url),
                source_name=self.source_name,
                venue_name=venue_name,
                street_address=street_address,
                category=self.parse_category(row['category']),
                cost=row['cost'] or None,
                family_friendly=self.parse_family_friendly(row['family_friendly']),
                image_url=row['image_url'] or None,
                website_url=event_url,
                contact_email=row['contact_email'] or None,
            )
            try:
                try:
                    event = EventCreate(**fields)
                except ValueError:
                    if not fields['contact_email']:
                        raise
                    # A mistyped email should cost the email, not the event
                    event = EventCreate(**{**fields, 'contact_email': None})
            except Exception as e:
                logger.error(f"Failed to parse '{title}' (row {row['row_index']}): {e}")
                continue
            events.append(event)
            self._processed_rows.append(row['row_index'])

        logger.info(f"Parsed {len(events)} events from the submissions sheet")
        return events

    def get_processed_row_indices(self) -> List[int]:
        return self._processed_rows.copy()
