import { ExternalLink, Plus } from 'lucide-react';
import { SUBMIT_URL } from '@/lib/site';

/**
 * "Submit an event" — a link to the station's own form (submissions.form_url
 * in calendar.config.yaml). Renders nothing when submissions are switched off
 * or no form is configured.
 *
 * Cambridge Calendar posted a form to a Supabase function instead; the kit
 * collects submissions in a Google Form, which sync_user_events.py reads.
 */
export const SubmitEventLink = () => {
  if (!SUBMIT_URL) return null;
  return (
    <a
      href={SUBMIT_URL}
      target="_blank"
      rel="noopener noreferrer"
      className="text-sm text-primary hover:underline font-sans flex items-center gap-1"
    >
      <Plus className="w-4 h-4" aria-hidden="true" />
      Submit an event
      <ExternalLink className="w-3 h-3" aria-hidden="true" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
};
