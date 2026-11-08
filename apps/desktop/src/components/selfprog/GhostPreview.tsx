import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Check, ChevronDown, Minus, ChevronUp, Fingerprint, GitBranch, Loader2, RotateCcw, Sparkles, X } from 'lucide-react';
import { selfprog, type SelfChange, type SelfState } from './selfprogApi';

/**
 * The ghost preview (after-1.0 plan §7, Phase 8 "Living Orion").
 *
 * When Orion has built and tested a change to itself in a separate copy, this glass card appears in the corner:
 * what it changes and why, the checks it ran, the diff, and for screen changes the before/after screenshots and a
 * live "ghost" of the new screen (side by side, or small in the corner). Approve swaps it in; changes to the
 * protected core say they need your phone instead. After a swap a small pill offers Undo for the 2-minute watch,
 * and this screen tells Orion it came back (so a broken screen change is put back automatically).
 */
const POLL_MS = 4000;

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="selectable emboss-well max-h-56 overflow-auto rounded-xl p-2 text-[10.5px] leading-[1.35]">
      {diff.split('\n').slice(0, 600).map((ln, i) => (
        <div
          key={i}
          className={
            ln.startsWith('+') && !ln.startsWith('+++')
              ? 'text-[#9AF5C2]'
              : ln.startsWith('-') && !ln.startsWith('---')
                ? 'text-[#FF9BB3]'
                : ln.startsWith('@@')
                  ? 'text-[#A5B4FC]'
                  : 'text-white/55'
          }
        >
          {ln || ' '}
        </div>
      ))}
    </pre>
  );
}

function CheckRow({ c }: { c: SelfChange['checks'][number] }) {
  const color = c.skipped ? 'text-white/45' : c.ok ? 'text-[#9AF5C2]' : 'text-[#FF9BB3]';
  return (
    <li className="flex gap-2 text-[11.5px]" title={c.detail}>
      <span className={`${color} mt-0.5 shrink-0`}>{c.skipped ? <Minus size={11} /> : c.ok ? <Check size={11} /> : <X size={11} />}</span>
      <span className="text-white/80">{c.label}</span>
      <span className="truncate text-white/40">{c.skipped ? `skipped: ${c.detail}` : c.ok ? '' : c.detail.split('\n').slice(-1)[0]}</span>
    </li>
  );
}

export default function GhostPreview() {
  const [state, setState] = useState<SelfState | null>(null);
  const [open, setOpen] = useState<SelfChange | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [layout, setLayout] = useState<'corner' | 'side'>('corner');
  const [ghostUrl, setGhostUrl] = useState<string | null>(null);
  const [shots, setShots] = useState<{ before?: string; after?: string }>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [hidden, setHidden] = useState<string[]>([]);
  const reported = useRef<string>('');
  const lastSwap = useRef<string>('');

  const refresh = useCallback(async () => {
    try {
      const s = await selfprog.state();
      setState(s);
      // This screen is up: tell Orion, so a screen change on probation is kept.
      if (s.probation?.needs_ui && !s.probation.ui_ok && reported.current !== s.probation.swap_id) {
        reported.current = s.probation.swap_id;
        selfprog.uiOk().catch(() => {});
      }
      // A screen change went in or was put back: reload in place (the dev server also hot-reloads by itself).
      const top = s.swaps[0];
      const key = top ? `${top.id}:${top.status}` : '';
      if (lastSwap.current && key && key !== lastSwap.current && top.touches_ui && top.status !== 'kept') {
        window.setTimeout(() => window.location.reload(), 600);
      }
      lastSwap.current = key;
    } catch {
      /* daemon not up yet, or the guest demo */
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  const ready = state?.changes.find((c) => (c.status === 'ready' || c.status === 'building') && !hidden.includes(c.id));

  // Load the full change (diff), its screenshots and the live ghost when a change is waiting.
  useEffect(() => {
    let alive = true;
    const urls: string[] = [];
    if (!ready || ready.status !== 'ready') {
      setOpen(ready ?? null);
      return;
    }
    (async () => {
      try {
        const full = await selfprog.change(ready.id);
        if (!alive) return;
        setOpen(full);
        const next: { before?: string; after?: string } = {};
        for (const which of ['before', 'after'] as const) {
          if (full.screens?.[which]) {
            const u = await selfprog.screen(full.id, which);
            if (u) {
              urls.push(u);
              next[which] = u;
            }
          }
        }
        if (alive) setShots(next);
        if (full.touches_ui && full.preview_dir) {
          const p = await selfprog.preview(full.id);
          if (alive) setGhostUrl(p.url);
        } else setGhostUrl(null);
      } catch {
        /* ignore: next poll tries again */
      }
    })();
    return () => {
      alive = false;
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [ready?.id, ready?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<{ message?: string } | unknown>, done?: string) => {
    setBusy(true);
    setNote('');
    try {
      const r = (await fn()) as { message?: string };
      setNote(r?.message || done || '');
      await refresh();
    } catch (e) {
      setNote(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const probationSwap = state?.probation ? state.swaps.find((s) => s.id === state.probation?.swap_id) : undefined;
  const top = state?.swaps[0];
  const lastRolledBack =
    top?.status === 'rolled_back' && Date.now() / 1000 - (top.rolled_back_at ?? 0) < 600 ? top : undefined;
  const isProtected = open?.kind === 'protected_core';
  const failed = open?.checks.filter((c) => !c.ok && !c.skipped) ?? [];

  return (
    <>
      <AnimatePresence>
        {open && (
          <motion.div
            key={open.id}
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16 }}
            className={`fixed z-[84] ${layout === 'side' ? 'inset-y-16 right-4 flex w-[min(1100px,calc(100vw-2rem))] gap-3' : 'bottom-20 right-4 w-[380px]'}`}
            role="dialog"
            aria-label="Orion's change to itself"
          >
            <div className={`glass flex flex-col gap-2.5 p-3.5 ${layout === 'side' ? 'w-[400px] shrink-0 overflow-y-auto' : ''}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-1.5 text-[12.5px] font-bold text-[#C4B5FD]">
                  <Sparkles size={14} />
                  {open.status === 'building' ? 'Orion is building and testing a change to itself…' : 'Orion changed a copy of itself'}
                </div>
                <button aria-label="Hide" className="text-white/50 hover:text-white" onClick={() => setHidden((h) => [...h, open.id])}>
                  <X size={14} />
                </button>
              </div>
              <p className="selectable text-[12.5px] text-white/90">{open.summary}</p>
              {open.reason && <p className="selectable text-[11.5px] text-white/55">Why: {open.reason}</p>}
              <p className="text-[11px] text-white/45">
                {open.files.length} file{open.files.length === 1 ? '' : 's'} ·{' '}
                {open.kind === 'ui_code' ? 'screen' : open.kind === 'daemon_code' ? "Orion's brain (restarts in about a second)" : 'protected core'}
                {open.mode === 'layer' ? ' · kept in the Orion layer' : ''}
              </p>
              {open.status === 'building' ? (
                <div className="flex items-center gap-2 text-[11.5px] text-white/60">
                  <Loader2 size={13} className="animate-spin" /> Running Orion's tests in a separate copy
                </div>
              ) : (
                <ul className="space-y-1">{open.checks.map((c) => <CheckRow key={c.name} c={c} />)}</ul>
              )}
              {isProtected && (
                <div className="emboss-well flex gap-2 rounded-xl p-2 text-[11.5px] text-[#FDE68A]">
                  <Fingerprint size={14} className="mt-0.5 shrink-0" />
                  <span>{open.protected_words} {open.decision ? 'Waiting for your phone.' : ''}</span>
                </div>
              )}
              {open.diff && (
                <button className="flex items-center gap-1 text-[11px] text-white/55 hover:text-white" onClick={() => setShowDiff((v) => !v)}>
                  {showDiff ? <ChevronUp size={12} /> : <ChevronDown size={12} />} {showDiff ? 'Hide' : 'Show'} the exact change
                </button>
              )}
              {showDiff && open.diff && <DiffView diff={open.diff} />}
              {(shots.before || shots.after) && layout === 'corner' && (
                <div className="grid grid-cols-2 gap-1.5">
                  {(['before', 'after'] as const).map((w) =>
                    shots[w] ? (
                      <figure key={w} className="emboss-well overflow-hidden rounded-lg">
                        <img src={shots[w]} alt={`Screen ${w} the change`} className="w-full" />
                        <figcaption className="px-1.5 py-0.5 text-[10px] text-white/45">{w === 'before' ? 'Now' : 'After'}</figcaption>
                      </figure>
                    ) : null,
                  )}
                </div>
              )}
              {open.touches_ui && (ghostUrl || shots.after) && (
                <button className="emboss-pill self-start rounded-full px-3 py-1 text-[11px]" onClick={() => setLayout((l) => (l === 'corner' ? 'side' : 'corner'))}>
                  {layout === 'corner' ? 'Side by side' : 'Back to the corner'}
                </button>
              )}
              {note && <p className="selectable text-[11px] text-[#A5F3FC]">{note}</p>}
              {open.status === 'ready' && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {isProtected ? (
                    <button disabled={busy || !!open.decision} className="emboss-pill flex items-center gap-1 rounded-full px-3 py-1.5 text-[12px] text-[#FDE68A]" onClick={() => act(() => selfprog.askPhone(open.id))}>
                      <Fingerprint size={13} /> {open.decision ? 'Asked your phone' : 'Ask my phone'}
                    </button>
                  ) : (
                    <button disabled={busy || failed.length > 0} className="emboss-pill flex items-center gap-1 rounded-full px-3 py-1.5 text-[12px] text-[#9AF5C2]" onClick={() => act(() => selfprog.approve(open.id), 'Swapped in. Say "Orion, undo that" to put it back.')}>
                      <Check size={13} /> Approve
                    </button>
                  )}
                  <button disabled={busy} className="emboss-pill rounded-full px-3 py-1.5 text-[12px]" onClick={() => act(() => selfprog.reject(open.id), 'Thrown away.')}>
                    Not this
                  </button>
                  <button disabled={busy} className="emboss-pill flex items-center gap-1 rounded-full px-3 py-1.5 text-[12px] text-white/70" title="Makes a branch ready for a pull request. Nothing is sent." onClick={() => act(() => selfprog.upstream(open.id))}>
                    <GitBranch size={13} /> Offer upstream
                  </button>
                </div>
              )}
            </div>
            {layout === 'side' && (
              <div className="glass flex min-w-0 flex-1 flex-col gap-2 p-2">
                <div className="grid min-h-0 flex-1 grid-cols-2 gap-2">
                  <div className="emboss-well flex flex-col overflow-hidden rounded-xl">
                    <span className="px-2 py-1 text-[10.5px] text-white/45">Now</span>
                    {shots.before ? <img src={shots.before} alt="The screen now" className="w-full object-contain" /> : <span className="p-3 text-[11px] text-white/40">No screenshot of the current screen.</span>}
                  </div>
                  <div className="emboss-well flex flex-col overflow-hidden rounded-xl">
                    <span className="px-2 py-1 text-[10.5px] text-white/45">Ghost: after the change</span>
                    {ghostUrl ? (
                      <iframe title="Ghost preview of the changed screen" src={ghostUrl} sandbox="allow-scripts" className="h-full w-full flex-1 border-0" />
                    ) : shots.after ? (
                      <img src={shots.after} alt="The screen after the change" className="w-full object-contain" />
                    ) : null}
                  </div>
                </div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {!open && (probationSwap || lastRolledBack) && (
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="fixed bottom-20 right-4 z-[84]">
            <div className="glass flex max-w-[380px] items-center gap-2 rounded-full px-3 py-1.5 text-[11.5px] text-white/80">
              {probationSwap ? (
                <>
                  <Sparkles size={13} className="text-[#C4B5FD]" />
                  <span className="truncate">Changed: {probationSwap.summary}</span>
                  <button disabled={busy} className="emboss-pill flex items-center gap-1 rounded-full px-2 py-0.5" onClick={() => act(() => selfprog.undo())}>
                    <RotateCcw size={12} /> Undo
                  </button>
                </>
              ) : (
                <span className="truncate" title={lastRolledBack?.rollback_reason}>Put back: {lastRolledBack?.summary}</span>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
