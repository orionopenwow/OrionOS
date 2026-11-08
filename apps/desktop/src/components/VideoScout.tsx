import { useCallback, useEffect, useState } from 'react';
import { onOrionOpen, setMenuBadge } from '../lib/menu';
import { motion, AnimatePresence } from 'framer-motion';
import { Clapperboard, X, Loader2, Trophy, ExternalLink, AlertTriangle, Copy, Download, FileText, Send } from 'lucide-react';
import { ORION_HTTP_URL } from '../lib/assets';
import '../styles/glass.css';

/**
 * Video Scout (daemon: video_scout.py). Paste TikTok / YouTube / Shorts / Instagram Reels links; Orion fetches,
 * transcribes and summarises them. One card per video, plus a "Top WoW addons" leaderboard.
 * Self-contained: polls the daemon's /scout routes over HTTP.
 */

interface Mention { name: string; kind: string; count: number; first_t: number | null }
interface Summary {
  bullets: string[];
  addons: { name: string; what: string; link: string }[];
  projects: { name: string; what: string; link: string }[];
  links: string[];
  claims: string[];
  verdict: string;
  verdict_reason: string;
}
interface Card {
  id: string;
  url: string;
  platform: string;
  title: string;
  uploader: string;
  followers: number | null;
  upload_ts: number | null;
  status: string;
  error: string | null;
  notes: string[];
  transcript_source: string | null;
  stats: { views: number | null; likes: number | null; comments: number | null; reposts: number | null };
  virality: { views_per_day?: number | null; engagement?: number | null; vs_followers?: number | null; vs_creator?: number | null; velocity?: number | null; label?: string };
  summary: Summary | null;
  top_mentions: Mention[];
}
interface Ranked { name: string; score: number; videos: number; mentions: number; trend: string; best_video: { title: string; url: string } | null }
interface Board { days: number; videos: number; addons: Ranked[]; projects: Ranked[]; keywords: Ranked[]; new_on_github: { name: string; url: string }[] }
interface Status { fetcher: boolean; voice: boolean; ocr: boolean; queued: number; working: string[] }

function human(n: number | null | undefined): string {
  if (n == null) return '–';
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, '')}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  return String(Math.round(n));
}

function fmtT(t: number | null): string {
  if (t == null) return '';
  const s = Math.floor(t);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const VERDICT_STYLE: Record<string, string> = {
  'worth checking': 'text-[#3DFFA8] border-[#3DFFA8]/40 bg-[#3DFFA8]/10',
  maybe: 'text-[#FFB300] border-[#FFB300]/40 bg-[#FFB300]/10',
  skip: 'text-white/50 border-white/15 bg-white/5',
};
const TREND_STYLE: Record<string, string> = {
  new: 'text-[#00E5FF]', rising: 'text-[#3DFFA8]', steady: 'text-white/50', falling: 'text-[#FF5C8A]',
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ORION_HTTP_URL}${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error || `Orion answered ${res.status}`);
  return data as T;
}

function VideoCard({ c }: { c: Card }) {
  const [open, setOpen] = useState(false);
  const busy = c.status === 'working' || c.status === 'queued';
  const failed = c.status === 'blocked' || c.status === 'error';
  const s = c.summary;
  const addons = c.top_mentions.filter((m) => m.kind === 'addon');
  const others = c.top_mentions.filter((m) => m.kind !== 'addon').slice(0, 6);
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-[9px] font-mono uppercase tracking-wider text-[#C084FC]">
          {c.platform}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold text-white/90" title={c.title || c.url}>{c.title || c.url}</div>
          <div className="text-[11px] text-white/45">
            {c.uploader || 'unknown'}{c.followers ? ` · ${human(c.followers)} followers` : ''}
          </div>
        </div>
        <a href={c.url} target="_blank" rel="noreferrer" className="rounded p-1 text-white/40 hover:bg-white/10 hover:text-white" title="Open the video">
          <ExternalLink size={13} />
        </a>
      </div>

      {busy && (
        <div className="mt-2 flex items-center gap-2 text-[12px] text-[#00E5FF]">
          <Loader2 size={13} className="animate-spin" /> Watching it now…
        </div>
      )}
      {failed && (
        <div className="mt-2 flex items-start gap-2 rounded-lg border border-[#FF5C8A]/30 bg-[#FF5C8A]/10 p-2 text-[12px] text-[#FFB3C8]">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {c.error}
        </div>
      )}

      {c.status === 'done' && (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-white/60">
            <span>{human(c.stats.views)} views</span>
            <span>{human(c.virality.views_per_day)}/day</span>
            {c.virality.engagement != null && <span>{(c.virality.engagement * 100).toFixed(1)}% engaged</span>}
            {c.virality.vs_creator != null && <span>{c.virality.vs_creator}× their usual</span>}
            {c.virality.velocity != null && <span>+{human(c.virality.velocity)}/day lately</span>}
            {c.virality.label && c.virality.label !== 'unknown' && (
              <span className="rounded-full border border-white/15 px-1.5 text-white/70">{c.virality.label}</span>
            )}
          </div>
          {s && (
            <>
              <div className="mt-2 flex items-center gap-2">
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${VERDICT_STYLE[s.verdict] || VERDICT_STYLE.maybe}`}>
                  {s.verdict}
                </span>
                <span className="truncate text-[11px] text-white/50" title={s.verdict_reason}>{s.verdict_reason}</span>
              </div>
              <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[12px] leading-snug text-white/80">
                {s.bullets.map((b, i) => <li key={i}>{b}</li>)}
              </ul>
            </>
          )}
          {addons.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {addons.map((m) => (
                <span key={m.name} className="rounded-md border border-[#00E5FF]/30 bg-[#00E5FF]/10 px-1.5 py-0.5 text-[11px] text-[#9FF3FF]" title={m.first_t != null ? `first at ${fmtT(m.first_t)}` : undefined}>
                  {m.name}{m.count > 1 ? ` ×${m.count}` : ''}{m.first_t != null ? ` @${fmtT(m.first_t)}` : ''}
                </span>
              ))}
            </div>
          )}
          <button onClick={() => setOpen(!open)} className="mt-2 text-[11px] text-[#C084FC] hover:underline">
            {open ? 'Less' : 'More'}
          </button>
          {open && (
            <div className="mt-1 space-y-1 text-[11px] text-white/60">
              {others.length > 0 && <div>Also: {others.map((m) => `${m.name} (${m.kind})`).join(', ')}</div>}
              {s && s.projects.length > 0 && <div>Projects: {s.projects.map((p) => p.name).join(', ')}</div>}
              {s && s.claims.length > 0 && <div>Claims to check: {s.claims.join(' · ')}</div>}
              {s && s.links.length > 0 && <div className="break-all">Links: {s.links.join(' · ')}</div>}
              <div>Transcript from: {c.transcript_source || 'nothing'}</div>
              {c.notes.map((n, i) => <div key={i} className="text-[#FFB300]/80">{n}</div>)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Leaderboard() {
  const [days, setDays] = useState(7);
  const [board, setBoard] = useState<Board | null>(null);
  const [msg, setMsg] = useState('');

  const load = useCallback(() => {
    api<Board>(`/scout/leaderboard?days=${days}`).then(setBoard).catch((e) => setMsg(String(e.message || e)));
  }, [days]);
  useEffect(load, [load]);

  const copyMd = async () => {
    const res = await fetch(`${ORION_HTTP_URL}/scout/leaderboard?days=${days}&format=md`);
    await navigator.clipboard.writeText(await res.text());
    setMsg('Copied as Markdown.');
  };
  const downloadCsv = async () => {
    const res = await fetch(`${ORION_HTTP_URL}/scout/leaderboard?days=${days}&format=csv`);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = 'video-scout-leaderboard.csv';
    a.click();
    URL.revokeObjectURL(url);
  };
  const toIdeas = async () => {
    try {
      const r = await api<{ saved: string }>('/scout/leaderboard/ideas', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days }),
      });
      setMsg(`Added to ${r.saved}`);
    } catch (e) {
      setMsg(String((e as Error).message || e));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1">
        {[7, 30, 0].map((d) => (
          <button key={d} onClick={() => setDays(d)}
            className={`rounded-full px-2.5 py-0.5 text-[11px] ${days === d ? 'bg-[#7C5CFF] text-white' : 'bg-white/5 text-white/60 hover:bg-white/10'}`}>
            {d === 7 ? 'This week' : d === 30 ? '30 days' : 'All time'}
          </button>
        ))}
        <span className="ml-auto text-[11px] text-white/40">{board ? `${board.videos} videos` : ''}</span>
      </div>
      {board && board.addons.length === 0 && (
        <div className="text-[12px] text-white/50">No addon mentions yet in this period. Send Orion some WoW videos.</div>
      )}
      {board && board.addons.length > 0 && (
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-white/40">
              <th className="py-1 pr-1">#</th><th>Addon</th><th className="text-right">Score</th><th className="text-right">Videos</th><th className="pl-2">Trend</th>
            </tr>
          </thead>
          <tbody>
            {board.addons.map((a, i) => (
              <tr key={a.name} className="border-t border-white/5 text-white/80">
                <td className="py-1 pr-1 text-white/40">{i + 1}</td>
                <td className="truncate">
                  {a.best_video?.url ? <a href={a.best_video.url} target="_blank" rel="noreferrer" title={a.best_video.title} className="hover:underline">{a.name}</a> : a.name}
                </td>
                <td className="text-right font-mono">{a.score}</td>
                <td className="text-right">{a.videos}</td>
                <td className={`pl-2 ${TREND_STYLE[a.trend] || ''}`}>{a.trend}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {board && board.projects.length > 0 && (
        <div className="text-[11px] text-white/60">Projects: {board.projects.slice(0, 10).map((p) => `${p.name} (${p.trend})`).join(', ')}</div>
      )}
      {board && board.new_on_github.length > 0 && (
        <div className="text-[11px] text-white/60">New on GitHub: {board.new_on_github.slice(0, 10).map((p) => p.name).join(', ')}</div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <button onClick={copyMd} className="flex items-center gap-1 rounded-lg bg-white/5 px-2 py-1 text-[11px] text-white/70 hover:bg-white/10"><Copy size={11} /> Markdown</button>
        <button onClick={downloadCsv} className="flex items-center gap-1 rounded-lg bg-white/5 px-2 py-1 text-[11px] text-white/70 hover:bg-white/10"><Download size={11} /> CSV</button>
        <button onClick={toIdeas} className="flex items-center gap-1 rounded-lg bg-white/5 px-2 py-1 text-[11px] text-white/70 hover:bg-white/10"><FileText size={11} /> Send to IDEAS.md</button>
      </div>
      {msg && <div className="text-[11px] text-white/50">{msg}</div>}
    </div>
  );
}

export default function VideoScout() {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'videos' | 'board'>('videos');
  const [videos, setVideos] = useState<Card[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [link, setLink] = useState('');
  const [msg, setMsg] = useState('');
  const [online, setOnline] = useState(true);

  const refresh = useCallback(() => {
    api<{ videos: Card[]; status: Status }>('/scout/videos?limit=40')
      .then((d) => { setVideos(d.videos); setStatus(d.status); setOnline(true); })
      .catch(() => setOnline(false));
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, open ? 5000 : 30000);
    return () => window.clearInterval(id);
  }, [open, refresh]);

  const submit = async () => {
    const text = link.trim();
    if (!text) return;
    try {
      const r = await api<{ message: string }>('/scout', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
      });
      setMsg(r.message);
      setLink('');
      refresh();
    } catch (e) {
      setMsg(String((e as Error).message || e));
    }
  };

  // [simple-menu] opens from the menu (More > Video Scout) instead of its own floating button
  useEffect(() => onOrionOpen('scout', () => setOpen(true)), []);
  const working = videos.filter((v) => v.status === 'working').length + (status?.queued || 0);
  useEffect(() => setMenuBadge('scout', working), [working]);

  return (
    <>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 24 }}
            transition={{ type: 'spring', stiffness: 320, damping: 30 }}
            className="orion-glass orion-glass--dense fixed right-4 top-12 bottom-24 z-[55] flex w-[420px] max-w-[calc(100vw-32px)] flex-col"
          >
            <div className="flex items-center gap-2 border-b border-white/5 px-4 py-3">
              <Clapperboard size={15} className="text-[#C084FC]" />
              <span className="text-[13px] font-semibold text-white/90">Video Scout</span>
              {working > 0 && <Loader2 size={12} className="animate-spin text-[#00E5FF]" aria-label="Working" />}
              {!online && <span className="text-[11px] text-white/40">Orion isn't answering</span>}
              <div className="ml-3 flex gap-1">
                <button onClick={() => setTab('videos')} className={`rounded-full px-2 py-0.5 text-[11px] ${tab === 'videos' ? 'bg-white/10 text-white' : 'text-white/50'}`}>Videos</button>
                <button onClick={() => setTab('board')} className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] ${tab === 'board' ? 'bg-white/10 text-white' : 'text-white/50'}`}>
                  <Trophy size={11} /> Top addons
                </button>
              </div>
              <button onClick={() => setOpen(false)} className="ml-auto rounded-lg p-1 text-white/40 hover:bg-white/10 hover:text-white" title="Close">
                <X size={14} />
              </button>
            </div>
            <div className="flex-1 space-y-2 overflow-y-auto px-4 py-3">
              {tab === 'videos' ? (
                <>
                  <div className="flex gap-1.5">
                    <input
                      value={link}
                      onChange={(e) => setLink(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
                      placeholder="Paste TikTok / YouTube / Reels links"
                      className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-2.5 py-1.5 text-[12px] text-white placeholder:text-white/30 focus:border-[#7C5CFF]/60 focus:outline-none"
                    />
                    <button onClick={submit} className="rounded-lg bg-[#7C5CFF] px-2.5 text-white hover:bg-[#6a4cf0]" title="Scout">
                      <Send size={13} />
                    </button>
                  </div>
                  {msg && <div className="text-[11px] text-white/55">{msg}</div>}
                  {status && (!status.fetcher || !status.voice || !status.ocr) && (
                    <div className="text-[11px] text-[#FFB300]/80">
                      {!status.fetcher && 'Downloader missing (pip install yt-dlp). '}
                      {!status.voice && 'Voice add-on not installed: captions and descriptions only. '}
                      {!status.ocr && 'On-screen text needs ffmpeg and Tesseract.'}
                    </div>
                  )}
                  {videos.length === 0 && (
                    <div className="pt-6 text-center text-[12px] text-white/45">
                      Nothing scouted yet. Paste a link here, say "scout &lt;link&gt;" in chat, or share a video to Orion from your phone.
                    </div>
                  )}
                  {videos.map((c) => <VideoCard key={c.id} c={c} />)}
                </>
              ) : (
                <Leaderboard />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
