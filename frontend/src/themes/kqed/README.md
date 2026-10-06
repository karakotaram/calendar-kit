# KQED theme — provisional

> **Status: provisional.** Every asset and value here was read from KQED's
> public website, not supplied by KQED. **Confirm with KQED's brand team and
> replace with official files before launch.** Until then, treat this theme as
> a close draft, not an approved brand implementation.

Selected by `brand.theme: kqed` in `calendar.config.yaml`.

## Files

| File | What it is | Source |
|---|---|---|
| `logo.svg` | The "KQED" wordmark, white, `viewBox="0 0 99 31"`. Drawn for a dark background. | Copied verbatim from the inline `<svg class="logo logo-kqed">` in the masthead of https://www.kqed.org, fetched 2026-10-06. Not modified. |
| `favicon.ico` | White "Q" on KQED red, 16, 32 and 48 px. | https://www.kqed.org/favicon.ico, fetched 2026-10-06. Not modified. |
| `theme.css` | Colors, radius and font stacks as CSS variables. | Values below. |
| `theme.json` | Font stylesheet, favicon and browser theme color for `index.html`. | — |
| `index.ts` | Logo metadata and the `band` masthead treatment. | — |

Both files are stored here and bundled with the site. Nothing is hotlinked
from kqed.org.

## Colors

Read from kqed.org's compiled stylesheet (`/dist/index.542b52e94d0a40cedb7a.css`
on 2026-10-06). Contrast is against white unless noted. WCAG AA for body
text is 4.5:1.

| Token | Value | Where kqed.org uses it | Contrast |
|---|---|---|---|
| `--masthead` | `#012842` navy | Site header background | white text on it: 15.2:1 |
| `--primary` | `#d80040` red | Text links | 5.3:1; white on it: 5.3:1 |
| `--foreground` | `#04151a` | Body text | 18.6:1 |
| `--muted-foreground` | `#636363` | Dates, bylines, labels | 5.9:1; on `--muted` 5.3:1 |
| `--border` | `#d5d5d5` | Rules and dividers | (non-text) |
| `--ring` | `#0078c9` blue | Keyboard focus outlines | (non-text) |
| `--success` | `#1a7a31` green | **Ours**, see below | 5.4:1 |

Seen but deliberately not used:

- **`#ec0046`**, the brighter red on kqed.org's Donate button. It measures
  4.51:1 on white, too close to the 4.5:1 floor for small labels, so the
  theme uses KQED's own darker link red `#d80040` for everything textual.
- **`#219135`**, kqed.org's green. It measures 4.1:1 on white, which fails AA
  for the small "Family Friendly" label, so the theme uses a darker green.
- **`#a50031`**, the hover red for links. The kit's hover states use the
  foreground color instead.

The Editor's Picks cards put white type on photographs under a dark gradient,
with the category on a solid red chip (white on `#d80040`, 5.3:1). Red text
straight on a dark photo would fail contrast.

## Typefaces and substitutions

| Role | kqed.org | This theme | Why |
|---|---|---|---|
| Headlines, card titles, the "Events" label | **Tiempos Headline** Semibold (Klim Type Foundry) | **Source Serif 4**, weights 400–700 with optical sizing, from Google Fonts | Tiempos is a commercial typeface; this kit has no license to serve it. Source Serif 4 (Adobe, SIL Open Font License) is the closest open match: a sharp, transitional newspaper serif whose display optical size approximates Tiempos Headline at large sizes. |
| Body and interface | System stack: `-apple-system, BlinkMacSystemFont, Roboto, Arial, "Helvetica Neue", sans-serif` | The same stack | System fonts need no license, so there is nothing to substitute. |
| Campaign and show display faces | GT America, Druk, Obviously, Söhne Schmal, Sharp Grotesk | Not used | These belong to particular KQED programs and campaigns, not the core identity. |

If KQED's Tiempos license covers this site's domain, self-host the font files
in this folder, add `@font-face` rules to `theme.css`, point `--font-serif` and
`--font-display` at Tiempos, and remove the `fonts` URL from `theme.json`.

## Masthead treatment

`masthead: 'band'`: a full-width navy bar carrying the white wordmark, a thin
divider and the word "Events" in the headline serif. The page title sits
below it on white. The header shows "Events" rather than "KQED Events"
because the logo already spells "KQED" (`wordmark: 'KQED'` in `index.ts`); the
link's accessible name is still "KQED Events".

The theme borrows KQED's colors, type and wordmark, not its site design. It
has no navigation, Donate button, NPR or PBS marks, or audio player. This is
an events calendar in KQED's colors, not a copy of kqed.org.

## To confirm with KQED's brand team

1. **Logo**: is this wordmark the right mark for an events product? Is there
   an approved "KQED Events" lockup? What clear space, minimum size and
   on-navy usage rules apply? Supply official SVGs (light and dark).
2. **Colors**: confirm navy `#012842` and red `#d80040`, and whether the
   brighter `#ec0046` is the primary brand red for large elements.
3. **Typefaces**: is a Tiempos web license available for this domain, or is
   Source Serif 4 acceptable?
4. **Favicon and app icons**: supply official files, including a 180 px
   Apple touch icon if wanted.
5. **Naming and footer**: is "KQED Events" the product name, and what
   copyright or legal line should the footer carry? It currently reads
   "© {year} KQED Events".

## Replacing with official files

Drop official files in with the same names (`logo.svg`, `favicon.ico`), or
change the names in `index.ts` and `theme.json`. If the new logo is a
different shape, update `width`/`height` in `index.ts` to its viewBox. If it
is dark-on-light, the `band` masthead needs a light `--masthead` or a
light-on-dark version of the logo. Re-check contrast after any color change.
