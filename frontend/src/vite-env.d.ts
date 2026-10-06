/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Overrides api.base_url from calendar.config.yaml, e.g. a local API. */
  readonly VITE_API_BASE_URL?: string;
  /** Public Mapbox token (pk.…). Without it the map stays hidden. */
  readonly VITE_MAPBOX_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** The theme named by brand.theme, aliased in vite.config.ts. */
declare module '@site-theme' {
  import type { Theme } from '@/themes/types';
  const theme: Theme;
  export default theme;
}
