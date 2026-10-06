/**
 * Wall-clock time in the calendar's own zone.
 *
 * The API sends every time as naive wall clock in the zone named by
 * `region.timezone`: "2026-10-06T19:00:00" means 7 pm where the event is, with
 * no offset attached. Read with `new Date(value)`, that string becomes 7 pm in
 * whatever zone the reader's browser (or the build machine) is in, which is
 * fine for display — the digits come back out unchanged — but wrong in three
 * places this module covers:
 *
 *   "now"        what counts as today, upcoming or past is decided where the
 *                events are, not where the reader or the Vercel builder is
 *   offsets      schema.org and calendar links want an explicit UTC offset,
 *                and that offset changes with daylight saving time
 *   instants     a calendar link must name the real moment, not 7 pm in the
 *                reader's own zone
 *
 * Everything goes through Intl, so any IANA zone works and DST rules come from
 * the runtime's tz database rather than hand-coded transition dates.
 *
 * Shared by the app (src/lib/site.ts) and scripts/generate-static.mjs.
 */

const formatters = new Map();

function formatterFor(timeZone) {
  if (!formatters.has(timeZone)) {
    formatters.set(timeZone, new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    }));
  }
  return formatters.get(timeZone);
}

/** The wall-clock fields an instant shows in `timeZone`. */
function wallFields(timeZone, instant) {
  const parts = {};
  for (const { type, value } of formatterFor(timeZone).formatToParts(instant)) parts[type] = value;
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour) % 24,
    minute: Number(parts.minute),
    second: Number(parts.second),
  };
}

/** Minutes east of UTC in effect in `timeZone` at `instant` (-420 for PDT). */
export function offsetAt(timeZone, instant) {
  const f = wallFields(timeZone, instant);
  const asUtc = Date.UTC(f.year, f.month - 1, f.day, f.hour, f.minute, f.second);
  const wholeSeconds = Math.floor(instant.getTime() / 1000) * 1000;
  return Math.round((asUtc - wholeSeconds) / 60000);
}

/**
 * Offset in effect for a wall-clock reading in `timeZone`. Guess the instant as
 * if the zone were UTC, correct by the offset there, then check once more — the
 * second pass settles readings that sit next to a DST change.
 */
export function offsetForWallClock(timeZone, year, month, day, hour = 0, minute = 0) {
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  const first = offsetAt(timeZone, new Date(guess));
  return offsetAt(timeZone, new Date(guess - first * 60000));
}

/** -420 → "-07:00" */
export function formatOffset(minutes) {
  const sign = minutes < 0 ? '-' : '+';
  const abs = Math.abs(minutes);
  const pad = (n) => String(n).padStart(2, '0');
  return `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

const WALL_CLOCK = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;

/**
 * "2026-10-06T19:00:00" → "2026-10-06T19:00:00-07:00", or null when the value
 * is not a date. Reads the digits directly, so the result never depends on the
 * zone of the machine running it.
 */
export function wallClockToIso(value, timeZone) {
  const match = WALL_CLOCK.exec(String(value ?? ''));
  if (!match) return null;
  const [, y, mo, d, h = '00', mi = '00'] = match;
  const offset = offsetForWallClock(timeZone, Number(y), Number(mo), Number(d), Number(h), Number(mi));
  return `${y}-${mo}-${d}T${h}:${mi}:00${formatOffset(offset)}`;
}

/**
 * The current wall clock in `timeZone`, as a Date whose *local* fields read it.
 *
 * That is the same representation `new Date("2026-10-06T19:00:00")` produces
 * for an event, so the two compare correctly and `windowRange()` can use its
 * local-time arithmetic unchanged.
 */
export function wallClockNow(timeZone, now = new Date()) {
  const f = wallFields(timeZone, now);
  return new Date(f.year, f.month - 1, f.day, f.hour, f.minute, f.second);
}

/** Is this a time zone the runtime knows? */
export function isValidTimeZone(timeZone) {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone });
    return true;
  } catch {
    return false;
  }
}
