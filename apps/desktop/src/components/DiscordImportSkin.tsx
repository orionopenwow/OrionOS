import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MessageSquare,
  Database,
  Hash,
  Check,
  Sparkles,
  Bot,
  User,
  CheckSquare,
  Square,
  FileJson,
  Layers,
  X,
} from 'lucide-react';

interface DiscordMessageItem {
  id: string;
  author: string;
  avatarColor: string;
  isBot?: boolean;
  time: string;
  content: string;
  channel: string;
}

interface Props {
  onImportMessages: (messages: Array<{ author: string; content: string; channel?: string }>) => void;
  onAskAboutDiscord: (prompt: string) => void;
  isDiscordSkinActive: boolean;
  onToggleDiscordSkin: () => void;
}

export default function DiscordImportSkin({
  onImportMessages,
  onAskAboutDiscord,
  isDiscordSkinActive,
  onToggleDiscordSkin,
}: Props) {
  const [selectedChannel, setSelectedChannel] = useState('#general');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    new Set(['m1', 'm2', 'm3', 'm4', 'm5'])
  );
  const [importStatus, setImportStatus] = useState<string | null>(null);
  const [rawJsonInput, setRawJsonInput] = useState('');
  const [showJsonModal, setShowJsonModal] = useState(false);

  // Sample Discord channel messages ready for import
  const channelMessages: Record<string, DiscordMessageItem[]> = {
    '#general': [
      {
        id: 'm1',
        author: 'AlexDeveloper',
        avatarColor: '#5865F2',
        time: 'Today at 2:14 PM',
        content: 'Hey everyone, what is the best strategy for local vector embeddings on Windows with LanceDB?',
        channel: '#general',
      },
      {
        id: 'm2',
        author: 'OrionBot',
        avatarColor: '#57F287',
        isBot: true,
        time: 'Today at 2:15 PM',
        content: 'Use FastEmbed with BAAI/bge-small-en-v1.5 (384-dim) or all-MiniLM-L6-v2. It runs sub-10ms purely on CPU!',
        channel: '#general',
      },
      {
        id: 'm3',
        author: 'Sarah_Architect',
        avatarColor: '#FEE75C',
        time: 'Today at 2:22 PM',
        content: 'Make sure to configure nightly consolidation routines so old vector entries get clustered and pruned.',
        channel: '#general',
      },
    ],
    '#agent-commands': [
      {
        id: 'm4',
        author: 'Chief_Engineer',
        avatarColor: '#EB459E',
        time: 'Yesterday at 9:02 PM',
        content: '!deploy agent --profile=screen-ocr --interval=500ms',
        channel: '#agent-commands',
      },
      {
        id: 'm5',
        author: 'OrionBot',
        avatarColor: '#57F287',
        isBot: true,
        time: 'Yesterday at 9:02 PM',
        content: 'Deployed perceptual capture daemon with pHash diff gating. Memory footprint: 140MB RAM.',
        channel: '#agent-commands',
      },
    ],
    '#research': [
      {
        id: 'm6',
        author: 'Quantum_Dev',
        avatarColor: '#5865F2',
        time: 'Oct 11 at 4:30 PM',
        content: 'Here is the benchmark sheet for dual-tier routing between Ollama 3.2 3B and DeepSeek Cloud: 94% local cache hit rate.',
        channel: '#research',
      },
    ],
  };

  const currentList = channelMessages[selectedChannel] || [];

  const handleToggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSelectAll = () => {
    if (selectedIds.size === currentList.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(currentList.map((m) => m.id)));
    }
  };

  const handleExecuteImport = () => {
    const toImport = currentList.filter((m) => selectedIds.has(m.id));
    if (toImport.length === 0) return;

    onImportMessages(
      toImport.map((m) => ({
        author: m.author,
        content: m.content,
        channel: m.channel,
      }))
    );

    setImportStatus(`Successfully ingested ${toImport.length} messages into LanceDB!`);
    setTimeout(() => setImportStatus(null), 3500);
  };

  const handleRawJsonSubmit = () => {
    try {
      const parsed = JSON.parse(rawJsonInput);
      const array = Array.isArray(parsed) ? parsed : [parsed];
      const valid = array.map((item: any) => ({
        author: item.author || item.username || 'DiscordUser',
        content: item.content || item.text || item.message || JSON.stringify(item),
        channel: item.channel || '#imported',
      }));

      onImportMessages(valid);
      setImportStatus(`Imported ${valid.length} items from custom JSON!`);
      setShowJsonModal(false);
      setRawJsonInput('');
      setTimeout(() => setImportStatus(null), 3500);
    } catch (err: any) {
      alert(`Invalid JSON format: ${err.message}`);
    }
  };

  return (
    <div className="w-full rounded-2xl border border-[#5865F2]/40 bg-[#1E1F22]/90 backdrop-blur-2xl shadow-2xl overflow-hidden font-sans">
      {/* Top Header Bar */}
      <div className="flex items-center justify-between px-5 py-3.5 bg-[#2B2D31] border-b border-[#1E1F22]">
        <div className="flex items-center gap-3">
          <div className="p-1.5 rounded-xl bg-[#5865F2] text-white">
            <MessageSquare size={18} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-display text-xs font-bold tracking-wider text-white">
                DISCORD MEMORY IMPORTER & SKIN
              </span>
              <span className="text-[10.5px] font-mono px-2 py-0.5 rounded-full bg-[#5865F2]/20 border border-[#5865F2]/40 text-[#5865F2]">
                GATEWAY SYNC
              </span>
            </div>
            <p className="text-[11px] text-gray-400 font-mono">
              Ingest channel threads, discussions, and task logs directly into Orion's neural memory
            </p>
          </div>
        </div>

        {/* Discord Skin Toggle & JSON Import Button */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowJsonModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 text-xs font-mono text-gray-200 transition-colors"
          >
            <FileJson size={13} className="text-[#5865F2]" />
            <span>Import JSON</span>
          </button>

          <button
            onClick={onToggleDiscordSkin}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono transition-all border ${
              isDiscordSkinActive
                ? 'bg-[#5865F2] text-white border-[#5865F2]'
                : 'bg-white/5 text-gray-300 border-white/10 hover:border-[#5865F2]/50'
            }`}
          >
            <Layers size={13} />
            <span>{isDiscordSkinActive ? 'Discord Skin: ON' : 'Discord Skin: OFF'}</span>
          </button>
        </div>
      </div>

      {/* Main Grid: Channels Sidebar + Messages View */}
      <div className="grid grid-cols-12 min-h-[300px]">
        {/* Left Channels Column */}
        <div className="col-span-3 bg-[#2B2D31]/70 border-r border-[#1E1F22] p-3 space-y-1">
          <span className="text-[11px] font-bold uppercase tracking-wider text-gray-400 px-2 block mb-2 font-mono">
            TEXT CHANNELS
          </span>
          {Object.keys(channelMessages).map((ch) => (
            <button
              key={ch}
              onClick={() => setSelectedChannel(ch)}
              className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
                selectedChannel === ch
                  ? 'bg-[#404249] text-white shadow-sm'
                  : 'text-gray-400 hover:bg-[#35373C] hover:text-gray-200'
              }`}
            >
              <Hash size={14} className="text-gray-400" />
              <span>{ch.slice(1)}</span>
            </button>
          ))}
        </div>

        {/* Right Messages Column */}
        <div className="col-span-9 bg-[#313338] p-4 flex flex-col justify-between">
          <div className="space-y-3.5 max-h-60 overflow-y-auto pr-1">
            {/* Action Bar */}
            <div className="flex items-center justify-between pb-2 border-b border-white/5">
              <button
                onClick={handleSelectAll}
                className="flex items-center gap-1.5 text-[11.5px] font-mono text-gray-300 hover:text-white"
              >
                {selectedIds.size === currentList.length ? (
                  <CheckSquare size={13} className="text-[#5865F2]" />
                ) : (
                  <Square size={13} className="text-gray-400" />
                )}
                <span>Select All ({currentList.length})</span>
              </button>

              <span className="text-[11px] font-mono text-gray-400">
                {selectedIds.size} selected for LanceDB vector ingestion
              </span>
            </div>

            {/* Message Feed */}
            {currentList.map((m) => {
              const isSelected = selectedIds.has(m.id);
              return (
                <div
                  key={m.id}
                  onClick={() => handleToggleSelect(m.id)}
                  className={`flex items-start gap-3 p-2.5 rounded-lg cursor-pointer transition-colors ${
                    isSelected ? 'bg-[#383A40]' : 'hover:bg-[#35373C]/60'
                  }`}
                >
                  <div
                    className="h-8 w-8 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0"
                    style={{ backgroundColor: m.avatarColor }}
                  >
                    {m.isBot ? <Bot size={15} /> : <User size={15} />}
                  </div>

                  <div className="flex-1 overflow-hidden space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-white">{m.author}</span>
                      {m.isBot && (
                        <span className="text-[10.5px] px-1 rounded bg-[#5865F2] text-white font-bold">
                          BOT
                        </span>
                      )}
                      <span className="text-[11px] text-gray-400 font-mono">{m.time}</span>
                    </div>
                    <p className="text-xs text-gray-200 leading-relaxed">{m.content}</p>
                  </div>

                  <div className="pt-1">
                    {isSelected ? (
                      <CheckSquare size={15} className="text-[#5865F2]" />
                    ) : (
                      <Square size={15} className="text-gray-500" />
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Footer Importer Bar */}
          <div className="flex items-center justify-between pt-3 mt-3 border-t border-white/5">
            <button
              onClick={() =>
                onAskAboutDiscord(
                  `Analyze the discussions in ${selectedChannel} and outline any pending action items or technical decisions.`
                )
              }
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#5865F2]/40 bg-[#5865F2]/10 text-xs font-mono text-[#5865F2] hover:bg-[#5865F2]/20 transition-colors"
            >
              <Sparkles size={12} />
              <span>Ask Orion About Thread</span>
            </button>

            <button
              onClick={handleExecuteImport}
              disabled={selectedIds.size === 0}
              className="flex items-center gap-2 px-4 py-1.5 rounded-lg bg-[#5865F2] hover:bg-[#4752C4] text-xs font-semibold text-white transition-all disabled:opacity-40"
            >
              <Database size={13} />
              <span>Import Selected to LanceDB</span>
            </button>
          </div>
        </div>
      </div>

      {/* Import Notification Toast */}
      {importStatus && (
        <div className="px-4 py-2 bg-[#57F287]/20 border-t border-[#57F287]/40 text-[#57F287] text-xs font-mono flex items-center gap-2">
          <Check size={14} />
          <span>{importStatus}</span>
        </div>
      )}

      {/* JSON File Drop / Paste Modal */}
      <AnimatePresence>
        {showJsonModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md p-4">
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="w-full max-w-lg rounded-2xl bg-[#313338] border border-white/10 p-5 shadow-2xl space-y-4"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-white font-bold text-sm">
                  <FileJson size={16} className="text-[#5865F2]" />
                  <span>Import Discord JSON Transcript</span>
                </div>
                <button
                  onClick={() => setShowJsonModal(false)}
                  className="text-gray-400 hover:text-white"
                >
                  <X size={16} />
                </button>
              </div>

              <p className="text-xs text-gray-300">
                Paste any Discord chat export JSON or raw text array. Orion will parse and ingest it into LanceDB vector memory.
              </p>

              <textarea
                value={rawJsonInput}
                onChange={(e) => setRawJsonInput(e.target.value)}
                placeholder='[{"author": "Dev", "content": "Need to fix the cache layer"}]'
                rows={6}
                className="w-full rounded-lg bg-[#1E1F22] border border-white/10 p-3 font-mono text-xs text-white placeholder:text-gray-500 focus:outline-none focus:border-[#5865F2]"
              />

              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setShowJsonModal(false)}
                  className="px-3 py-1.5 rounded-lg text-xs text-gray-300 hover:bg-white/5"
                >
                  Cancel
                </button>
                <button
                  onClick={handleRawJsonSubmit}
                  disabled={!rawJsonInput.trim()}
                  className="px-4 py-1.5 rounded-lg bg-[#5865F2] hover:bg-[#4752C4] text-xs font-semibold text-white transition-colors disabled:opacity-40"
                >
                  Import into Vector Store
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
