/**
 * A theme is a folder under src/themes/, chosen by `brand.theme` in
 * calendar.config.yaml. Only the chosen theme is bundled: vite.config.ts points
 * the `@site-theme` import at its index.ts, so another station's logo never
 * ships in this site's JavaScript.
 *
 * Each folder holds:
 *
 *   index.ts     this `Theme` object, and `import './theme.css'`
 *   theme.css    the CSS variables Tailwind reads: colors, radius, fonts
 *   theme.json   what index.html needs before any script runs — the font
 *                stylesheet, favicon and browser theme color (ThemeHead)
 *   logo, favicon and any other assets, saved locally — never hotlinked
 */

/**
 * How the site header is drawn.
 *
 *   rule  centered title and tagline over a double rule, newspaper style
 *   band  a solid brand-colored bar carrying the logo, with the page title
 *         set below it on the page background
 */
export type MastheadTreatment = 'rule' | 'band';

export interface ThemeLogo {
  src: string;
  /** Text equivalent of the logo, e.g. "KQED". */
  alt: string;
  /**
   * The words the logo already spells. When the site name starts with them
   * ("KQED Events"), the header shows the logo followed by only the rest
   * ("Events") rather than repeating the name.
   */
  wordmark?: string;
  width: number;
  height: number;
}

export interface Theme {
  id: string;
  masthead: MastheadTreatment;
  logo?: ThemeLogo;
}

/** Shape of theme.json, read by vite.config.ts at build time. */
export interface ThemeHead {
  /** Human name, for the build log. */
  label: string;
  /** A Google Fonts (or other) stylesheet URL; omit when the theme uses system fonts only. */
  fonts?: string;
  /** File name in the theme folder, e.g. "favicon.svg". */
  favicon: string;
  /** <meta name="theme-color">, usually the masthead color. */
  themeColor: string;
}
