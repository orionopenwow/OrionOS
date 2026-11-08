import { useEffect, useMemo, useRef, useState } from 'react';
import { Send, Sparkles, Loader2, Inbox, AlertTriangle, ExternalLink, Bell, BellOff, Star, ScrollText, ChevronLeft } from 'lucide-react';
import { useIsMobile } from '../lib/device';
import type {
  CommsDraftEvent,
  CommsPlatform,
  CommsReplyResultEvent,
  CommsThread,
  TelemetrySnapshot,
} from '@orion/shared-types';

interface Props {
  threads: CommsThread[];
  drafts: Record<string, CommsDraftEvent>;
  replyResult: CommsReplyResultEvent | null;
  telemetry: TelemetrySnapshot | null;
  onReply: (threadKey: string, text: string, draftId?: string | null) => void;
  onRequestDraft: (threadKey: string) => void;
  onMarkRead: (threadKey: string) => void;
  connected?: boolean;
  /** Feature Bible: mute a group into the twice-daily digest. */
  onMute?: (threadKey: string, muted: boolean) => void;
  onDigest?: () => void;
  focusThreadKey?: string | null;
  /** Prefill the composer of focusThreadKey (e.g. a suggestion picked in the Messages widget). Never sends. */
  focusDraft?: string | null;
  /** Quick replies in the user's own style, keyed by thread. Clicking one fills the box — it never sends. */
  suggestions?: Record<string, { for_id: string; replies: string[] }>;
  onRequestSuggestions?: (threadKey: string) => void;
}

const PLATFORM: Record<CommsPlatform, { label: string; color: string; short: string }> = {
  telegram: { label: 'Telegram', color: '#26A5E4', short: 'TG' },
  discord: { label: 'Discord', color: '#5865F2', short: 'DC' },
  messenger: { label: 'Messenger', color: '#A855F7', short: 'FB' },
};

const ROUTE_LABEL: Record<string, string> = {
  telegram_user: 'Telegram account',
  telegram_api: 'Telegram bot',
  discord_bot: 'Discord bot',
  extension_cdp_inject: 'Chrome extension',
};

function hhmm(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

interface Composer {
  text: string;
  draftId: string | null;
}

const EMPTY: Composer = { text: '', draftId: null };
const REQUEST_TIMEOUT_MS = 25_000;

export default function CommsPanel({
  threads,
  drafts,
  replyResult,
  telemetry,
  connected = true,
  onReply,
  onRequestDraft,
  onMarkRead,
  onMute,
  onDigest,
  focusThreadKey,
  focusDraft,
  suggestions = {},
  onRequestSuggestions,
}: Props) {
  const [filter, setFilter] = useState<CommsPlatform | 'all'>('all');
  const [selected, setSelected] = useState<string | null>(null);
  // phones: one column — the thread list, then a conversation with a back button
  const mobile = useIsMobile();
  const [mobileThread, setMobileThread] = useState(false);
  // Everything typed is kept per conversation, so a thread switch can never carry text to someone else.
  const [composers, setComposers] = useState<Record<string, Composer>>({});
  const [drafting, setDraftingMap] = useState<Record<string, boolean>>({});
  const [sendingMap, setSendingMap] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const seenDrafts = useRef<Record<string, CommsDraftEvent>>({});
  const timers = useRef<Record<string, number>>({});
  const bottomRef = useRef<HTMLDivElement>(null);

  const visible = useMemo(
    () => threads.filter((t) => filter === 'all' || t.platform === filter),
    [threads, filter],
  );

  // Pin the open conversation: only auto-pick one when nothing valid is selected. New messages
  // in other threads never switch the view.
  useEffect(() => {
    if (!selected || !visible.some((t) => t.key === selected)) {
      setSelected(visible[0]?.key ?? null);
    }
  }, [visible, selected]);

  useEffect(() => {
    if (focusThreadKey) {
      setFilter('all');
      setSelected(focusThreadKey);
      setMobileThread(true);
      if (focusDraft) setComposers((c) => ({ ...c, [focusThreadKey]: { text: focusDraft, draftId: null } }));
    }
  }, [focusThreadKey, focusDraft]);

  const thread = threads.find((t) => t.key === selected) ?? null;
  const key = thread?.key ?? '';
  const composer = composers[key] ?? EMPTY;
  const text = composer.text;
  const draftId = composer.draftId;
  const isDrafting = Boolean(drafting[key]);
  const sending = Boolean(sendingMap[key]);
  const lastMessageId = thread?.messages[thread.messages.length - 1]?.id;

  const lastInbound = thread ? [...thread.messages].reverse().find((m) => m.direction === 'in') : undefined;
  const chips = lastInbound && suggestions[key]?.for_id === lastInbound.id ? suggestions[key].replies : [];
  useEffect(() => {
    if (key && lastInbound && suggestions[key]?.for_id !== lastInbound.id) onRequestSuggestions?.(key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, lastInbound?.id]);

  const setComposer = (k: string, next: Composer) => setComposers((c) => ({ ...c, [k]: next }));
  const setText = (value: string) => {
    setComposer(key, { ...composer, text: value });
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }));
  };
  const clearTimer = (k: string) => {
    window.clearTimeout(timers.current[k]);
    delete timers.current[k];
  };

  useEffect(() => {
    if (thread && thread.unread > 0) onMarkRead(thread.key);
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [key, lastMessageId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Apply each draft exactly once, to the thread that requested it.
  useEffect(() => {
    Object.entries(drafts).forEach(([k, d]) => {
      if (seenDrafts.current[k] === d) return;
      seenDrafts.current[k] = d;
      if (!drafting[k]) return; // not requested (or already given up on)
      clearTimer(`draft:${k}`);
      setDraftingMap((m) => ({ ...m, [k]: false }));
      if (d.draft) setComposer(k, { text: d.draft, draftId: d.draft_id });
      if (d.error) setErrors((e) => ({ ...e, [k]: d.error }));
    });
  }, [drafts]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!replyResult) return;
    const k = replyResult.thread_key;
    clearTimer(`send:${k}`);
    setSendingMap((m) => ({ ...m, [k]: false }));
    if (replyResult.ok) setComposer(k, EMPTY);
    setErrors((e) => ({ ...e, [k]: replyResult.ok ? undefined : replyResult.detail }));
  }, [replyResult]); // eslint-disable-line react-hooks/exhaustive-deps

  // A dropped connection resolves nothing: release every spinner.
  useEffect(() => {
    if (!connected) {
      setDraftingMap({});
      setSendingMap({});
    }
  }, [connected]);

  useEffect(() => () => Object.keys(timers.current).forEach(clearTimer), []); // eslint-disable-line react-hooks/exhaustive-deps

  const selectThread = (k: string) => {
    setSelected(k);
    setMobileThread(true);
  };

  const requestDraft = () => {
    if (!thread || isDrafting) return;
    const k = thread.key;
    setDraftingMap((m) => ({ ...m, [k]: true }));
    setErrors((e) => ({ ...e, [k]: undefined }));
    onRequestDraft(k);
    timers.current[`draft:${k}`] = window.setTimeout(() => {
      setDraftingMap((m) => ({ ...m, [k]: false }));
      setErrors((e) => ({ ...e, [k]: 'No draft came back — is the daemon running?' }));
    }, REQUEST_TIMEOUT_MS);
  };

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!thread || !text.trim() || sending || !connected) return;
    const k = thread.key;
    setSendingMap((m) => ({ ...m, [k]: true }));
    onReply(k, text.trim(), draftId);
    clearTimer(`send:${k}`);
    timers.current[`send:${k}`] = window.setTimeout(() => {
      setSendingMap((m) => ({ ...m, [k]: false }));
      setErrors((er) => ({ ...er, [k]: 'No confirmation from the bridge — check the chat before resending.' }));
    }, REQUEST_TIMEOUT_MS);
  };

  const bridges = [
    { label: 'Telegram', on: Boolean(telemetry?.telegram_user_active || telemetry?.telegram_active) },
    { label: 'Discord bot', on: Boolean(telemetry?.discord_bot_active) },
    { label: 'Chrome ext', on: (telemetry?.extension_clients ?? 0) > 0 },
  ];
  const errorText = errors[key];

  return (
    <div className="relative z-10 flex-1 flex flex-col min-h-0">
      {/* Filter + bridge status */}
      <div className="px-3 py-2 border-b border-white/10 bg-black/30 flex items-center justify-between gap-2">
        <div className="flex gap-1.5">
          {(['all', 'telegram', 'discord', 'messenger'] as const).map((p) => (
            <button
              key={p}
              onClick={() => setFilter(p)}
              data-active={filter === p}
              className="emboss-pill rounded-full px-2.5 py-1 text-[11.5px]"
              style={{ color: p === 'all' ? '#DCE7FF' : PLATFORM[p].color }}
            >
              {p === 'all' ? 'All' : PLATFORM[p].label}
            </button>
          ))}
        </div>
        <div className="flex gap-2 text-[11px] text-white/78 items-center">
          {onDigest && (
            <button
              type="button"
              onClick={onDigest}
              title="Summarise muted group chats now"
              className="emboss-pill rounded-full px-2 py-0.5 flex items-center gap-1 text-[#26A5E4] hover:brightness-125"
            >
              <ScrollText size={10} /> Digest
            </button>
          )}
          {bridges.map((b) => (
            <span key={b.label} className="flex items-center gap-1">
              <span className={`h-1.5 w-1.5 rounded-full ${b.on ? 'bg-[#3DFFA8] shadow-[0_0_5px_#3DFFA8]' : 'bg-white/25'}`} />
              {b.label}
            </span>
          ))}
        </div>
      </div>

      {threads.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center text-center px-8 space-y-3">
          <div className="emboss w-12 h-12 rounded-2xl flex items-center justify-center">
            <Inbox size={22} className="text-[#7FA6FF]" />
          </div>
          <h3 className="text-sm text-starlight">Unified Comms is listening</h3>
          <p className="text-[12px] text-white/80 leading-relaxed max-w-sm">
            Pin your <b>messenger.com</b> and <b>discord.com/channels/@me</b> tabs in Chrome (click the Orion icon on the
            tab), DM your Orion Discord bot, or add <b>TELEGRAM_API_ID/HASH</b> to mirror personal Telegram chats.
          </p>
        </div>
      ) : (
        <div className="flex-1 flex min-h-0">
          {/* Thread list */}
          <div
            className={`overflow-y-auto p-2 space-y-1.5 bg-black/20 ${
              mobile ? (mobileThread && thread ? 'hidden' : 'w-full') : 'w-[190px] shrink-0 border-r border-white/10'
            }`}
          >
            {visible.map((t) => {
              const p = PLATFORM[t.platform];
              const last = t.messages[t.messages.length - 1];
              const active = thread?.key === t.key;
              return (
                <button
                  key={t.key}
                  onClick={() => selectThread(t.key)}
                  data-active={active}
                  className={`w-full text-left rounded-xl px-2.5 py-2 transition-all ${
                    active ? 'emboss-pill border-white/20' : 'hover:bg-white/[0.04] border border-transparent'
                  }`}
                >
                  <div className="flex items-center gap-1.5">
                    <span
                      className="shrink-0 rounded-md px-1 py-[1px] text-[10.5px] font-extrabold"
                      style={{ color: p.color, background: `${p.color}22`, border: `1px solid ${p.color}55` }}
                    >
                      {p.short}
                    </span>
                    {t.vip && <Star size={10} className="shrink-0 text-[#FFB300] fill-[#FFB300]" />}
                    <span className={`truncate text-[12px] font-bold ${t.muted ? 'text-white/74' : 'text-white/90'}`}>{t.thread_name}</span>
                    {t.muted && <BellOff size={10} className="shrink-0 text-white/68" />}
                    {t.unread > 0 && (
                      <span className="ml-auto shrink-0 rounded-full bg-[#00E5FF] px-1.5 text-[11px] font-extrabold text-black">
                        {t.unread}
                      </span>
                    )}
                  </div>
                  {last && (
                    <div className="mt-0.5 truncate text-[11.5px] text-white/76">
                      {last.direction === 'out' ? 'You: ' : ''}
                      {last.text}
                    </div>
                  )}
                </button>
              );
            })}
          </div>

          {/* Conversation */}
          {thread && (!mobile || mobileThread) && (
            <div className="flex-1 flex flex-col min-w-0">
              <div className="px-3 py-2 border-b border-white/10 flex items-center justify-between text-[11.5px]">
                {mobile && (
                  <button
                    type="button"
                    onClick={() => setMobileThread(false)}
                    className="mr-2 -ml-1 p-1.5 rounded-full text-white/85 hover:text-white hover:bg-white/10"
                    aria-label="Back to all chats"
                  >
                    <ChevronLeft size={18} />
                  </button>
                )}
                <div className="min-w-0 mr-auto">
                  <div className="font-bold text-[13px] text-white truncate text-emboss-header">{thread.thread_name}</div>
                  <div className="text-white/74">
                    {PLATFORM[thread.platform].label} · replies via {ROUTE_LABEL[thread.route] ?? thread.route}
                  </div>
                </div>
                {onMute && (
                  <button
                    type="button"
                    onClick={() => onMute(thread.key, !thread.muted)}
                    className="mr-2 text-white/76 hover:text-white"
                    title={thread.muted ? 'Unmute (notify me again)' : 'Mute into the 12:00 / 18:00 digest'}
                  >
                    {thread.muted ? <BellOff size={13} /> : <Bell size={13} />}
                  </button>
                )}
                {thread.thread_url && (
                  <a href={thread.thread_url} target="_blank" rel="noreferrer" className="text-white/76 hover:text-white" title="Open chat">
                    <ExternalLink size={13} />
                  </a>
                )}
              </div>

              <div className="flex-1 overflow-y-auto p-3 space-y-2">
                {thread.messages.map((m) => (
                  <div key={m.id} className={`flex ${m.direction === 'out' ? 'justify-end' : 'justify-start'}`}>
                    <div
                      className={`max-w-[82%] rounded-2xl px-3 py-2 text-[12.5px] leading-relaxed ${
                        m.direction === 'out'
                          ? 'emboss-pill text-white rounded-br-md'
                          : 'emboss-well text-white/90 rounded-bl-md'
                      }`}
                      style={m.direction === 'out' ? { borderColor: `${PLATFORM[thread.platform].color}66` } : undefined}
                    >
                      {m.direction === 'in' && thread.thread_name !== m.author && (
                        <div className="text-[11.5px] font-bold" style={{ color: PLATFORM[thread.platform].color }}>
                          {m.author}
                        </div>
                      )}
                      <p className="selectable whitespace-pre-wrap break-words">{m.text}</p>
                      <div className="mt-0.5 text-right text-[10.5px] text-white/72">{hhmm(m.timestamp)}</div>
                    </div>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>

              {errorText && (
                <div className="mx-3 mb-1 flex items-center gap-1.5 text-[11.5px] text-[#FF8FAB]">
                  <AlertTriangle size={12} /> {errorText}
                </div>
              )}

              {chips.length > 0 && !text && (
                <div className="px-2.5 pt-2 flex flex-wrap gap-1.5" aria-label="Suggested replies">
                  {chips.map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => setText(c)}
                      className="emboss-pill rounded-full px-3 py-1 text-[12px] text-white/85 max-w-full truncate"
                      title="Use this reply (you can edit it before sending)"
                    >
                      {c}
                    </button>
                  ))}
                </div>
              )}

              {/* Orion input bar → dispatches through the originating bridge */}
              <form onSubmit={submit} className="p-2.5 border-t border-white/10 bg-black/50 flex items-end gap-2">
                <button
                  type="button"
                  onClick={requestDraft}
                  disabled={isDrafting || !connected}
                  aria-label="Draft a reply in your voice"
                  className="emboss-pill rounded-xl p-2.5 text-[#7FA6FF]"
                  title="Draft a reply in your voice"
                >
                  {isDrafting ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                </button>
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => {
                    // Never send the Enter that confirms an IME (Japanese / Chinese / Korean) composition.
                    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      submit();
                    }
                  }}
                  aria-label={`Reply to ${thread.thread_name}`}
                  rows={Math.min(5, Math.max(1, text.split('\n').length))}
                  placeholder={`Reply to ${thread.thread_name} via ${PLATFORM[thread.platform].label}…`}
                  className="selectable emboss-well flex-1 resize-none rounded-xl px-3 py-2.5 text-[12.5px] text-starlight placeholder:text-white/55 focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={!text.trim() || sending || !connected}
                  aria-label="Send reply"
                  className="rounded-xl p-2.5 text-white disabled:opacity-30 shadow-emboss"
                  style={{ background: `linear-gradient(180deg, ${PLATFORM[thread.platform].color}, #4C3BCF)` }}
                  title="Send"
                >
                  {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                </button>
              </form>
              {draftId && (
                <div className="px-3 pb-2 -mt-1 text-[11px] text-white/74">
                  Drafted in your voice — edits you make here are learned.
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
