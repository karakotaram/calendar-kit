import { Link, useLocation } from "react-router-dom";
import { useEffect } from "react";

import { SiteHeader } from "@/components/SiteHeader";

const NotFound = () => {
  const location = useLocation();

  useEffect(() => {
    console.error("404 Error: User attempted to access non-existent route:", location.pathname);
  }, [location.pathname]);

  return (
    <div className="min-h-screen bg-background">
      <SiteHeader compact />
      <main className="max-w-[900px] mx-auto px-4 py-16">
        <p className="font-sans text-xs uppercase tracking-[0.15em] text-primary mb-2">404</p>
        <h1 className="font-display font-black text-4xl mb-3">Page not found</h1>
        <p className="font-serif text-muted-foreground mb-6">
          There is nothing at this address. The calendar itself is one click away.
        </p>
        <Link to="/" className="font-sans text-sm font-semibold uppercase tracking-[0.12em] text-primary hover:underline">
          Browse the calendar
        </Link>
      </main>
    </div>
  );
};

export default NotFound;
