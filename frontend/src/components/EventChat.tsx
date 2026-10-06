import { useState, useRef, useEffect } from 'react';
import { Send } from 'lucide-react';
import { cn } from '@/lib/utils';
import { API_BASE_URL } from '@/lib/config';
import { SITE } from '@/lib/site';

const API_URL = API_BASE_URL;

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export function EventChat() {
  const [messages, setMessages] = useState<Message[]>([{
    role: 'assistant',
    content: `Hello! I can help you find ${SITE.region.name} events. You might ask:\n\n\u2022 "What's happening this weekend?"\n\u2022 "Find live music next Saturday"\n\u2022 "Something fun for kids this Sunday"\n\nWhat are you in the mood for?`
  }]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  // Scroll the chat log itself, never the page. scrollIntoView() also scrolls
  // every scrollable ancestor, and this effect runs on mount, so it dragged the
  // whole page down to the chat (at the bottom) on every first load.
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTo({ top: log.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const sendMessage = async () => {
    if (!input.trim() || loading) return;
    const userMessage = input.trim();
    setInput('');
    const newMessages: Message[] = [...messages, { role: 'user', content: userMessage }];
    setMessages(newMessages);
    setLoading(true);

    try {
      const conversationHistory = newMessages.slice(1).slice(-10).map(m => ({
        role: m.role,
        content: m.content
      }));
      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMessage,
          conversation_history: conversationHistory.slice(0, -1)
        })
      });
      // 404: the API has chat switched off (features.chat disagrees with the
      // API's config). 503: the assistant failed; the body carries {detail}.
      if (response.status === 404) {
        setMessages([...newMessages, { role: 'assistant', content: 'The events assistant is not available on this calendar.' }]);
        return;
      }
      if (!response.ok) throw new Error(`Chat returned ${response.status}`);
      const data = await response.json();
      setMessages([...newMessages, { role: 'assistant', content: data.response }]);
    } catch (error) {
      console.error('Chat error:', error);
      setMessages([...newMessages, {
        role: 'assistant',
        content: "Sorry \u2014 I couldn't reach the events assistant just now. Please try again shortly."
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const renderWithLinks = (text: string) => {
    const linkRegex = /\[([^\]]+)\]\(([^)]+)\)/g;
    const parts: (string | JSX.Element)[] = [];
    let lastIndex = 0;
    let match;
    while ((match = linkRegex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(text.slice(lastIndex, match.index));
      }
      parts.push(
        <a key={match.index} href={match[2]} target="_blank" rel="noopener noreferrer"
           className="text-primary underline hover:text-foreground transition-colors">
          {match[1]}
        </a>
      );
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < text.length) {
      parts.push(text.slice(lastIndex));
    }
    return parts.length > 0 ? parts : text;
  };

  return (
    <section className="border-t border-border mt-10 pt-8 pb-12" aria-labelledby="event-chat-heading">
      <h2 id="event-chat-heading" className="font-serif font-bold text-2xl border-b-2 border-foreground pb-1 mb-2">
        Ask About Events
      </h2>
      <p className="font-serif italic text-muted-foreground text-sm mb-5">
        Ask for recommendations from everything on the calendar.
      </p>

      {/* Chat log */}
      <div ref={logRef} className="max-h-[360px] overflow-y-auto mb-4 space-y-4" aria-live="polite">
        {messages.map((msg, idx) => (
          <div key={idx} className={cn("max-w-[85%]", msg.role === 'user' ? "ml-auto text-right" : "")}>
            <div className="text-[0.7rem] font-bold uppercase tracking-wider text-muted-foreground mb-0.5">
              {msg.role === 'user' ? (
                <span className="text-primary">You</span>
              ) : (
                'Assistant'
              )}
            </div>
            <div className={cn(
              "inline-block text-left px-4 py-3 text-sm leading-relaxed font-sans",
              msg.role === 'user'
                ? "bg-foreground text-background"
                : "bg-card border border-border"
            )}>
              {msg.content.split('\n').map((line, i) => (
                <p key={i} className={line === '' ? 'h-2' : ''}>
                  {renderWithLinks(line)}
                </p>
              ))}
            </div>
          </div>
        ))}
        {loading && (
          <div className="max-w-[85%]">
            <div className="text-[0.7rem] font-bold uppercase tracking-wider text-muted-foreground mb-0.5">
              Assistant
            </div>
            <div className="inline-block bg-card border border-border px-4 py-3">
              <span className="flex gap-1 text-muted-foreground">
                <span className="animate-bounce">&bull;</span>
                <span className="animate-bounce" style={{ animationDelay: '0.1s' }}>&bull;</span>
                <span className="animate-bounce" style={{ animationDelay: '0.2s' }}>&bull;</span>
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyPress}
          placeholder="What's happening this weekend?"
          aria-label="Ask about events"
          disabled={loading}
          className="flex-1 px-3 py-2.5 border border-border bg-card font-sans text-sm outline-none focus:border-foreground transition-colors"
        />
        <button
          onClick={sendMessage}
          disabled={loading || !input.trim()}
          className="px-5 py-2.5 bg-foreground text-white font-sans font-semibold text-xs uppercase tracking-wider hover:bg-primary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Send
        </button>
      </div>
    </section>
  );
}
