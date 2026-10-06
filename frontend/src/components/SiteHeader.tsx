import { Link, useLocation } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import theme from '@site-theme';

import { FACETS, SITE, dateline } from '@/lib/site';
import { cn } from '@/lib/utils';

interface SiteHeaderProps {
  /** Landing-page title; the homepage passes none. */
  heading?: string;
  standfirst?: string;
  /** A slim bar for single-event pages: home link only, no title block. */
  compact?: boolean;
}

/** Time and price quick views, then one per configured city. */
const QUICK_VIEW_SLUGS = ['today', 'this-weekend', 'free', 'family'];
const QUICK_VIEWS = FACETS.filter((f) => QUICK_VIEW_SLUGS.includes(f.slug) || f.filter.city);

/**
 * The site name with its last word in the accent color: "Bay Area <Events>".
 * Used when the theme has no logo.
 */
const Wordmark = ({ name }: { name: string }) => {
  const split = name.lastIndexOf(' ');
  if (split < 0) return <>{name}</>;
  return <>{name.slice(0, split)} <span className="text-primary">{name.slice(split + 1)}</span></>;
};

/**
 * What to print beside the logo. A logo that already spells the start of the
 * site name ("KQED" in "KQED Events") is followed by the rest ("Events") only.
 */
const logoLabel = (): string => {
  const name = SITE.site.name;
  const wordmark = theme.logo?.wordmark;
  if (!theme.logo) return name;
  if (wordmark && name.toLowerCase().startsWith(wordmark.toLowerCase())) {
    return name.slice(wordmark.length).trim();
  }
  return name;
};

const QuickViews = ({ className }: { className?: string }) => {
  const { pathname } = useLocation();
  if (QUICK_VIEWS.length === 0) return null;
  return (
    <nav aria-label="Quick views" className={className}>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 font-sans text-sm">
        {QUICK_VIEWS.map((facet) => {
          const href = `/${facet.slug}`;
          const current = pathname === href;
          return (
            <li key={facet.slug}>
              <Link
                to={href}
                aria-current={current ? 'page' : undefined}
                className={cn(
                  'underline-offset-4 hover:text-primary hover:underline',
                  current ? 'font-semibold text-foreground underline decoration-primary decoration-2' : 'text-muted-foreground',
                )}
              >
                {facet.heading}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
};

const BackLink = ({ className }: { className?: string }) => (
  <Link to="/" className={cn('font-sans text-xs font-semibold uppercase tracking-[0.12em] text-primary hover:underline', className)}>
    ← All {SITE.region.name} events
  </Link>
);

/**
 * Centered title over a double rule — the theme's `masthead: 'rule'`.
 */
const RuleHeader = ({ heading, standfirst, compact }: SiteHeaderProps) => {
  if (compact) {
    return (
      <header className="border-b-[3px] border-double border-foreground">
        <div className="max-w-[900px] mx-auto px-4 py-5">
          <Link to="/" className="inline-flex items-center gap-2 font-sans text-sm uppercase tracking-[0.12em] text-muted-foreground hover:text-primary">
            <ArrowLeft className="h-4 w-4" />
            {SITE.site.name}
          </Link>
        </div>
      </header>
    );
  }
  return (
    <header className="text-center py-10 px-4 border-b-[3px] border-double border-foreground">
      <p className="text-xs font-sans uppercase tracking-[0.15em] text-muted-foreground mb-1">
        {dateline()}
      </p>
      {theme.logo && (
        <img src={theme.logo.src} alt={theme.logo.alt} width={theme.logo.width} height={theme.logo.height} className="mx-auto mb-3 h-10 w-auto" />
      )}
      <h1 className="font-display font-black text-5xl md:text-7xl tracking-tight leading-none">
        {heading ? heading : <Wordmark name={SITE.site.name} />}
      </h1>
      <p className="font-serif italic text-base text-muted-foreground mt-1 max-w-3xl mx-auto">
        {standfirst || SITE.site.tagline}
      </p>
      {heading && <p className="mt-3"><BackLink /></p>}
      <QuickViews className="mt-5 flex justify-center" />
    </header>
  );
};

/**
 * A solid brand bar carrying the logo, with the page title set below it on
 * the page background — the theme's `masthead: 'band'`.
 */
const BandHeader = ({ heading, standfirst, compact }: SiteHeaderProps) => {
  const label = logoLabel();
  return (
    <header>
      <div className="bg-masthead text-masthead-foreground">
        <div className={cn('mx-auto px-4 flex items-center justify-between gap-4 h-14 md:h-[72px]', compact ? 'max-w-[900px]' : 'max-w-[1280px]')}>
          <Link
            to="/"
            className="flex items-center gap-3 md:gap-4 min-w-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-masthead-foreground focus-visible:ring-offset-4 focus-visible:ring-offset-masthead"
          >
            {theme.logo && (
              <img
                src={theme.logo.src}
                alt={theme.logo.alt}
                width={theme.logo.width}
                height={theme.logo.height}
                className="h-6 md:h-8 w-auto shrink-0"
              />
            )}
            {label && (
              <span
                className={cn(
                  'font-display font-semibold text-xl md:text-2xl leading-none whitespace-nowrap',
                  theme.logo && 'border-l border-masthead-foreground/40 pl-3 md:pl-4',
                )}
              >
                {label}
              </span>
            )}
          </Link>
          <p className="hidden sm:block font-sans text-xs uppercase tracking-[0.12em] text-masthead-foreground/80 truncate">
            {dateline()}
          </p>
        </div>
      </div>

      {/* No bottom rule here: the page body opens with its own */}
      {!compact && (
        <div>
          <div className="max-w-[1280px] mx-auto px-4 pt-8 md:pt-10">
            {heading && <BackLink className="inline-block mb-3" />}
            <h1 className="font-display font-semibold text-3xl md:text-5xl leading-[1.1] tracking-tight max-w-4xl text-balance">
              {heading || SITE.site.tagline || SITE.site.name}
            </h1>
            {standfirst && (
              <p className="mt-3 font-sans text-base md:text-lg text-muted-foreground max-w-3xl text-pretty">
                {standfirst}
              </p>
            )}
            <QuickViews className="mt-5" />
          </div>
        </div>
      )}
    </header>
  );
};

/** The site header, drawn the way the active theme asks. */
export const SiteHeader = (props: SiteHeaderProps) =>
  theme.masthead === 'band' ? <BandHeader {...props} /> : <RuleHeader {...props} />;

export const SiteFooter = () => (
  <footer className="text-center py-6 border-t border-border mt-4">
    <p className="text-xs text-muted-foreground font-sans">
      &copy; {new Date().getFullYear()} {SITE.site.name}
    </p>
  </footer>
);
