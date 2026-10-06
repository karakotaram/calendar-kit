import { defineConfig, type HtmlTagDescriptor, type Plugin } from "vite";
import react from "@vitejs/plugin-react-swc";
import { existsSync, readFileSync } from "fs";
import path from "path";

/**
 * src/site.config.json is written by scripts/sync-config.mjs from the kit's
 * calendar.config.yaml; `npm run dev` and `npm run build` run that first.
 */
function readJson<T>(file: string, hint: string): T {
  if (!existsSync(file)) throw new Error(`${path.relative(__dirname, file)} is missing. ${hint}`);
  return JSON.parse(readFileSync(file, "utf8")) as T;
}

interface SiteJson {
  site: { name: string; url: string };
  brand: { theme: string };
  home: { title: string; description: string };
}

interface ThemeJson {
  label: string;
  fonts?: string;
  favicon: string;
  themeColor: string;
}

const site = readJson<SiteJson>(
  path.resolve(__dirname, "src/site.config.json"),
  "Run `npm run sync-config`.",
);
const themeDir = path.resolve(__dirname, "src/themes", site.brand.theme);
const theme = readJson<ThemeJson>(
  path.join(themeDir, "theme.json"),
  `brand.theme is "${site.brand.theme}" but that theme has no theme.json.`,
);

/**
 * Fill index.html's <head> from the config and the active theme, so a page
 * read without JavaScript — a crawler, a link preview — already carries the
 * right title, description, fonts and icon. scripts/generate-static.mjs later
 * replaces the title, description, canonical and social tags per page.
 */
function siteHead(): Plugin {
  return {
    name: "site-head",
    transformIndexHtml: {
      // "pre" so Vite then fingerprints the favicon like any other asset
      order: "pre",
      handler() {
        const { title, description } = site.home;
        const meta = (attrs: Record<string, string>): HtmlTagDescriptor => ({ tag: "meta", attrs, injectTo: "head" });
        const link = (attrs: Record<string, string | boolean>): HtmlTagDescriptor => ({ tag: "link", attrs, injectTo: "head" });
        const tags: HtmlTagDescriptor[] = [
          { tag: "title", children: title, injectTo: "head" },
          meta({ name: "description", content: description }),
          meta({ name: "author", content: site.site.name }),
          meta({ name: "theme-color", content: theme.themeColor }),
          meta({ property: "og:title", content: title }),
          meta({ property: "og:description", content: description }),
          meta({ property: "og:type", content: "website" }),
          meta({ property: "og:site_name", content: site.site.name }),
          meta({ name: "twitter:card", content: "summary" }),
          meta({ name: "twitter:title", content: title }),
          meta({ name: "twitter:description", content: description }),
          link({ rel: "icon", href: `/src/themes/${site.brand.theme}/${theme.favicon}` }),
        ];
        if (site.site.url) {
          tags.push(
            link({ rel: "canonical", href: `${site.site.url}/` }),
            meta({ property: "og:url", content: `${site.site.url}/` }),
          );
        }
        if (theme.fonts) {
          tags.push(
            link({ rel: "preconnect", href: "https://fonts.googleapis.com" }),
            link({ rel: "preconnect", href: "https://fonts.gstatic.com", crossorigin: true }),
            link({ rel: "stylesheet", href: theme.fonts }),
          );
        }
        return tags;
      },
    },
  };
}

// https://vitejs.dev/config/
export default defineConfig(() => ({
  server: {
    host: "::",
    port: 8080,
  },
  plugins: [react(), siteHead()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Only the configured theme is bundled; see src/themes/types.ts
      "@site-theme": path.join(themeDir, "index.ts"),
    },
  },
}));
