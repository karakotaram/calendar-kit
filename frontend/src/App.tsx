import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Index from "./pages/Index";
import NotFound from "./pages/NotFound";
import EventDetail from "./pages/EventDetail";
import { FacetPage, VenuePage } from "./pages/Facet";
import { FACETS } from "@/lib/site";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Index />} />
          {/* Header- and footer-less, for iframes on other sites */}
          <Route path="/embed" element={<Index embed />} />

          {/* Indexable landing pages. Each is also prerendered at build time by
              scripts/generate-static.mjs; these routes make them work for
              client-side navigation and as a fallback. The slug list comes from
              src/lib/facets.mjs (via src/lib/site.ts) so the two cannot drift. */}
          <Route path="/event/:id" element={<EventDetail />} />
          <Route path="/venue/:slug" element={<VenuePage />} />
          {FACETS.map((facet) => (
            <Route key={facet.slug} path={`/${facet.slug}`} element={<FacetPage facet={facet} />} />
          ))}
          {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
