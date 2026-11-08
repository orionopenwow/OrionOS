import { useState } from 'react';
import {
  Chrome,
  Globe,
  ExternalLink,
  Copy,
  Check,
  Sparkles,
  Code2,
  FileText,
  RefreshCw,
  Search,
} from 'lucide-react';
import type { ClientInfo } from '@orion/shared-types';

interface ChromeTabContext {
  url: string;
  title: string;
  text_sample: string;
  timestamp?: number;
  browser?: string;
  tab_id?: number;
}

interface Props {
  tabContext?: ChromeTabContext | null;
  clients: ClientInfo[];
  onTriggerAction: (prompt: string) => void;
  onDeepScrape?: (url: string) => void;
}

export default function ChromeContextWindow({
  tabContext,
  clients,
  onTriggerAction,
  onDeepScrape,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [filterQuery, setFilterQuery] = useState('');

  // Fallback sample data if extension hasn't sent an update yet
  const activeTab: ChromeTabContext = tabContext || {
    url: 'https://github.com/orionopenwow/OrionOS',
    title: 'Orion Desktop Agent · Autonomous AI Intelligence Hub',
    text_sample: `Orion is a local-first autonomous agent running on Windows with perceptual screen capture (pHash + OCR), LanceDB vector memory, and dual-tier SLM orchestration.
Features:
- Sub-50ms pHash screen capture with BitBlt gating
- Headless Chrome DevTools Protocol (CDP) bridge
- Web Speech API live voice streaming into side chat drawer
- Native local TTS using pyttsx3
- LanceDB persistent embeddings at ~/.orion/memory_db`,
    browser: 'Chrome 128 (MV3 Active)',
    timestamp: Date.now() / 1000,
  };

  const extensionClients = clients.filter((c) => c.kind === 'extension');
  const isExtensionConnected = extensionClients.length > 0;

  const handleCopyUrl = () => {
    navigator.clipboard.writeText(activeTab.url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const filteredText = filterQuery
    ? activeTab.text_sample
        .split('\n')
        .filter((l) => l.toLowerCase().includes(filterQuery.toLowerCase()))
        .join('\n')
    : activeTab.text_sample;

  return (
    <div className="w-full rounded-2xl border border-white/10 bg-[#151A21]/85 p-5 backdrop-blur-2xl shadow-2xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 border-b border-white/10 pb-3">
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-[#00E5FF]/10 border border-[#00E5FF]/30 text-[#00E5FF]">
            <Chrome size={18} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-display text-xs font-bold tracking-[0.2em] text-[#00E5FF]">
                CHROME CONTEXT WINDOW
              </span>
              <span
                className={`text-[10.5px] font-mono px-2 py-0.5 rounded-full border ${
                  isExtensionConnected
                    ? 'border-[#3DFFA8]/40 bg-[#3DFFA8]/10 text-[#3DFFA8]'
                    : 'border-yellow-500/40 bg-yellow-500/10 text-yellow-400'
                }`}
              >
                {isExtensionConnected ? 'MV3 BRIDGE CONNECTED' : 'STANDALONE CDP READY'}
              </span>
            </div>
            <span className="text-[11px] text-dust font-mono">
              Live DOM extraction & perceptual page comprehension
            </span>
          </div>
        </div>

        {/* Quick External Link */}
        <div className="flex items-center gap-2">
          <button
            onClick={handleCopyUrl}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-white/10 bg-white/5 text-[11.5px] font-mono text-dust hover:text-starlight hover:border-white/20 transition-colors"
            title="Copy URL"
          >
            {copied ? <Check size={12} className="text-[#3DFFA8]" /> : <Copy size={12} />}
            <span>{copied ? 'Copied' : 'Copy URL'}</span>
          </button>
        </div>
      </div>

      {/* Active Tab Card */}
      <div className="rounded-xl border border-white/10 bg-black/40 p-3.5 mb-4 space-y-2">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1 overflow-hidden">
            <div className="flex items-center gap-2">
              <Globe size={13} className="text-[#00E5FF] shrink-0" />
              <h3 className="text-xs font-semibold text-starlight truncate" title={activeTab.title}>
                {activeTab.title}
              </h3>
            </div>
            <a
              href={activeTab.url}
              target="_blank"
              rel="noreferrer"
              className="text-[11.5px] font-mono text-dust/80 hover:text-[#00E5FF] truncate block flex items-center gap-1"
            >
              <span className="truncate">{activeTab.url}</span>
              <ExternalLink size={10} className="shrink-0" />
            </a>
          </div>
          <span className="text-[11px] font-mono text-dust shrink-0">
            {activeTab.browser || 'Chrome'}
          </span>
        </div>

        {/* Quick Action Pills */}
        <div className="flex flex-wrap gap-2 pt-2 border-t border-white/5">
          <button
            onClick={() =>
              onTriggerAction(
                `Summarize this Chrome tab in 3 bullet points: "${activeTab.title}" (${activeTab.url})\n\nContent:\n${activeTab.text_sample.slice(0, 1500)}`
              )
            }
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-[#00E5FF]/30 bg-[#00E5FF]/10 text-[11px] font-mono text-[#00E5FF] hover:bg-[#00E5FF]/20 transition-all"
          >
            <Sparkles size={11} />
            <span>Summarize Page</span>
          </button>

          <button
            onClick={() =>
              onTriggerAction(
                `Extract all code snippets, technical commands, and configs from this page: "${activeTab.title}" (${activeTab.url})\n\nContent:\n${activeTab.text_sample.slice(0, 2000)}`
              )
            }
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-[#7FA6FF]/30 bg-[#7FA6FF]/10 text-[11px] font-mono text-[#7FA6FF] hover:bg-[#7FA6FF]/20 transition-all"
          >
            <Code2 size={11} />
            <span>Extract Code Blocks</span>
          </button>

          <button
            onClick={() => onDeepScrape && onDeepScrape(activeTab.url)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-[#3DFFA8]/30 bg-[#3DFFA8]/10 text-[11px] font-mono text-[#3DFFA8] hover:bg-[#3DFFA8]/20 transition-all"
          >
            <RefreshCw size={11} />
            <span>Deep CDP Scrape</span>
          </button>
        </div>
      </div>

      {/* DOM Sample Search & Code Area */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] font-mono uppercase tracking-wider text-dust flex items-center gap-1.5">
            <FileText size={12} className="text-[#00E5FF]" />
            Extracted DOM Text Sample
          </span>
          {/* Quick Search */}
          <div className="relative flex items-center">
            <Search size={11} className="absolute left-2 text-dust" />
            <input
              type="text"
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              placeholder="Filter page text..."
              className="rounded-md border border-white/10 bg-black/50 pl-6 pr-2 py-0.5 text-[11px] font-mono text-starlight placeholder:text-dust/55 focus:outline-none focus:border-[#00E5FF]/50"
            />
          </div>
        </div>

        {/* Text Container with Monospace Terminal Style */}
        <div className="rounded-xl border border-white/5 bg-black/60 p-3 max-h-48 overflow-y-auto font-mono text-[11.5px] text-dust leading-relaxed selectable">
          <pre className="whitespace-pre-wrap">{filteredText || 'No text found matching query.'}</pre>
        </div>
      </div>
    </div>
  );
}
