# Access policy

How this calendar reads other people's websites, and the line it does not cross.
It applies to every source, every adapter, every custom scraper, and to anyone
(or any Claude Code session) onboarding one.

A community calendar exists to send readers to venues. Venues are the partners,
not the obstacle. Read them the way you would want your own site read: openly,
lightly, and with credit.

## The line never crossed

- **No impersonation.** Never send a user-agent, client hints or headers that
  claim to be something else, and never use flags or plugins that hide browser
  automation.
- **No solving challenges.** Never click, solve, or pay a service to solve a
  CAPTCHA or bot check. A challenge that does not clear on its own is an answer.
- **No credentials.** Never log in, use a member or staff account, or read
  anything behind a login or paywall.
- **No evasion.** Never rotate IP addresses or use proxies to get round a block.

These are not tunable per source. If a source can only be read by crossing one,
it is not read: mark it blocked and ask the venue for a feed.

## Identify honestly

Every request names this calendar. `src/config.py` builds the user-agent once,
from `calendar.config.yaml`:

```
<site.short_name>/1.0 (+<site.url>)          e.g.  KQEDEvents/1.0 (+https://events.example.org)
<site.short_name>/1.0 (+mailto:<contact>)    when there is no site URL yet
```

Fill in `site.url` or `site.contact_email` before the first scrape: it is how a
venue's webmaster finds you instead of just blocking you. Plain-HTTP scrapers
send it through `BaseScraper.get_browser_headers()`; browser-driven scrapers send
the browser's own user-agent and nothing else.

Why it matters beyond principle (dated history from Cambridge Calendar):

- A spoofed user-agent is a contradiction bot protection looks for. Cambridge's
  Playwright base claimed macOS while the browser's client hints said Linux;
  on 2026-09-01 Porter Square Books returned 403 to the spoof and 200 to the
  browser's own user-agent, from the same browser.
- On 2026-10-06 every remaining spoof was removed (Windows Chrome 120 in
  Selenium, macOS Chrome 120 for plain HTTP). Every source answered the honest
  user-agent exactly as it had the spoof.
- Some servers refuse the bare `python-requests` default and serve a named
  client happily: Somerville Theatre's nginx and The Middle East both did.

The answer to a 403 is never a different user-agent string. See
[rules S3](rules-and-edge-cases.md#s3-identify-honestly).

## Politeness and rate limits

| Do | Why |
|---|---|
| Onboard on a budget: about five requests per venue to detect it (`cal detect` plus `robots.txt`), one recording run, a few spot checks; one worker per host | every venue onboarded is a site that did not ask to be read; develop against saved copies |
| Read the platform's feed or API, not page after page of HTML | one request to Arts at the Armory's `/events.ics` replaced 7 listing pages and 81 detail pages |
| One request per page of data; pause about a second between pages | Multicultural Arts Center answers 429 after a short burst; its scraper went from about 40 requests a run to 7, a second apart |
| Scrape once a day (the daily job), and only the window you publish | recurring classes can run to thousands of instances; bound them |
| Stop on a 429 or a challenge; never retry it in a loop | Longy's challenge is keyed on the caller's IP and was tripped by repeated testing from one machine (2026-09-01) |
| Read as little as possible from a site that is sensitive to automation | the two bookstores Cambridge reads in a visible browser put follow-up pages behind a check after a day of testing; they now read one month per run |
| Cache what does not change | the geocoder caches every answer, misses included, and asks OpenStreetMap at most once a second |

While developing a scraper, save a fixture once and iterate against it offline
(`cal scrape "<source>" --save-fixture`), rather than re-fetching the live site
on every change.

## Link back and credit

- Every event carries `source_url`, the venue's own page for that event (a ticket
  link where there is one), and `source_name`. The site links each event to its
  `source_url` and names the source. A placeholder link (`#`) is replaced with
  the event's or the show's own page, never left dead.
- The calendar republishes what venues publish to promote their events: the
  title, date, place, a description and an image URL. If a venue asks you to
  change or remove any of it, do so and record the request (below).
- Aggregators are credited too, but a venue's own listing always outranks an
  aggregator's copy of it ([rules U3](rules-and-edge-cases.md#u3-the-venues-own-listing-beats-an-aggregators)).

### When a venue asks

Honour a venue's request to exclude events, correct them, or stop scraping it.
Record it where the next person will look: in the adapter params or custom
scraper, with a comment naming who asked and when, and in the source's registry
`notes`. Cambridge's example: on 2026-09-02 Harvard GSD's public programmes office
confirmed two listings were not open to the public; they are excluded by slug,
with the reason and the date beside them, because "why is this one missing?" is
otherwise unanswerable six months later.

## robots.txt

`/add-source` reads `https://<venue>/robots.txt` right after `cal detect`, for
the paths the source will read; by hand, do the same
([onboarding-sources.md](onboarding-sources.md#1-detect-then-robotstxt)). If the
events pages or the feed are disallowed for all user-agents, or for ours, the
venue is registered as blocked, with `robots.txt` as the evidence, and asked for
a feed. A `Crawl-delay` sets the minimum pause between requests. Anything other
than "allowed" goes in the source's `notes`.

The scrapers themselves do not read `robots.txt` on every run; the check happens
once, at onboarding, and again whenever someone audits the source.

## When a site blocks you

A block looks like a 401, 403 or 429, a bot-check page ("Just a moment...",
"Performing security verification") that does not clear, or a challenge on
every visit.

**At onboarding**, one refusal is enough. `cal detect` reports the page as
`blocked`, and the venue is registered `status: blocked` with dated notes and no
further requests: no retry, no other user-agent, no browser "to see if it gets
through". Repeated probing is what turns a soft block into a hard one. The one
exception is a sanctioned door the venue offers publicly (an iCal link, a
documented API), tried once. `/onboard` then drafts the outreach email in its
report ([onboarding-sources.md](onboarding-sources.md#blocked-and-retired)).

**For a source that was working**, the pipeline already does the safe thing on
the first night: the scraper raises, the run records the source as failed with
the status and URL, and the source's upcoming events are kept
([rules S4, S5](rules-and-edge-cases.md#s4-a-refused-page-is-a-failure-not-an-empty-listing)).
Then:

1. **Confirm it.** One failure is noise; a source failing on consecutive runs,
   from CI and from a normal connection alike, is a block. If it fails only in
   CI, the venue is blocking GitHub's IP ranges: set `runs_in_ci: false` and run
   it locally ([onboarding-sources.md](onboarding-sources.md#sources-ci-cannot-reach)).
2. **Look for a sanctioned door.** An iCal or JSON feed, a public API, a
   partner listing service the venue already feeds. Use one if it exists.
3. **Ask.** Email the venue for a feed or a submission arrangement
   ([venue-outreach-email.md](venue-outreach-email.md)). Many venues block bots
   by default and are glad to be listed once asked.
4. **Mark it blocked** once the block is confirmed: `status: blocked` in its
   registry file, with the evidence and date in `notes`. The calendar stops
   sending requests the venue has said it does not want.
5. **Mind its listings.** A source marked `blocked` keeps its upcoming
   listings, exactly as a failing source does, and drift stays quiet about it
   ([rules S11](rules-and-edge-cases.md#s11-blocked-and-retired-sources-are-recorded-not-deleted)).
   They age out as their dates pass. Check whether an aggregator
   legitimately carries the venue (Harvard Book Store's events continued
   through the Harvard Square aggregator after 2026-09-01).

What not to do: change the user-agent, add stealth flags, slow down and retry
until it works, or switch to a visible browser without the decision below.
Onboarding never chooses visible-browser mode; it returns such a venue as
needing a person's decision.

## Visible-browser mode

Some bot protection refuses plain HTTP and any browser that announces itself as
headless, but serves an ordinary visible browser. A scraper can open a real
window on someone's machine and read such a site the way a person's browser
would. Whether to do that is **an organisational decision, not an engineering
one**: it reads a site that has chosen, by default, to turn away automated
clients. Decide it once, in writing, for your organisation; then per source.

### Cambridge Calendar's history (dated)

| Date | What happened |
|---|---|
| 2026-04-21 | Harvard Book Store began refusing plain requests; the fix of the day was a Safari user-agent. Somerville Theatre got the same in July. Both were spoofs. |
| 2026-09-01 | The Playwright base's spoofed user-agent was removed. **Harvard Book Store retired**: Cloudflare's interstitial everywhere, CI and residential IP alike; "getting past it means defeating protection the venue put up deliberately". **Harvard Memorial Church retired**: 403 on every path to every client. |
| 2026-10-06, morning | **Porter Square Books retired** (Cloudflare challenge to headless and plain HTTP, failing every day since 2026-09-04). **Aeronaut retired**: its challenge had only cleared because the browser claimed to be Windows Chrome 120; with the spoof gone, the way left was a visible browser window, judged at the time to be defeating protection the venue chose. Every remaining user-agent spoof removed. |
| 2026-10-06, later | **The owner decided to revive all three in a visible browser**, on stated terms: the browser's own user-agent; no automation-hiding flags or plugins (three such Chrome flags were removed from Aeronaut's old scraper); nothing interacts with a challenge; a challenge that does not clear on its own within 45 seconds fails the source rather than being parsed as an empty listing; local runs only (`runs_in_ci: false`). |
| 2026-10-06, after a day of testing | Both bookstores began putting follow-up pages behind a check that did not clear on its own. The scraper reads one month per run and clicks nothing. |

The same week produced both judgements, by the same owner, about the same
sites. That is the point of recording it: reasonable people can draw the line
in either place, and the honest position is to draw it deliberately.

### If your organisation says yes

The terms are fixed, whichever sources they apply to:

- `BasePlaywrightScraper(..., headless=False)` with no `user_agent`, so the
  browser sends its own;
- no automation-hiding flags, plugins or patched browsers;
- `wait_past_challenge()` after navigating: it waits for an interstitial to
  clear on its own and raises `ScrapeRefusedError` if it does not; nothing
  clicks or solves;
- `runs_in_ci: false`, run by the weekly local job on a machine someone is
  logged in to;
- the smallest read that works, and a `notes` line naming the decision and its
  date;
- if the venue objects, stop.

### If it says no

Leave such sources `blocked`, use any aggregator that legitimately carries them,
and send the outreach email.
