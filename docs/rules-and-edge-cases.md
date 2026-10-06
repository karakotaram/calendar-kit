# Rules, edge cases and tests

Every rule this calendar enforces, the incident that produced it, the test that
keeps it true, and what to do when it fires.

The kit is a generalised copy of Cambridge Calendar (cambridgecalendar.com),
which ran this engine against about forty venue websites in Cambridge and
Somerville, MA. Every rule below exists because something specific went wrong
there. The incidents are dated history: their numbers describe Cambridge on
that date, not your calendar today. The full timeline is in
[incident-log.md](incident-log.md).

**The one idea behind all of it:** every scraper will break, and most breakage
is silent. The system's job is not to scrape correctly. It is to make breakage
**loud** (a specific, findable signal instead of a plausible wrong answer) and
**non-shipping** (a run that fails its checks does not reach readers). See
[architecture.md](architecture.md).

How to read a rule:

- **Rule**: one sentence.
- **Why**: the incident behind it, with Cambridge's real numbers.
- **Test**: the test in this repo that enforces it. Run one with
  `.venv/bin/python -m pytest "tests/test_gate.py::test_healthy_run_passes"`.
- **When it fires**: what to do.

A rule with no test says so, and is repeated in [Untested](#untested) at the
end. Nothing here claims a test that does not exist.

Contents: [Dates and times](#dates-and-times) ·
[Sources and access](#sources-and-access) ·
[Duplicates and identity](#duplicates-and-identity) ·
[The publish gate](#the-publish-gate) · [Monitoring](#monitoring) ·
[Frontend and API](#frontend-and-api) · [Edge cases](#edge-cases) ·
[How testing works](#how-testing-works) · [Untested](#untested)

---

## Dates and times

A missing event costs one reader one listing. A wrong date costs every reader
that day's trust. Absence is always the cheaper failure.

### D1. Never fabricate a date or a time

- **Rule**: a scraper that cannot read an event's date or time skips the event
  with a warning; it never substitutes the clock, a default hour, or a
  neighbouring value.
- **Why**: on 2026-08-31 the City of Cambridge scraper's browser died a third
  of the way through its run, and its fallback `start = parsed or current_date`
  (the scrape clock plus N weeks) stamped 117 events with
  `2026-09-14T13:29:26.288025`. They all landed on one day, 2026-09-14, which
  showed 133 events instead of the usual ~40. It shipped; a reader emailed. The
  same defect was then found in Cambridge Public Library (every event at 10:00
  on the day of the scrape, 2026-09-01), Skip the Small Talk (18:30), and, in the
  2026-10-05 audit, invented 7 PM, 8 PM, 6 PM and midnight defaults in more
  than ten other scrapers.
- **Test**: `tests/test_validator_dates.py::test_scrape_timestamps_are_rejected`;
  `tests/test_gate.py::test_fabricated_dates_are_blocked_on_invariants_alone`
  replays the run that shipped; every adapter test also runs the invariants over
  its fixture's output (see [How testing works](#how-testing-works)).
- **When it fires**: find the code path that fills a missing value and make it
  `continue` with a `logger.warning` naming the title and URL. Never loosen the
  validator to let the event through.

```python
# never
start = parsed if parsed else current_date
# always
if parsed is None:
    logger.warning(f"Skipping '{title}' - no parseable date ({url})")
    continue
```

### D2. Real listings are on the minute

- **Rule**: a start time carrying seconds or microseconds is rejected, because
  sub-minute precision only ever comes from a clock reading; feeds whose
  timestamps carry millisecond noise are floored to the minute on purpose.
- **Why**: the 117 fabricated events of 2026-08-31 all carried `.288025`
  microseconds. Legitimate feeds also carry noise: Squarespace sends 19:30 as an
  epoch ending `...400563` ms, Harvard Art Museums sends `22:00:31Z`. Those must
  be floored, or the validator rejects real events as clock readings.
- **Test**: `tests/test_validator_dates.py::test_published_times_are_accepted`,
  `tests/test_validator_dates.py::test_scrape_timestamps_are_rejected`,
  `tests/test_docs.py::test_stored_data_satisfies_documented_invariants[clock_stamped]`.
- **When it fires**: if the source is a feed with noisy timestamps, pass the
  value through `on_the_minute()` in `src/adapters/_common.py`. If it is a
  scraper reading the clock, it is D1.

### D3. No pileups on one timestamp

- **Rule**: more than `MAX_EVENTS_PER_TIMESTAMP` (20) events from one source on
  the same exact start is an invariant violation (`timestamp_pileup`), and so is
  a source of eight or more events where over 70% share one start
  (`uniform_timestamp`).
- **Why**: on 2026-08-31, 117 events shared one timestamp; the highest
  legitimate value ever observed was 8 (library programmes at 6 PM across
  branches). On 2026-09-01 Cambridge Public Library was found stamping every
  event `datetime.now().replace(hour=10, minute=0, second=0)`: 17 events, under
  the cap of 20, with zeroed seconds so D2 could not see it. The share rule
  catches that regardless of size.
- **Test**: `tests/test_gate.py::test_fabricated_dates_are_blocked_on_invariants_alone`
  asserts `timestamp_pileup`;
  `tests/test_docs.py::test_stored_data_satisfies_documented_invariants[timestamp_pileup]`.
  **`uniform_timestamp` has no test.**
- **When it fires**: it is almost always D1 in disguise. Run
  `cal scrape "<source>"` and look for the shared start.

### D4. Store naive local wall-clock time, in the calendar's own zone

- **Rule**: every stored `start_datetime` and `end_datetime` is naive wall-clock
  time in the zone `calendar.config.yaml` names (`region.timezone`); an aware
  value is converted to that zone and its offset stripped at model
  construction.
- **Why**: on 2026-08-31, 56 Cambridge events carried a UTC offset while 2,918
  were naive. Comparing the two raises `TypeError`, which is what made
  `/events?start_date=` and `/stats` return 500. Worse, anything converting to
  the viewer's zone moved the offset-carrying evening events to a different day
  from their naive neighbours. A 9:30 PM show in Somerville is not a different
  day's event because the reader opened the page in Denver.
- **Test**: `tests/test_event_timezones.py` (all of it), including
  `test_mixed_sources_sort_and_compare_without_raising` and
  `test_pacific_calendar_stores_pacific_wall_clock`, which proves the zone comes
  from config; `tests/test_docs.py::test_stored_data_satisfies_documented_invariants[tz_aware]`.
- **When it fires** (`tz_aware`): something wrote around the model. Build
  events through `EventCreate`/`Event` so `to_local_naive()` in
  `src/models/event.py` runs. A datetime from a query parameter needs the same
  conversion before comparison.

### D5. Convert instants explicitly, never in the machine's zone

- **Rule**: an epoch or a UTC timestamp is converted with an explicit zone
  (`datetime.fromtimestamp(x, tz=timezone.utc)`, then `to_local_naive()`);
  `datetime.fromtimestamp(x)` with no zone is never correct.
- **Why**: the 2026-10-05 audit found First Parish's 10:30 AM services published
  at 14:30 (15:30 after the November clock change) because the epoch was read in
  the machine's zone, and CI runs in UTC. Regent Theatre had the same bug.
  Brattle Theatre's showtime day was right on a UTC runner and an Eastern laptop
  only by luck; anywhere west of Pacific moved every showtime a day early.
  Harvard Art Museums took the calendar date of a UTC instant and wrote the local
  time onto it, so anything at or after 8 PM EDT would have published a day
  late.
- **Test**: `tests/test_event_timezones.py::test_utc_is_converted_not_just_stripped`,
  `::test_conversion_respects_daylight_saving`,
  `::test_a_late_evening_event_keeps_its_own_day`;
  `tests/adapters/test_eventon.py::test_times_do_not_depend_on_the_machine_zone`
  (parses the same page under several `TZ` values);
  `tests/adapters/test_squarespace.py::test_epoch_milliseconds_become_local_wall_clock_on_the_minute`;
  `tests/adapters/test_tribe.py::test_times_come_from_utc_and_agree_with_the_local_field`.
- **When it fires**: run the source under another zone
  (`TZ=Pacific/Honolulu cal scrape "<source>"`); if the times move, a
  conversion is using the machine's zone.

### D6. When a source states a time twice, both must agree

- **Rule**: if a page gives the same moment in two renderings (an epoch and
  printed text, an ISO field and a teaser, a UTC attribute and local text), the
  event is published only when they agree; a disagreement skips it rather than
  picking one.
- **Why**: EventON writes a `-4:00` offset year-round. On 2026-10-05 that put 15
  of Regent Theatre's 34 events (everything in standard time) an hour early:
  "That Motown Band", printed 8:00 pm, was published at 19:00. Harvard
  Athletics' ISO `date` disagreed with the displayed time for some games (Rugby
  vs Navy published at 1 PM; the site said 11:00 AM). Skip the Small Talk's
  products are cloned from each other, and one Chicago listing's URL still said
  `august-5` for an August 19 event.
- **Test**: `tests/adapters/test_eventon.py::test_every_start_matches_the_time_on_the_card`,
  `::test_disagreeing_renderings_are_skipped`;
  `tests/adapters/test_drupal_listing.py::test_a_start_is_believed_only_when_both_renderings_agree`;
  `tests/adapters/test_indiecommerce.py::test_a_start_that_disagrees_with_the_printed_date_or_time_is_skipped`;
  `tests/adapters/test_assabet.py::test_a_card_that_disagrees_with_the_json_ld_is_skipped`;
  `tests/adapters/test_tribe.py::test_a_start_its_fields_disagree_on_is_skipped`;
  `tests/adapters/test_jsonld.py::test_an_offset_that_is_not_the_regions_is_not_believed`.
- **When it fires**: a disagreement means one rendering changed meaning. Read
  both on the live page, decide which the venue intends, and fix the adapter;
  do not silence the cross-check.

### D7. A date with no time is all-day, never an invented hour

- **Rule**: a listing that gives a date but no time is published with
  `all_day: true` at 00:00 of its first day; time text that is present but
  unreadable (`TBA` aside) skips the event.
- **Why**: on 2026-10-05 Sanders Theatre's "Midwinter Revels", listed
  "December 11-28, 2026" with no time, showed as a 12:00 AM start. Harvard
  Athletics published "TBA" games at midnight. Boston Swing Central's boot camp
  went out at 00:00; it runs 11:00 AM to 1:30 PM. Others defaulted to 7 PM or
  8 PM.
- **Test**: in every adapter: `tests/adapters/test_tribe.py::test_all_day_events_are_dated_not_timed`,
  `tests/adapters/test_localist.py::test_times_are_local_wall_clock_and_all_day_is_dated`,
  `tests/adapters/test_ical.py::test_a_date_only_event_is_all_day`,
  `tests/adapters/test_eventon.py::test_an_all_day_listing_is_midnight_and_flagged`,
  `tests/adapters/test_drupal_listing.py::test_a_date_only_time_is_all_day_at_midnight`,
  `tests/adapters/test_assabet.py::test_an_all_day_card_is_midnight_and_flagged`,
  `tests/adapters/test_indiecommerce.py::test_an_all_day_event_is_midnight_and_flagged`,
  `tests/adapters/test_jsonld.py::test_date_only_is_all_day_and_a_time_is_used`; in the
  custom-scraper template, `tests/sources/test_templates.py::test_listed_times_are_read_and_nothing_is_invented`;
  in chat, `tests/test_api_chat.py::test_the_listings_say_when_without_inventing_a_time`.
  The API side is in [Frontend and API](#frontend-and-api).
- **When it fires**: there is no failing check for an invented hour, which is
  why D1 matters. Spot-check date-only listings during onboarding.

### D8. A missing year comes from the printed weekday, never the clock

- **Rule**: when a listing prints "Wed, October 7" with no year, the year is the
  one near today in which that date falls on that weekday
  (`year_for_weekday()` in `src/adapters/_common.py`); with no weekday printed,
  or no matching year, the event is skipped.
- **Why**: in the 2026-10-05 audit, Museum of Science published "Strange Land",
  listed "Wednesday, September 23", as 2027-09-23, a Thursday, because a passed
  date was rolled into next year. A.R.T., run in June 2026, put Life on Mars's
  2027 dates in 2026. Aeronaut rolled any event earlier than the scrape clock,
  including one that had started an hour before, into the next year.
- **Test**: `tests/sources/test_templates.py::test_undated_unreadable_and_cancelled_listings_are_skipped`
  ("Mon, Oct 9" matches no nearby year and is skipped). `year_for_weekday()`
  has no test of its own.
- **When it fires**: a skipped event with a printed date means the weekday text
  did not parse or contradicts the date. Look at the page; do not add a
  fallback year.

### D9. A time with no meridiem is unreadable

- **Rule**: "5:00" with no AM/PM is skipped, not read as morning; a range
  without a meridiem on its start borrows the end's ("6:00-7:00 PM" starts at
  6 PM, "8-9 am" at 8 AM).
- **Why**: Cambridge.gov's `<time datetime>` attribute is a 12-hour clock with
  no meridiem, so 5 PM is written `05:00:00`. When the date fix shipped on
  2026-08-31, 646 of 1,121 events would otherwise have been twelve hours early.
  Multicultural Arts Center's "6:00 - 8:00 PM" was published as 8 PM because the
  first "N pm" on the page won. Somerville Public Library's cards omit the
  start's meridiem whenever it matches the end's.
- **Test**: `tests/adapters/test_assabet.py::test_the_start_shares_the_end_meridiem`;
  `tests/sources/test_templates.py::test_listed_times_are_read_and_nothing_is_invented`
  ("6:00 - 8:00 PM" starts at 18:00) and
  `::test_undated_unreadable_and_cancelled_listings_are_skipped` ("7:30" is skipped).
- **When it fires**: check what the venue's own page shows. If the machine
  attribute is the ambiguous one, take the time from the visible text.

### D10. Scrapers do not read the clock

- **Rule**: a scraper or adapter never filters by `datetime.now()` and never
  bounds a window by the clock; staleness is `EventValidator`'s job, and a
  window (such as recurring-event expansion) is anchored on a timestamp in the
  data itself.
- **Why**: in the 2026-10-05 audit, Grolier's saved page parsed to 18 readings
  when captured on 2026-09-01 and 8 a month later, until its test failed on
  `main`; Brattle's August fixture would have yielded nothing after Oct 21.
  Theatre@First's window of [now - 1 day, now + 365 days] made the same saved
  feed parse differently every day; it is now anchored on the feed's own
  `DTSTAMP`. A scraper that reads the clock cannot be tested offline.
- **Test**: every fixture test must pass on any day it runs;
  `tests/adapters/test_ical.py::test_the_window_comes_from_the_feed_not_the_clock`,
  `::test_a_feed_without_a_timestamp_cannot_say_what_is_current`;
  `tests/adapters/test_assabet.py::test_months_chain_by_the_listings_own_next_link`;
  `tests/adapters/test_indiecommerce.py::test_months_chain_from_the_page_not_the_clock`.
- **When it fires** (a fixture test starts failing with no code change): the
  scraper is reading the clock. Remove the filter.

### D11. Dates must be plausible

- **Rule**: `EventValidator` rejects new events more than 30 days past or 2
  years ahead; the stored-data invariant `date_out_of_range` uses ±730 days,
  because stored data legitimately holds older events.
- **Why**: the invariant was first written at ±30 days and flagged 258 events
  in clean data on 2026-08-31, because user submissions are never re-scraped
  and CI-skipped sources keep their listings. An invariant that fires on healthy
  data is broken, not strict. Separately, Theatre at First returned seven
  events every run, all over 30 days old, all correctly rejected: it looked
  alive and contributed nothing.
- **Test**: **none.** Neither the validator's window nor `date_out_of_range` is
  tested directly.
- **When it fires**: `date_out_of_range` means a year-parsing bug (D8). A
  source whose events are all rejected shows `ok` status and zero published
  events; read the run record's `rejected` counts ([operations.md](operations.md)).

---

## Sources and access

### S1. One registry, one file per source

- **Rule**: a source is described in exactly one place, `registry/<slug>.yaml`;
  `scrape.py`, `scrape_local.py`, the CI monitor and `cal` all derive from
  `src/sources.py`, which reads that directory, and names must be unique.
- **Why**: Cambridge kept its source list in `scrape.py` and again in the CI
  monitor. By 2026-08-31 they had drifted: four scrapers (Harvard GSD, Museum of
  Science, Regent Theatre, The Sinclair) ran daily with no monitoring, and
  Longfellow House, 185 lines of working scraper, had never been wired in and
  produced nothing for months. `scrape_local.py` was a third copy until
  2026-09-01. One file per source (rather than one list) lets several people,
  or several Claude Code workers, onboard sources in parallel without merge
  conflicts.
- **Test**: `tests/test_docs.py::test_every_registry_file_parses_and_explains_itself`,
  `::test_every_custom_scraper_module_is_registered`,
  `::test_monitoring_covers_every_registered_source`. A repeated name makes
  `load_registry()` raise at import, which fails every test.
- **When it fires**: register the module (`cal add`) or delete it. Never add a
  second list anywhere.

### S2. A source's registry name is the name its events carry

- **Rule**: `name:` in the registry file equals the `source_name` the scraper
  puts on its events.
- **Why**: if they differ, events land under one name while monitoring and
  deduplication ranking look for another, which silently unmonitors the source.
- **Test**: `tests/test_docs.py::test_registry_names_match_what_scrapers_emit`.
- **When it fires**: change one to match the other. Adapters take their name
  from the registry, so this only bites custom scrapers.

### S3. Identify honestly

- **Rule**: every request names this calendar in its user-agent
  (`config.USER_AGENT`, built from `site.short_name` plus the site URL or contact
  email), and a browser sends its own user-agent; nothing ever impersonates an
  ordinary browser.
- **Why**: Cambridge's Playwright base claimed macOS while the browser's client
  hints said Linux, and bot protection reads that contradiction for what it is:
  on 2026-09-01 Porter Square Books returned 403 to the spoof and 200 to the
  browser's own user-agent, from the same headless Chromium. On 2026-10-06 the
  remaining spoofs (Windows Chrome 120 in Selenium, macOS Chrome 120 for plain
  HTTP) were removed, and every source answered the honest user-agent exactly as
  it had the spoof. Some servers refuse the bare `python-requests` default
  (Somerville Theatre's nginx, The Middle East) and accept a named client.
- **Test**: `tests/test_detect.py::test_the_default_fetch_identifies_honestly`;
  `tests/adapters/test_tribe.py::test_one_honest_request_per_page_with_a_server_side_window`;
  `tests/adapters/test_squarespace.py::test_every_upcoming_event_in_one_request`;
  `tests/adapters/test_indiecommerce.py::test_headless_unless_a_human_opts_into_a_window`
  (a browser keeps its own user-agent);
  `tests/test_geocoder.py::test_nominatim_answers_are_cached_with_an_honest_user_agent`.
- **When it fires**: see [access-policy.md](access-policy.md). The answer to a
  403 is never a different user-agent string.

### S4. A refused page is a failure, not an empty listing

- **Rule**: a scrape that finds nothing after any navigation returned HTTP 400
  or above raises `ScrapeRefusedError`, and a bot-check page that does not clear
  on its own fails the source; zero events from a page that loaded normally is
  a legitimate empty listing.
- **Why**: Playwright's `goto()` result was being discarded, so a 403 or a
  Cloudflare interstitial parsed as an ordinary page with no listings and the run
  recorded "ok, 0 events". The 2026-10-05 audit found six sources that had sat
  in that state in CI for weeks.
- **Test**: `tests/test_base_playwright_scraper.py`:
  `test_zero_events_after_a_403_is_a_failure`,
  `test_the_message_names_where_a_redirect_landed`,
  `test_an_empty_listing_that_loaded_is_not_a_failure`,
  `test_events_found_despite_one_refused_page_are_kept`,
  `test_a_navigation_without_a_response_is_not_a_refusal`,
  `test_each_run_starts_with_a_clean_record`.
- **When it fires**: the run record names the status and URL. A one-off is
  noise; a repeat is a block ([access-policy.md](access-policy.md#when-a-site-blocks-you)).
  Plain-HTTP adapters must also raise on an error status or a non-JSON body
  rather than return `[]`.

### S5. A failed scrape never deletes events

- **Rule**: when a source fails or returns nothing, its stored events that are
  still upcoming are kept; a source that scraped successfully replaces its own
  events entirely.
- **Why**: on 2026-09-01 Harvard Book Store began returning 403 from every IP.
  Preservation only applied inside CI, so a local run deleted all 21 of its
  events, and Somerville Theatre's with them. On 2026-10-06 `scrape_local.py` was
  found doing the same to Aeronaut. A scrape that failed is not evidence that a
  venue cancelled its programme.
- **Test**: `tests/test_gate.py::test_a_failed_source_keeps_its_upcoming_events`,
  `tests/test_gate.py::test_a_source_that_succeeded_is_replaced_not_merged`.
  The same rule in `scrape_local.py` has no test.
- **When it fires**: it is silent by design; the run log says "Kept existing
  events for sources that produced nothing this run". Fix the source before its
  kept events run out.

### S6. Read every page, and make any cap loud

- **Rule**: an adapter follows the platform's own paging (`total_pages`,
  `page.total`, a "next" link, a "Load More" endpoint) to the end; a sanity cap
  that would truncate logs an error naming how much was not read.
- **Why**: the 2026-10-05 audit found most of the calendar's gaps were
  page-one-only scrapers. The Middle East read 20 shows of about 227; Mount
  Auburn 10 of 39; Longy 10 of 34; The Comedy Studio kept 30 of 163; MIT Events
  read the homepage, about 5% of the calendar; The Sinclair 20 of 67; Mad Monkfish
  10 of 37; Arts at the Armory 12 of 81. The Dance Complex's cap of 1,000 events
  was 87% full with nothing to say so when it filled.
- **Test**: `tests/adapters/test_tribe.py::test_every_page_the_api_reports_is_read`,
  `::test_hitting_the_sanity_cap_is_an_error`;
  `tests/adapters/test_localist.py::test_reads_the_whole_calendar_page_by_page`,
  `::test_query_api_url_and_the_page_cap`;
  `tests/adapters/test_drupal_listing.py::test_paging_stops_when_a_page_adds_nothing_new`;
  `tests/adapters/test_jsonld.py::test_every_show_in_the_listing_is_read`;
  `tests/adapters/test_ical.py::test_every_event_in_the_feed_is_read`.
- **When it fires**: during onboarding, compare `cal scrape` against the
  furthest-out event on the venue's own page. A short `date_span_days` in drift
  means a run was truncated.

### S7. Publish only events a reader can attend, in the region

- **Rule**: closures, deadlines, members-only or internal events, placeholders,
  cancelled listings and events held outside the region are skipped, judged per
  event from the event's own fields rather than from a URL filter.
- **Why**: on 2026-09-01 Skip the Small Talk was found publishing 114 events
  from Raleigh, Baltimore and other cities on Cambridge's calendar: its
  `?category=Cambridge` request named a category that does not exist, so the
  site returned its national list. Harvard GSD's alumni receptions in Los
  Angeles, Toronto, Miami and Seattle went out as Cambridge events. A deadline
  ("Agassiz Theater Apps Due", 11:59 PM), office closures ("Holiday:", listed at
  12:00 am), closed executive sessions and a live "Example Meeting Event"
  placeholder all reached the calendar. Most of MIT's calendar is internal; on
  2026-10-06 166 of 665 occurrences were open to the public.
- **Test**: `tests/adapters/test_localist.py::test_only_events_open_to_the_general_public`,
  `::test_holidays_are_not_events_and_virtual_events_are_online`;
  `tests/adapters/test_drupal_listing.py::test_only_attendable_events_are_published`;
  `tests/adapters/test_squarespace.py::test_private_bookings_are_not_public_events`,
  `::test_rescheduled_notices_are_skipped_but_mentions_kept`;
  `tests/adapters/test_ical.py::test_cancelled_private_and_excluded_events_are_skipped`.
  **No adapter filters by region**, so events outside it are untested; a
  source that lists other cities needs a custom rule and its own test.
- **When it fires**: a reader reports an event they cannot attend. Add the
  exclusion to the adapter's params or the custom scraper, keyed on a field of
  the event, and add the case to its test. If a venue asks for an event to be
  removed, exclude it by its id or slug with a comment naming who asked and
  when.

### S8. Sources CI cannot reach run locally, on a schedule

- **Rule**: a source registered `runs_in_ci: false` is skipped and preserved by
  the daily CI scrape and refreshed by `scrape_local.py`, which the weekly local
  job runs.
- **Why**: Boston Swing Central blocks GitHub's IP ranges, and visible-browser
  sources need a display. Nothing ran `scrape_local.py` on a schedule until
  2026-10-06, and Somerville Theatre's listings had gone 96 days without a
  refresh. Separately, nine Playwright scrapers failed in CI every night until
  2026-08-31 because the workflow never ran `playwright install`.
- **Test**: **none.**
- **When it fires**: `cal sources` shows "last scraped" per source. See
  [operations.md](operations.md#the-weekly-local-job).

### S9. Visible-browser mode is opt-in, local, and honest

- **Rule**: a source may open a visible browser window only when an owner has
  decided it should; such a source sends the browser's own user-agent, uses no
  automation-hiding flags, never interacts with a challenge, fails if a challenge
  does not clear on its own, and is registered `runs_in_ci: false`.
- **Why**: Porter Square Books, Harvard Book Store and Aeronaut were retired
  (2026-09-01 and 2026-10-06) because their Cloudflare settings refused plain
  HTTP and any browser announcing itself as HeadlessChrome. They serve an
  ordinary visible browser, and on 2026-10-06 the owner decided to read them that
  way, on those terms. After a day of testing, both bookstores put follow-up pages
  behind a check that did not clear on its own, so the IndieCommerce scraper reads
  one month per run.
- **Test**: `tests/adapters/test_indiecommerce.py::test_headless_unless_a_human_opts_into_a_window`,
  `::test_a_challenge_that_does_not_clear_fails_the_source`;
  `tests/test_detect.py::test_a_challenge_page_is_reported_blocked_and_nothing_else`.
- **When it fires**: a `ScrapeRefusedError` naming a bot-check page. Do not
  retry in a loop. See [access-policy.md](access-policy.md#visible-browser-mode).

### S10. Be polite

- **Rule**: one request per page of data, a pause between pages of a listing
  (about a second), no retry loops against a challenge or a 429, and the
  platform's data endpoint rather than one request per event page wherever
  possible.
- **Why**: Multicultural Arts Center answers 429 after a short burst; its
  scraper made about 40 requests per run and now makes 7, a second apart. Longy's
  Imunify360 challenge is keyed on the caller's IP and was tripped by repeated
  testing from one machine (2026-09-01). Arts at the Armory's iCal feed replaced 7
  listing pages and 81 detail pages with one request.
- **Test**: `tests/adapters/test_tribe.py::test_one_honest_request_per_page_with_a_server_side_window`
  (one request per page, no retries);
  `tests/adapters/test_indiecommerce.py::test_a_run_reads_the_requested_months_and_pauses_between_them`;
  `tests/test_geocoder.py::test_at_most_one_request_a_second`;
  `tests/test_detect.py::test_probe_requests_stay_within_budget`. Pacing in
  other adapters and custom scrapers is **untested**.
- **When it fires**: a 429 or a challenge during development. Stop, wait, and
  read the platform's feed instead.

### S11. Blocked and retired sources are recorded, not deleted

- **Rule**: a source that refuses access is set to `status: blocked`, and one
  that is gone or redundant to `status: retired`, with `notes` saying why and
  since when; neither is run, and monitoring stays silent about a source nobody
  scrapes any more. A blocked source keeps its upcoming listings while the
  venue is asked for a feed; a retired source's listings are dropped.
- **Why**: Harvard Memorial Church returned 403 on every path, to every client,
  from every IP: a deliberate block, retired 2026-09-01. Cambridge Public
  Library fabricated every date and duplicated City of Cambridge's library
  listings, retired the same day. Harvard Book Store's 14 runs of history then
  reported "source disappeared entirely" on every check, a permanent alert for
  an intended state, which trains its reader to skip the output. On 2026-10-06
  the health monitor raised CRITICAL alerts, each opening a GitHub issue, for
  two sources retired that morning.
- **Test**: `tests/test_docs.py::test_every_registry_file_parses_and_explains_itself`
  fails a `blocked` or `retired` file with no `notes`;
  `tests/test_gate.py::test_drift_ignores_retired_sources` covers a source
  removed from the registry altogether;
  `tests/test_gate.py::test_a_blocked_source_keeps_its_upcoming_listings_and_a_retired_one_does_not`
  and `tests/test_gate.py::test_drift_does_not_report_blocked_or_retired_sources_as_disappeared`
  cover sources kept as files with those statuses.
- **When it fires**: see [onboarding-sources.md](onboarding-sources.md#blocked-and-retired).

### S12. Reader submissions follow the same rules

- **Rule**: a submission from the Google Form is published only with a readable
  date that names a year, is added once, is never re-read after it is marked
  uploaded, and survives every scrape; a missing time makes it all-day.
- **Why**: on 2026-01-13 Cambridge's sync deleted every stored submission and
  re-added only the new ones; on 2026-01-14 the daily scrape was found deleting
  submissions, because only CI-skipped sources were preserved. A form's date
  field is free text to a reader, and a guessed year is a fabricated date.
- **Test**: `tests/test_submissions.py`: `test_dates_are_never_guessed`,
  `test_a_submission_already_stored_is_not_added_again`,
  `test_uploaded_rows_are_never_read_again`,
  `test_moderation_publishes_only_approved_rows`,
  `test_an_unreadable_sheet_fails_loudly`; preservation through a scrape is U4.
- **When it fires**: the sync's log and the workflow summary list what was
  skipped and why. Fix the row in the sheet; an unmarked row is read again on
  the next sync.

### S13. A place comes from the event, and no pin beats a wrong pin

- **Rule**: an event's venue, address and city come from the event's own
  fields first, then the registry's `venue` defaults, then the region's default
  city; coordinates come from `data/venues.yaml`, then the geocoder's cache,
  then (only with `GEOCODER=nominatim`) OpenStreetMap, and an answer outside the
  region is rejected.
- **Why**: on 2026-07-01 Cambridge found Somerville venues stamped "Cambridge"
  (494 events in other cities), because the geocoder took the first substring
  match and the validator defaulted the city. The 2026-10-05 audit found every
  A.R.T. performance placed at one building (wrong for 82 of 125), every Middle
  East show at the Mass Ave address (40 of 226 are at Sonia, round the corner),
  and 17 BostonShows.org rows as "Unknown Venue". A venue called "The
  Independent" has namesakes in other cities; a pin in the wrong state is worse
  than none.
- **Test**: `tests/sources/test_templates.py::test_an_events_own_place_beats_the_venue_defaults`;
  `tests/adapters/test_assabet.py::test_an_off_site_event_is_at_its_own_place`;
  `tests/test_geocoder.py`: `test_pinned_venues_match_by_name_alias_and_whole_words`,
  `test_a_city_is_read_from_an_address_only_where_it_is_the_city`,
  `test_answers_outside_the_region_or_not_a_venue_are_rejected`,
  `test_at_most_one_request_a_second`, `test_the_api_never_geocodes_over_the_network`.
- **When it fires**: a reader reports a wrong place or pin. Pin the venue in
  `data/venues.yaml` (with aliases) rather than patching coordinates in
  `data/events.json`.

---

## Duplicates and identity

Two different questions with two different answers. Within one source, a
duplicate is the same occurrence listed twice. Across sources, the same show is
described differently by each listing, so matching has to be fuzzy.

### U1. Within one source, only the same occurrence twice is a duplicate

- **Rule**: two events from the same source merge only when they have the same
  start, the same title ignoring case and punctuation, and compatible venues.
- **Why**: the deduplicator used to apply one fuzzy rule everywhere. The
  2026-10-05 audit found it removing 45 of 105 Longfellow House tours (10:00
  and 11:00 were "duplicates"), 33 Dance Complex classes ("Tap Level 2" at 7
  swallowed by "Tap Level 1" at 6), and about 26 City of Cambridge events a day
  (story times at different library branches).
- **Test**: `tests/test_deduplicator.py`:
  `test_back_to_back_sessions_of_one_programme_are_kept`,
  `test_similarly_named_classes_are_different_events`,
  `test_same_programme_at_two_branches_is_two_events`,
  `test_the_same_listing_twice_is_still_merged`.
- **When it fires**: a run's `deduplicated` count is far below `validated` for
  one source. `cal run <id>` shows the counts.

### U2. Across sources, match fuzzily, and only at the same venue

- **Rule**: events from different sources merge when titles are at least 85%
  similar (or one title of 8+ characters is contained in the other and the venue
  agrees), starts are within an hour, and venues are compatible; different
  venues never merge.
- **Why**: "Global Arts Live presents Brad Mehldau Trio" and "Brad Mehldau Trio"
  were published twice. A late show can straddle midnight between two listings
  ("The Midnight Hour" at 23:45 and 00:15).
- **Test**: `tests/test_deduplicator.py::test_a_presenter_prefix_does_not_hide_a_duplicate`,
  `::test_different_venues_are_never_the_same_show`,
  `::test_a_match_can_straddle_midnight`.
- **When it fires**: a reader sees one show twice. Check that both copies name
  the same venue; a venue missing from one copy weakens the match.

### U3. The venue's own listing beats an aggregator's

- **Rule**: when duplicates merge, the source with the lower `KIND_ORDER`
  wins (`requests`/`playwright` before `aggregator`), whatever order the events
  arrived in; the fuller description still fills in. Listing sites must be
  registered `kind: aggregator`.
- **Why**: BostonShows.org was registered as `requests`, so it ran early and
  won merges: about 50 events from Sanders Theatre, The Rockwell, Portico and The
  Sinclair were published under its name and links (fixed 2026-10-06). When
  Harvard Book Store was revived, 31 aggregator copies of its events were
  credited back to the store.
- **Test**: `tests/test_deduplicator.py::test_the_venue_wins_over_an_aggregator_whatever_the_order`.
- **When it fires**: a venue's events link to an aggregator. Check the
  aggregator's registry file has `kind: aggregator`.

### U4. Preserved events are reconciled, not appended

- **Rule**: events kept from a previous run (user submissions, CI-skipped and
  failed sources) go through deduplication against this run's events; a user
  submission always survives, a venue's own preserved listing beats a fresh
  aggregator copy, and otherwise the fresh copy wins.
- **Why**: preserved events used to be appended after deduplication, so "The
  Raven" was listed twice (scraped and submitted), and Somerville Theatre shows
  appeared once from the venue and once from BostonShows.org. A submission must
  win because the sync would re-add it otherwise.
- **Test**: `tests/test_deduplicator.py::test_preserved_events_no_longer_bypass_deduplication`,
  `::test_a_fresh_listing_beats_a_stale_preserved_one_of_equal_rank`.
- **When it fires**: duplicates that involve "User Submitted" or a local-only
  source.

### U5. The enrichment pass merges only at the same time and venue

- **Rule**: the enrichment agent's fuzzy pass needs starts within an hour and
  compatible venues, and prefers the venue's own source.
- **Why**: it matched on title and calendar day alone and kept the longer
  description regardless of source, so a 3 PM listing could swallow an 11 AM one
  and an aggregator could replace a venue.
- **Test**: `tests/test_deduplicator.py::test_enrichment_dedup_needs_the_same_time_and_venue`.
- **When it fires**: as U2.

### U6. Ids are derived from content and never minted by hand

- **Rule**: an event's id is a hash of `source_name | source_url |
  start_datetime | normalised title`, minted only by `Event.from_create()`;
  venue, description, image and category are deliberately excluded.
- **Why**: Cambridge assigned a fresh `uuid4()` every scrape. Measured across
  two consecutive daily runs before 2026-08-31, 269 of about 2,260 ids survived:
  88% churn. Click history no longer joined to events, onboarding preferences
  expired within a day, Editor's Picks had to match on titles, `git diff
  data/events.json` was about 9,000 lines of noise, and `.git` grew to 136 MB
  against an 18 MB tree. After the fix a nightly run diffed to +1 −2 ~1.
  Excluding fields we expect to extract better keeps an improved scraper from
  rotating every id.
- **Test**: **none** for id stability or for the `duplicate_id` invariant.
- **When it fires**: `cal diff` reports thousands of changes. Something minted
  ids another way, or changed the basis.

### U7. The stored file is in a deterministic order

- **Rule**: `data/events.json` is written sorted by `(start_datetime, id)`.
- **Why**: with stable ids, a stable order turns the daily git diff into a
  changelog, and `git log -S<id>` answers "when did this event change?".
- **Test**: **none.**
- **When it fires**: a diff that moves every line. Write through
  `sort_events()` in `src/utils/storage.py`.

---

## The publish gate

`src/quality/gate.py` runs inside `scrape.py` between building the publish set
and writing it. Three checks; only one is tunable.

| check | tunable? | blocks |
|---|---|---|
| catastrophic collapse | no | always |
| invariant violation | no | always |
| drift | `GATE_DRIFT` | only when `GATE_DRIFT=enforce` |

### G1. Catastrophic collapse blocks, absolutely

- **Rule**: a run may not leave fewer than 50% of the events currently published
  (`MIN_EVENTS_RATIO`), or fewer than 60% of the contributing sources
  (`MIN_SOURCES_RATIO`); this is measured against the data being replaced, needs
  no history, and ignores `GATE_DRIFT`.
- **Why**: rehearsing the gate on 2026-08-31 caught it *passing* a run that would
  have replaced 2,974 events with 232. Every scraper had failed, only the user
  submissions survived, and the fifteen "source disappeared" findings were drift,
  which only reported. Losing most of the calendar must not depend on a tunable.
- **Test**: `tests/test_gate.py::test_mass_collapse_is_blocked_regardless_of_drift_mode`,
  `::test_collapse_check_is_silent_without_a_previous_state`,
  `::test_a_normal_daily_change_is_not_mistaken_for_collapse`.
- **When it fires**: almost always mass scraper failure (a missing browser in
  CI, a network outage), not bad data. `cal run <id>` lists which scrapers
  failed. Fix the cause; never `--force` past a collapse.

### G2. Invariant violations block, absolutely

- **Rule**: any `error` from `check_invariants()` in `src/quality/invariants.py`
  blocks the run: `missing_required`, `duplicate_id`, `clock_stamped`,
  `tz_aware`, `timestamp_pileup`, `uniform_timestamp`, `date_out_of_range`,
  `nav_title`.
- **Why**: invariants need no baseline and no tuning, and were proven clean
  against real data before they were enforced: 0 violations on Cambridge's clean
  data on 2026-08-31, 5 on the run that shipped the bug. The 2026-08-31 run is
  blocked on invariants alone.
- **Test**: `tests/test_gate.py::test_healthy_run_passes`,
  `::test_fabricated_dates_are_blocked_on_invariants_alone`. Of these rules,
  only `clock_stamped` and `timestamp_pileup` are asserted by a test;
  `nav_title`, `uniform_timestamp`, `date_out_of_range`, `duplicate_id` and
  `missing_required` are **untested**.
- **When it fires**: `cat data/quarantine/<run-id>/report.txt`. Fix the
  scraper and `cal repair` it. Do not make invariants configurable.

### G3. Drift is source-relative, and reports until enforced

- **Rule**: each source's shape is compared with its own last 30 passing runs
  (`data/fingerprints.json`); drift is silent until a source has three runs of
  history and ten events, and its errors block only when `GATE_DRIFT=enforce`
  (default `report`).
- **Why**: global thresholds are actively harmful. Against healthy data on
  2026-08-31, naive global rules flagged 9 of 24 sources: Brattle Theatre for
  having one venue (it is one cinema), A.R.T. for 11 distinct titles across 156
  events (11 productions), City of Cambridge for repeating titles (weekly story
  times). A monitor that cries wolf is worse than none, because its reader learns
  to skip it. Drift starts in report mode because a mistuned baseline blocks good
  data: after the Dance Complex fix it flagged "2341% of normal", a true
  statement about a stale baseline that should not have stopped a deploy.
- **Test**: `tests/test_gate.py::test_drift_blocks_only_when_enforced`,
  `::test_drift_ignores_retired_sources`.
- **When it fires**: read the metric. A rise after you fixed a scraper means
  rebaseline (G5); a fall with no code change means the source broke.

### G4. Only a run that passed defines normal

- **Rule**: fingerprints are recorded to the baseline only after the gate
  passes and the run is written.
- **Why**: Cambridge's old health monitor kept five runs and recorded every
  one. City of Cambridge degraded over several days, and its broken state became
  the baseline before anything noticed. History is 30 runs for the same reason.
- **Test**: **none.** The ordering lives in `ScraperOrchestrator.run_all()` in
  `scrape.py`.
- **When it fires**: n/a. Do not call `record()` anywhere else.

### G5. Rebaseline a source after fixing it

- **Rule**: after a fix changes what a source returns,
  `cal check "<source>" --rebaseline` forgets its history so drift relearns it.
- **Why**: a scraper that returned 4 events because it was broken has a
  baseline of 4, and the corrected 132 reads as a 3300% duplicate explosion for
  as many runs as it takes to age out (found 2026-09-01). On 2026-10-06 the
  baselines of 19 fixed sources were reset at once.
- **Test**: **none** for `reset_baseline()`.
- **When it fires**: you shipped a scraper fix. Rebaseline in the same commit.

### G6. A blocked run is quarantined, and someone is told

- **Rule**: a blocked run writes `data/quarantine/<run-id>/` (`events.json`,
  `gate.json`, `report.txt`), leaves `data/events.json` untouched, exits
  non-zero, and opens a GitHub issue with the report.
- **Why**: Cambridge's workflow used to commit and push unconditionally; there
  was no step between "the scraper finished" and "readers see it". The bad run
  is the evidence: discarding it means diagnosing from the symptom.
- **Test**: `tests/test_gate.py::test_quarantine_preserves_the_evidence`. The
  issue is **untested**.
- **When it fires**: [operations.md](operations.md#when-the-gate-blocks-a-run).

### G7. `--force` is the escape hatch, and report mode never blocks

- **Rule**: `python scrape.py --force` (or the workflow's `force` input)
  publishes past a failing gate; `GATE_MODE=report` evaluates without ever
  blocking.
- **Why**: a legitimate large change (a venue posting its whole season at once)
  will eventually trip the gate, and the fix for that must never be "disable
  the gate".
- **Test**: `tests/test_gate.py::test_force_overrides_everything`,
  `::test_report_mode_never_blocks`.
- **When it fires**: [operations.md](operations.md#overriding-the-gate).

---

## Monitoring

### M1. Measure shape, not volume

- **Rule**: each source is fingerprinted on every run: event count, date span,
  most events on one day, most on one timestamp, distinct title ratio, distinct
  venues, distinct image ratio, median description length, null-venue rate, and
  earliest and latest start.
- **Why**: the old health monitor watched event count. It scored the
  2026-08-31 run as healthy: 359 events, 143% of a 250.6 average. On the same
  data, date span was 16 days against 63, most events on one day 124 against
  40, most on one timestamp 117 against 8, and distinct start times 140 against
  596. The count went *up*. Six of the eight ways this system breaks are
  invisible to a count ([architecture.md](architecture.md#failure-taxonomy)).
- **Test**: `tests/test_gate.py::test_drift_blocks_only_when_enforced` squeezes
  a source's dates without changing its count and asserts drift sees it.
- **When it fires**: [operations.md](operations.md#reading-a-run-record).

### M2. Monitoring derives from the registry

- **Rule**: the CI monitor, the health monitor and drift enumerate sources from
  `src/sources.py`; nothing keeps its own list.
- **Why**: S1. Four scrapers ran unmonitored for months.
- **Test**: `tests/test_docs.py::test_monitoring_covers_every_registered_source`.
- **When it fires**: `cal doctor` prints "unmonitored sources".

### M3. Every run leaves a record

- **Rule**: every scrape writes `data/runs/<run-id>.json` with each scraper's
  status, duration, yield and error, the validator's rejections by reason, every
  source's fingerprint, the gate decision and the diff summary; 90 are kept, and
  run ids are genuine UTC.
- **Why**: the 2026-08-31 root cause was recovered by noticing that
  `13:29:26.288025` plus fourteen days was the scrape time, because nothing about
  the run survived it. Run ids were once local time with a `Z` pasted on, so a
  09:44 EDT run sorted before an 11:32 UTC run two hours older and `cal doctor`
  reported a stale failure (fixed 2026-09-01).
- **Test**: **none.**
- **When it fires**: n/a. Read it with `cal runs` and `cal run <id>`.

### M4. Degrade visibly

- **Rule**: optional steps (agents, enrichment, the deploy hook, chat) skip
  rather than crash when a key is missing, and every skip is logged as a
  warning; treat "the step was skipped" as a finding.
- **Why**: graceful *and silent* is how the 2026-08-31 run passed every check
  it had.
- **Test**: that each optional feature is off, not broken, when unconfigured:
  `tests/test_api_chat.py::test_chat_is_off_unless_configured`,
  `tests/test_api_featured.py::test_without_admin_token_set_nobody_can_choose`,
  `tests/test_submissions.py::test_not_configured_is_a_quiet_no_op`,
  `tests/test_geocoder.py::test_no_network_unless_switched_on`. That each skip
  is *logged* has **no test**.
- **When it fires**: a warning in the run log or the workflow summary.

---

## Frontend and API

### F1. All-day events carry their flag through the API

- **Rule**: `all_day` is part of every event the API returns, including the slim
  list the frontend reads.
- **Why**: when scrapers stopped inventing times (D7), date-only listings
  arrived at 00:00 and the list, map and calendar links rendered them as
  "12:00 AM" (fixed 2026-10-06).
- **Test**: `tests/test_api_all_day.py::test_slim_events_carry_the_flag`.
- **When it fires**: an all-day event shows a time on the site.

### F2. The upcoming filter keeps an all-day event through its last day

- **Rule**: an all-day event (or run) stays in "upcoming" results until its end
  date has passed; a timed event drops off when it starts.
- **Why**: the filter compared starts with the current time, so an all-day
  event vanished the moment its day began, and "Midwinter Revels" (December
  11-28) would have disappeared on its opening day.
- **Test**: `tests/test_api_all_day.py::test_an_all_day_run_stays_upcoming_through_its_last_day`,
  `::test_a_timed_event_is_not_affected`.
- **When it fires**: a multi-day festival is missing from today's list.

### F3. Calendar exports use dates for all-day events and floating local time otherwise

- **Rule**: the `.ics` export writes an all-day event as `DTSTART;VALUE=DATE`
  with an exclusive `DTEND` (the day after its last day), and a timed event as a
  floating local `DTSTART` with no `Z` and no zone.
- **Why**: an all-day event exported at midnight lands on a reader's calendar as
  a 12 AM appointment. A floating time is what a local-events calendar means:
  7:30 PM at the venue.
- **Test**: `tests/test_api_all_day.py::test_calendar_export_uses_dates_not_midnight`.
- **When it fires**: a reader's calendar app shows the wrong day or a midnight
  start.

### F4. The frontend hides the time only for all-day events

- **Rule**: the site shows no clock time when `all_day` is true, and shows
  "12:00 AM" for a timed event that really starts at midnight.
- **Why**: Cambridge's detail page treated every 00:00 as "no published time"
  until 2026-10-06; a 12 AM set at a jazz club is a real start.
- **Test**: **none.** The frontend has no tests.
- **When it fires**: a late-night show shows no time, or a festival shows
  12:00 AM.

### F5. The frontend shows the stored wall clock and never converts it

- **Rule**: the frontend displays times as stored (local wall clock for the
  region) and never converts to the viewer's zone; where a format requires a zone
  (schema.org `startDate` in prerendered pages), the offset is computed per date
  for the region's zone, so it is right on both sides of a clock change.
- **Why**: D4. A reader in another zone must see the venue's time.
- **Test**: **none.** The frontend has no tests.
- **When it fires**: a reader outside the region sees shifted times.

### F6. Query parameters are compared in the same terms as stored times

- **Rule**: a datetime arriving in a query parameter is converted to naive local
  time before it is compared with stored events.
- **Why**: on 2026-08-31 `/events?start_date=` returned 500 (`TypeError`
  comparing aware and naive); so did `/stats`, which also called `.value` on a
  category that is already a string (both models set `use_enum_values`).
- **Test**: `tests/test_api_featured.py::test_read_endpoints_work_over_the_same_data`
  calls `/stats`. Converting query parameters has **no test**.
- **When it fires**: a 500 from a filtered request. `cal doctor --live` probes
  `/events?start_date=...` and `/stats`.

### F7. Link back to the venue, and credit it

- **Rule**: every event carries the venue's own `source_url` and its
  `source_name`, and the site links to and credits them; a placeholder link such
  as `#` is replaced by the event's page, its show's page, or the calendar page.
- **Why**: the calendar exists to send readers to venues
  ([access-policy.md](access-policy.md#link-back-and-credit)). On 2026-10-05
  three Central Square Theater talks were published with `source_url` "#".
- **Test**: `tests/adapters/test_eventon.py::test_talks_link_somewhere_real`;
  `tests/adapters/test_drupal_listing.py::test_a_row_without_a_detail_page_links_to_its_day`.
  The `missing_required` invariant also rejects an empty `source_url`, but has
  no test of its own.
- **When it fires**: a reader clicks through to nowhere.

### F8. An unknown price is not "free"

- **Rule**: a "free" filter matches only events whose cost says free; an event
  with no price is unknown.
- **Why**: on 2026-09-01 Cambridge's prerendered `/free` page listed 190 events
  and the live `/free` filter 3,286, because the client treated a missing price
  as free (3,097 events had no price). Same rule as never inventing a date.
- **Test**: **none.** The frontend has no tests.
- **When it fires**: the free filter shows most of the calendar.

### F9. Editor's Picks need a token, live off the deploy, and never vanish by accident

- **Rule**: choosing picks requires `ADMIN_TOKEN` as a bearer token (unset, nobody
  can); picks are written to `FEATURED_PATH`, which should be on a persistent
  volume; a pick covers every date of an event; picks whose events have passed
  are dropped, but never when the events file is empty or unreadable.
- **Why**: Cambridge's pick endpoints had no check at all: anyone who found them
  could rewrite the homepage's picks. On 2026-02-25 picks disappeared on a
  redeploy, because Railway's filesystem is replaced on every deploy, and the
  daily data commit redeploys daily. On 2026-07-09 stale picks had accumulated
  for events long past.
- **Test**: `tests/test_api_featured.py`: `test_choosing_picks_needs_the_token`,
  `test_without_admin_token_set_nobody_can_choose`,
  `test_a_pick_covers_every_date_and_can_be_removed`,
  `test_featured_path_keeps_picks_off_the_deployed_filesystem`,
  `test_saving_drops_picks_whose_events_are_gone`,
  `test_with_editors_picks_off_there_is_nothing_to_find`.
- **When it fires**: picks vanish after a deploy (set `FEATURED_PATH` on a
  volume), or the admin page answers 503 (set `ADMIN_TOKEN`).

---

## Edge cases

Each row is a shape of data a real venue served. "Adapter tests" means the
fixtures in `tests/adapters/`, captured from real pages.

| Case | How it is handled | Test |
|---|---|---|
| Date with no time | `all_day: true` at 00:00 (D7) | `tests/adapters/test_ical.py::test_a_date_only_event_is_all_day`, and one per adapter (D7) |
| Time text present but unreadable ("TBA" aside) | skip with a warning | `tests/sources/test_templates.py::test_undated_unreadable_and_cancelled_listings_are_skipped` |
| A start that will not parse at all | skip; never `datetime.now()` | `tests/adapters/test_squarespace.py::test_an_unreadable_start_is_skipped_not_now` |
| "TBA" / "All day" as a time | all-day on its date (`read_time()` in the custom-scraper template) | untested; an empty time is, in `tests/sources/test_templates.py::test_listed_times_are_read_and_nothing_is_invented` |
| Date with no year | year from the printed weekday; no match, skip (D8) | `tests/sources/test_templates.py::test_undated_unreadable_and_cancelled_listings_are_skipped` |
| Time with no meridiem ("7:30") | skip (D9) | `tests/sources/test_templates.py::test_undated_unreadable_and_cancelled_listings_are_skipped` |
| Range with one meridiem ("6:00-7:00 PM", "8-9 am") | start borrows the end's | `tests/adapters/test_assabet.py::test_the_start_shares_the_end_meridiem`; `tests/sources/test_templates.py::test_listed_times_are_read_and_nothing_is_invented` |
| "Doors 7:00 PM / Show 8:30 PM" | the start is the show, not the doors | `tests/sources/test_templates.py::test_listed_times_are_read_and_nothing_is_invented` |
| Multi-day run with no time ("Dec 11-28") | all-day on the first day, `end_datetime` the last; upcoming until the last day | `tests/test_api_all_day.py::test_an_all_day_run_stays_upcoming_through_its_last_day` |
| Set from 12-1 AM listed under the evening before | dated from the event page; belongs to the next calendar day | `tests/test_deduplicator.py::test_a_match_can_straddle_midnight` (dedup side only) |
| A real midnight start | shown as 12:00 AM; only `all_day` hides a time (F4) | untested (frontend) |
| Evening event in a UTC timestamp | converted whole; stays on its local day | `tests/test_event_timezones.py::test_a_late_evening_event_keeps_its_own_day` |
| Across a clock change | conversion uses the zone's rules for that date | `tests/test_event_timezones.py::test_conversion_respects_daylight_saving` |
| Epoch read on a UTC CI runner | explicit UTC conversion; never `fromtimestamp()` bare (D5) | `tests/adapters/test_eventon.py::test_times_do_not_depend_on_the_machine_zone` |
| Source in another zone than the calendar | converted, not copied; a cross-check is done in the calendar's zone | `tests/adapters/test_tribe.py::test_times_are_stored_in_the_calendars_own_zone`; `tests/adapters/test_drupal_listing.py::test_a_venue_in_another_zone_is_not_shifted_silently` |
| All-day date in a feed from another zone | keeps the date it is written on | `tests/adapters/test_localist.py::test_all_day_dates_survive_a_different_calendar_zone` |
| Epoch milliseconds with noise (`...400563`) | floored to the minute | `tests/adapters/test_squarespace.py::test_epoch_milliseconds_become_local_wall_clock_on_the_minute` |
| Offset that is wrong half the year (EventON `-4:00`) | offset discarded; wall clock cross-checked against the epoch (D6) | `tests/adapters/test_eventon.py::test_every_start_matches_the_time_on_the_card` |
| Two renderings of a start disagree | skip (D6) | `tests/adapters/test_eventon.py::test_disagreeing_renderings_are_skipped`, and per adapter (D6) |
| End time of 23:59 meaning "no end" (EventON) | not published as an end | `tests/adapters/test_eventon.py::test_eventon_placeholder_end_is_not_published` |
| `DTEND` equal to `DTSTART` | no end time | `tests/adapters/test_ical.py::test_times_match_the_venues_listing` |
| All-day `DTEND` in iCal | exclusive: the day after the last day | `tests/test_api_all_day.py::test_calendar_export_uses_dates_not_midnight` (export side) |
| iCal recurring run (`RRULE` with `EXDATE`, `RECURRENCE-ID`) | expanded in local wall clock; exclusions and moves applied; window anchored on `DTSTAMP` | `tests/adapters/test_ical.py::test_a_recurring_run_lists_every_performance`, `::test_an_outdoor_run_keeps_its_exclusions_end_time_and_place`, `::test_a_series_keeps_its_own_wall_clock_across_a_clock_change` |
| iCal `VALARM` nested inside a `VEVENT` | ignored; cannot overwrite the event's fields | `tests/adapters/test_ical.py::test_an_alarm_does_not_overwrite_the_event` |
| Feed returns HTTP 200 with an empty body, or HTML | treated as a failure, not an empty calendar | `tests/adapters/test_ical.py::test_a_feed_that_is_not_a_calendar_fails_the_source` |
| Page that is no longer the calendar (redesign, wrong URL) | the source fails loudly | `tests/adapters/test_eventon.py::test_a_page_without_eventon_settings_fails_loudly`; `tests/adapters/test_squarespace.py::test_a_page_that_is_not_an_events_collection_fails_the_source`; `tests/adapters/test_jsonld.py::test_json_ld_without_events_is_empty_and_no_json_ld_is_an_error` |
| Bot check that never clears | `ScrapeRefusedError`; nothing clicks | `tests/adapters/test_indiecommerce.py::test_a_challenge_that_does_not_clear_fails_the_source` |
| Rate-limited host (challenge on a second request) | one request per page, no retries; a refusal fails the source | `tests/adapters/test_tribe.py::test_a_refusal_or_a_challenge_fails_the_source` |
| 403 or bot-check page | `ScrapeRefusedError`; stored events kept (S4, S5) | `tests/test_base_playwright_scraper.py::test_zero_events_after_a_403_is_a_failure` |
| Venue with genuinely nothing scheduled | `ok`, zero events | `tests/test_base_playwright_scraper.py::test_an_empty_listing_that_loaded_is_not_a_failure` |
| One secondary page refused, the rest fine | events kept, refusal logged | `tests/test_base_playwright_scraper.py::test_events_found_despite_one_refused_page_are_kept` |
| Pagination past a sanity cap | error logged with what was not read (S6) | `tests/adapters/test_tribe.py::test_hitting_the_sanity_cap_is_an_error` |
| Pager repeats a page, or wraps to page 1 past the end | stop on the first page that adds nothing new | `tests/adapters/test_drupal_listing.py::test_paging_stops_when_a_page_adds_nothing_new` |
| Calendar's own day-pager marked `rel="next"` | not mistaken for the listing's next page | `tests/adapters/test_drupal_listing.py::test_paging_stops_at_the_last_page` |
| Progressively rendered list | read the JSON the page fetches, or `wait_for_stable_count()`; never `wait_for_selector` | untested |
| Event data inside `<template>` elements | each template re-parsed on its own | `tests/adapters/test_indiecommerce.py::test_porter_reads_the_whole_month_with_real_cities` |
| JSON-LD with raw newlines | parsed with `strict=False` | `tests/adapters/test_jsonld.py::test_events_are_found_in_graphs_lists_and_series`; `tests/adapters/test_assabet.py::test_every_scheduled_event_is_read` |
| Entities encoded twice (`&amp;#8217;`) | decoded up to three times (`clean()`) | `tests/adapters/test_assabet.py::test_every_scheduled_event_is_read`; `tests/adapters/test_tribe.py::test_text_is_unescaped_and_free_of_page_builder_markup` |
| HTML, scripts or shortcodes in a description | stripped to text | `tests/adapters/test_ical.py::test_descriptions_are_prose_not_the_ticket_buttons_script`; `tests/adapters/test_drupal_listing.py::test_inline_style_is_not_prose`; `tests/adapters/test_tribe.py::test_text_is_unescaped_and_free_of_page_builder_markup` |
| Description is just the title, or missing | not published as the description; one is built from facts ("<title> at <venue>") | `tests/adapters/test_eventon.py::test_a_placeholder_description_is_not_published`; `tests/sources/test_templates.py::test_an_events_own_place_beats_the_venue_defaults` |
| Tribe `venue`/`image` as dict, `[]`, list or `False` | every shape accepted | `tests/adapters/test_tribe.py::test_polymorphic_venue_and_image_fields` |
| `performer` or `image` as one value or a list | both accepted | `tests/adapters/test_jsonld.py::test_a_show_without_its_own_image_uses_its_performers` |
| Leading "CANCELLED", "POSTPONED", "RESCHEDULED:", or a cancelled status | skipped; a title merely containing the word is kept | `tests/adapters/test_squarespace.py::test_rescheduled_notices_are_skipped_but_mentions_kept`; `tests/adapters/test_tribe.py::test_cancelled_hidden_and_unpublished_listings_are_skipped`; `tests/adapters/test_jsonld.py::test_cancelled_and_postponed_events_are_skipped`; `tests/adapters/test_eventon.py::test_cancelled_listings_are_skipped`; `tests/adapters/test_indiecommerce.py::test_cancelled_events_are_skipped` |
| Deadlines, closures, holidays, placeholders | skipped (S7) | `tests/adapters/test_drupal_listing.py::test_only_attendable_events_are_published`; `tests/adapters/test_localist.py::test_holidays_are_not_events_and_virtual_events_are_online` |
| Members-only, internal or private event | skipped; public flag first, the event's own words override | `tests/adapters/test_localist.py::test_only_events_open_to_the_general_public`, `::test_institution_rules_follow_the_affiliation_param`; `tests/adapters/test_squarespace.py::test_private_bookings_are_not_public_events` |
| Event outside the region on a regional page | skipped, judged from its own address or category (S7) | untested: no adapter filters by region |
| URL category filter that the site ignores | filter per item, not by URL | untested |
| Online event | venue "Online", no street | `tests/adapters/test_assabet.py::test_the_venue_comes_from_the_branch_or_says_online`; `tests/adapters/test_tribe.py::test_venues_rooms_and_online_events`; `tests/adapters/test_jsonld.py::test_places_virtual_locations_and_defaults`; `tests/adapters/test_drupal_listing.py::test_virtual_events_are_online` |
| Event names no place | the registry's `venue` defaults | `tests/adapters/test_indiecommerce.py::test_an_unplaced_event_without_a_home_tag_falls_back_to_the_registry_venue`; `tests/adapters/test_jsonld.py::test_places_virtual_locations_and_defaults` |
| Venue hosts an event elsewhere | the event's own address wins over the defaults | `tests/sources/test_templates.py::test_an_events_own_place_beats_the_venue_defaults`; `tests/adapters/test_assabet.py::test_an_off_site_event_is_at_its_own_place`; `tests/adapters/test_ical.py::test_locations` |
| Empty location that still carries map coordinates (Squarespace's default, in New York) | coordinates used only with an address | `tests/adapters/test_squarespace.py::test_the_venue_is_the_items_own_location_never_squarespaces_default_map` |
| The same talk re-posted under two URLs | one event | `tests/adapters/test_localist.py::test_reposted_events_are_one_event` |
| Multi-day event repeated in every day it spans | one event (deduplicated on the item's id) | untested |
| Same programme at two branches at once | two events | `tests/test_deduplicator.py::test_same_programme_at_two_branches_is_two_events` |
| Back-to-back sessions with the same title | separate events | `tests/test_deduplicator.py::test_back_to_back_sessions_of_one_programme_are_kept` |
| Curly vs straight apostrophe, or a copy missing its venue | same occurrence | `tests/test_deduplicator.py::test_the_same_listing_twice_is_still_merged` |
| "X presents Y" vs "Y" at the same venue | duplicate | `tests/test_deduplicator.py::test_a_presenter_prefix_does_not_hide_a_duplicate` |
| Same title at different venues | never merged | `tests/test_deduplicator.py::test_different_venues_are_never_the_same_show` |
| A submission duplicates a scraped event | the submission stays | `tests/test_deduplicator.py::test_preserved_events_no_longer_bypass_deduplication` |
| First ever run, nothing published yet | collapse check silent | `tests/test_gate.py::test_collapse_check_is_silent_without_a_previous_state` |
| Fixed scraper, stale baseline | `cal check "<source>" --rebaseline` (G5) | untested |
| A source removed from the registry | drift silent about it | `tests/test_gate.py::test_drift_ignores_retired_sources` |
| Site refuses the `python-requests` default user-agent | the honest named user-agent (S3) | `tests/test_detect.py::test_the_default_fetch_identifies_honestly` |
| `cal detect` meets a 403 or an interstitial | reported as `blocked`; no adapter matched against the challenge page | `tests/test_detect.py::test_a_challenge_page_is_reported_blocked_and_nothing_else`, `::test_a_bare_403_is_blocked` |
| Two venue names that slugify alike ("Lou's", "Lou’s") | slugs must fold typographic punctuation (Cambridge's page generator failed on a collision) | untested (frontend) |

---

## How testing works

```bash
.venv/bin/python -m pytest tests/ -q        # everything, offline, a few seconds
.venv/bin/python -m pytest tests/adapters -q
```

**Offline fixtures.** A scraper is pure (URL in, events out), so its test feeds
it a saved copy of the real page and asserts on the events. Fixtures are
gzipped under `tests/fixtures/`: the adapters' under `tests/fixtures/adapters/`,
each registered source's under `tests/fixtures/<slug_>/`. Read one with
`read_fixture()` from `tests/conftest.py` (or the `fixture_html` fixture); a
missing fixture skips its test with the command to capture it rather than
failing. **Every scraper bug leaves a fixture behind**: the page that broke it
is committed, so the failure cannot return silently.

**Record once, replay forever.** `.venv/bin/python -m tests.sources.record
"<source>"` runs a source once against the live site and saves every HTTP
response it read, with a manifest. The `serve` fixture
(`tests/sources/conftest.py`) then answers the scraper's requests from that
capture alone and raises on anything it did not record, so a source that reads
an API, a feed and a second page is tested end to end, offline.
`tests/sources/_template.py` is the test to copy. `cal scrape "<source>"
--save-fixture` saves only the registry URL's page. A browser's traffic is not
recorded: save the rendered page and test the parse step.

**The `offline` fixture** (`tests/conftest.py`) replaces `requests.get`,
`requests.post`, `requests.Session.request` and `urllib.request.urlopen` with a
function that raises. Any test that reaches the network fails loudly instead of
quietly depending on a venue's uptime. It caught its first bug on the day it was
written: a test patched `fetch_html`, which that scraper never calls, and was
silently testing the live API.

**The `zone` fixture** runs a test as if `calendar.config.yaml` named another
time zone. The adapter fixtures were captured from Cambridge's venues, so their
tests call `zone("America/New_York")`; a source in your own region needs
nothing. To prove a parser does not depend on the machine's zone, also run it
under `TZ=UTC` and a far-off zone such as `Pacific/Honolulu` (D5).

**Saved incident data.** `tests/fixtures/incidents/` holds runs that really
shipped and broke a rule, read with `incident_events()`:
`2026-08-31-clock-stamped-run.json.gz` is the run that put 117 events on one
day, and the gate test replays it. `tests/fixtures/sample/` holds Cambridge's
published calendar of 2026-10-06 (`sample_events()`), a healthy calendar for
gate and drift tests. `tests/fixtures/demo_registry/` holds Cambridge's sources
by name and kind only, swapped in by the `demo_registry` fixture so that engine
tests (dedup ranking, the gate, monitoring) do not change meaning as you register
your own sources.

**Invariants on every fixture.** An adapter or source test should end by
holding its output to the same absolute rules the gate applies:

```python
from src.quality.invariants import check_invariants, errors

events = [e.model_dump(mode="json") for e in scraper.run()]   # under `serve` or `offline`
assert not errors(check_invariants(events))
```

That catches a parser regressing into fabricated dates, chrome titles or
offset-carrying times without the venue being online. Every adapter test file
in `tests/adapters/` does this, and the template in `tests/sources/` does it for
every source copied from it.
`tests/test_docs.py::test_stored_data_satisfies_documented_invariants` applies
three of them to `data/events.json` itself.

**Live spot checks during onboarding.** Fixture tests prove a parser reads a
page the way it did on capture day; they cannot prove it reads it the way the
venue means it. Before a source ships, `cal scrape "<source>"` and compare at
least two events against the venue's own page: one in the evening, and one on
the far side of the next clock change if the listing reaches that far. Cambridge
recorded these in every commit ("Spot-checked That Motown Band (Nov 14, 20:00)
against the page"); do the same. See [onboarding-sources.md](onboarding-sources.md).

**What the tests cannot see** is semantic drift: a selector that still matches
but now means something else. Fixtures catch structural change; only a live
check against a known-good page catches the rest. The monthly `/audit` exists for
that ([operations.md](operations.md#the-monthly-audit)).

---

## Untested

Rules and cases above with no test in this repo. Each is a candidate for the
next test written; none should be described elsewhere as "enforced".

**Invariants with no test of their own**

- D3: `uniform_timestamp`.
- D11: `date_out_of_range`, and the validator's −30 days / +2 years window.
- G2: `nav_title`, `duplicate_id`, `missing_required`.
- D8: `year_for_weekday()` itself (covered only through the custom-scraper
  template's test).

**Pipeline behaviour**

- S5: `scrape_local.py` keeping a failed source's events, deduplicating, and
  refusing to write on invariant errors.
- S8: CI skipping and preserving `runs_in_ci: false` sources; the weekly local
  job.
- U6: id stability.
- U7: deterministic order of `data/events.json`.
- G4: recording fingerprints only after a passing run.
- G5: `reset_baseline()` / `cal check --rebaseline`.
- G6: opening a GitHub issue for a blocked run.
- M3: run records (contents, retention, UTC ids).
- M4: that a skipped optional step is logged.
- S10: pacing in adapters and custom scrapers other than those named.

**Frontend and API**

- F4, F5, F8 and slug collisions: the frontend has no tests.
- F6: converting datetimes in query parameters.

**Edge cases marked "untested" in the table**: "TBA" as a time, progressive
rendering, a URL category filter the site ignores, events outside the region,
multi-day events repeated in every day they span.
