"""Reader submissions, against a fake Google Sheet.

The sync's one rule: a submission is added only if no stored submission has
the same title on the same date. Rows it handled are marked uploaded so they
are not read again; rows it could not date are left for a corrected retry.
"""
import json
from datetime import date, timedelta

import pytest

import sync_user_events
from src.scrapers.google_sheets import GoogleSheetsScraper

HEADER_OFFSET = 2  # data starts on sheet row 2


class FakeSheet:
    """Just enough of googleapiclient's Sheets service."""

    def __init__(self, rows):
        self.rows, self.updates = rows, []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range=None, fields=None):
        if fields:  # spreadsheets().get(): the tab names
            return _Done({"sheets": [{"properties": {"title": "Form Responses 1"}}]})
        assert range == "'Form Responses 1'!A2:N"
        return _Done({"values": [list(r) for r in self.rows]})

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.updates.append((range, body["values"][0]))
        return _Done({})


class _Done:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def day(offset):
    d = date.today() + timedelta(days=offset)
    return f"{d.month}/{d.day}/{d.year}", d.isoformat()


def row(name, when, time="7:00:00 PM", address="The Chapel, 777 Valencia St, San Francisco, CA",
        approved="", uploaded="", email="host@example.org"):
    return ["9/1/2026 10:00:00", name, when, time, address, f"About {name}.", "Music", "Free", "Yes",
            "", "", email, approved, uploaded]


@pytest.fixture
def events_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_user_events.config, "SITE_URL", "https://events.example.org")
    path = tmp_path / "events.json"
    path.write_text("[]")
    return path


def run(rows, events_file):
    sheet = FakeSheet(rows)
    added = sync_user_events.sync(GoogleSheetsScraper(sheet_id="test-sheet", service=sheet), events_file)
    return added, sheet, json.loads(events_file.read_text())


def test_new_rows_are_published_and_marked(events_file, offline):
    when, iso = day(5)
    added, sheet, stored = run([row("Porch Concert", when)], events_file)
    assert [e["title"] for e in added] == ["Porch Concert"]
    (event,) = stored
    assert event["source_name"] == "User Submitted"
    assert event["start_datetime"] == f"{iso}T19:00:00"
    assert event["venue_name"] == "The Chapel"
    assert event["city"] == "San Francisco"
    assert event["category"] == "music"
    assert event["source_url"] == "https://events.example.org/", "the sheet is private; never link to it"
    assert sheet.updates == [("'Form Responses 1'!M2:N2", ["approved", sheet.updates[0][1][1]])]
    assert sheet.updates[0][1][1].startswith("Yes - ")


def test_a_submission_already_stored_is_not_added_again(events_file, offline):
    """A run that saved but failed to mark the sheet reads the rows again."""
    when, _ = day(5)
    run([row("Porch Concert", when)], events_file)
    added, sheet, stored = run([row("PORCH CONCERT", when, time="8:00:00 PM")], events_file)
    assert added == []
    assert len(stored) == 1
    assert sheet.updates, "the duplicate row is still marked, so it stops coming back"


def test_uploaded_rows_are_never_read_again(events_file, offline):
    when, _ = day(5)
    added, sheet, stored = run([row("Old Show", when, uploaded="Yes - 2026-09-01 08:00 UTC")], events_file)
    assert added == [] and stored == [] and sheet.updates == []


def test_dates_are_never_guessed(events_file, offline):
    when, iso = day(6)
    rows = [
        row("No Date", ""),
        row("No Year", "October 12"),
        row("Gibberish", "next-ish"),
        row("All Day Fair", when, time=""),
    ]
    added, sheet, stored = run(rows, events_file)
    assert [e["title"] for e in stored] == ["All Day Fair"]
    assert stored[0]["all_day"] is True and stored[0]["start_datetime"] == f"{iso}T00:00:00"
    marked = [r for r, _ in sheet.updates]
    assert marked == [f"'Form Responses 1'!M{3 + HEADER_OFFSET}:N{3 + HEADER_OFFSET}"], \
        "undatable rows stay unmarked so a corrected row is retried"


def test_a_mistyped_email_costs_the_email_not_the_event(events_file, offline):
    when, _ = day(4)
    added, _, stored = run([row("Book Swap", when, email="host@")], events_file)
    assert [e["title"] for e in stored] == ["Book Swap"]
    assert stored[0]["contact_email"] is None


def test_moderation_publishes_only_approved_rows(events_file, offline, monkeypatch):
    monkeypatch.setenv("SUBMISSIONS_REQUIRE_APPROVAL", "1")
    when, _ = day(5)
    added, sheet, stored = run([row("Waiting", when), row("Approved Show", when, approved="yes")], events_file)
    assert [e["title"] for e in stored] == ["Approved Show"]
    assert [r for r, _ in sheet.updates] == ["'Form Responses 1'!M3:N3"]


def test_an_unreadable_sheet_fails_loudly(events_file, monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_SERVICE_ACCOUNT_JSON"):
        sync_user_events.sync(GoogleSheetsScraper(sheet_id="test-sheet"), events_file)


def test_not_configured_is_a_quiet_no_op(monkeypatch, capsys):
    monkeypatch.setattr(sync_user_events.config, "SUBMISSIONS_SHEET_ID", "")
    assert sync_user_events.main() == 0
    assert "not configured" in capsys.readouterr().out
