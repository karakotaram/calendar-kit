# Venue outreach email

For when a venue blocks automated reading, has no machine-readable calendar, or
you would simply rather ask than scrape. See
[access-policy.md](access-policy.md#when-a-site-blocks-you) for when to send it.

Replace everything in `{braces}`. Send it to the venue's events, marketing or
web contact rather than a general inbox if you can find one. Keep the venue's
reply, and record the outcome in the source's registry `notes`.

---

**Subject:** Listing {Venue name}'s events on {Calendar name}

Hi {first name, or "there"},

I'm {your name} at {Calendar name} ({calendar URL}), a free public calendar of
events across {region}. We'd love to include {Venue name}'s events, with every
listing linking straight back to your own page for details and tickets.

Our calendar reads venues' public event listings automatically once a day, and
your site's security settings (very reasonably) turn our reader away. Rather
than work around that, we'd like to ask how you'd prefer to share your events.
Any one of these would work:

1. **A calendar feed.** If your calendar can export a subscription link (an
   ".ics", "iCal" or "Add to calendar / Subscribe" link), that address is all
   we need. Most event plugins and booking systems have one, sometimes switched
   off by default.
2. **A data feed.** If your site or ticketing system publishes your events as
   JSON (some call it an "API" or "events feed"), the address of that works too.
3. **Letting our reader through.** If your web host or security service (for
   example Cloudflare) allows it, you could permit requests whose user-agent is
   `{user-agent, e.g. KQEDEvents/1.0 (+https://events.example.org)}`. We read
   once a day, a few pages at most.
4. **Sending them to us.** If none of that is easy, you're welcome to submit
   events through our form ({submission form URL}), or email them to
   {contact email} and we'll add them.

Whatever we use, we only publish what you already publish: each event's title,
date, time, place, a short description and image, and a link to your page. If
you'd ever like something changed or removed, just reply to this address.

Thanks for considering it, and for everything you put on.

{your name}
{title}, {organisation}
{contact email} · {phone, optional}

---

## What to ask for, in plain words

If the venue forwards you to their web person, these are the specifics:

| Ask for | It looks like | Notes |
|---|---|---|
| An iCal feed of upcoming public events | `https://venue.org/events.ics`, `webcal://...`, `?ical=1`, or a Google Calendar's "public address in iCal format" | best option: the kit's `ical` adapter reads it as is. Ask that it include future events (not just this month) and each event's own URL |
| A JSON events endpoint | `https://venue.org/wp-json/tribe/events/v1/events`, `.../events?format=json` | often already public; ask whether it is fine for you to read it daily |
| An allowlist entry | user-agent `{your user-agent}` | for Cloudflare and similar: a WAF custom rule or "skip" rule matching that user-agent. Some hosts prefer to allow an IP; GitHub Actions has no fixed IP, so ask for the user-agent rule |
| A submission arrangement | the form, or an email address | slower and manual; fine for venues with a few events a month |

Things to confirm whichever route they choose: that the feed lists only public
events (not private bookings or staff events), and who to tell if it changes.
