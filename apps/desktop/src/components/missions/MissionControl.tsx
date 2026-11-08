/**
 * Mission Control (after-1.0 plan section 5): send Claude Code, Codex or Gemini CLI out on missions.
 * One board (queued, running, needs you, review, done), a new-mission form with templates, and a detail pane with
 * the live log, the diff, cost so far and Pause / Stop / Approve. Approve only asks for the merge: it still needs
 * your signature on your phone or the boardroom, and nothing is ever pushed. Daemon: missions_routes.py.
 */
import { onOrionOpen, setMenuBadge } from '../../lib/menu';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Loader2, Plus, Rocket, X } from 'lucide-react';
import '../../styles/glass.css';
import { toolColor } from '../swarm/AgentSwarm';
import { ago, usd } from '../../lib/swarmCompute';
import {
  COLUMNS,
  STATE_LABEL,
  missionsApi,
  type Mission,
  type MissionBoard,
  type MissionDetail,
  type MissionOp,
  type NewMission,
} from '../../lib/missions';

const AMBER = '#FFC66B';
const GOOD = '#86EFAC';
const BAD = '#FF7A90';

const verdictColor = (v?: string) => (v === 'green' || v === 'approve' ? GOOD : v === 'amber' || v === 'changes' ? AMBER : 'rgba(255,255,255,0.5)');

function Cost({ m }: { m: Mission }) {
  const pct = m.budget_usd > 0 ? Math.min(100, (m.cost_usd / m.budget_usd) * 100) : 0;
  return (
    <div className="mt-1.5">
      <div className="flex justify-between text-[11.5px] text-white/78">
        <span title={m.cost_note}>
          {usd(m.cost_usd)}
          {m.cost_estimated ? ' (estimate)' : ''}
        </span>
        <span>of {usd(m.budget_usd)}</span>
      </div>
      <div className="mt-0.5 h-1 rounded-full bg-white/10">
        <div className="h-1 rounded-full" style={{ width: `${pct}%`, background: pct >= 90 ? AMBER : '#7DF0FF' }} />
      </div>
    </div>
  );
}

function Card({ m, active, onClick }: { m: Mission; active: boolean; onClick: () => void }) {
  const tint = toolColor(m.agent);
  const tag = m.state === 'failed' || m.state === 'stopped' ? STATE_LABEL[m.state] : '';
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-xl border px-2.5 py-2 text-left transition-colors ${active ? 'bg-white/10' : 'bg-white/[0.03] hover:bg-white/[0.07]'}`}
      style={{ borderColor: active ? `${tint}88` : 'rgba(255,255,255,0.08)' }}
    >
      <div className="flex items-center gap-1.5">
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: tint }} />
        <span className="truncate text-[12px] font-semibold text-white/90">{m.title}</span>
      </div>
      <div className="mt-0.5 truncate text-[11.5px] text-white/76">
        {m.agent_label} · {m.repo_name}
        {m.race_group ? ' · race' : m.swarm_id ? ' · swarm' : ''}
      </div>
      {tag && (
        <span className="mt-1 inline-block rounded-full px-1.5 py-px text-[10.5px] font-bold uppercase" style={{ color: m.state === 'failed' ? BAD : AMBER, background: 'rgba(255,255,255,0.06)' }}>
          {tag}
        </span>
      )}
      <div className="mt-1 line-clamp-2 text-[11.5px] text-white/80">{m.summary}</div>
      <Cost m={m} />
    </button>
  );
}

function NewMissionForm({ board, onDone }: { board: MissionBoard; onDone: (msg: string) => void }) {
  const [f, setF] = useState<NewMission>({ title: '', description: '', repo: '', agent: '', race: 1, time_limit_min: 30 });
  const [budget, setBudget] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const set = (k: keyof NewMission, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const pickTemplate = (id: string) => {
    const t = board.templates.find((x) => x.id === id);
    if (t) setF((x) => ({ ...x, template: t.id, title: t.title, description: t.description, repo: t.repo === 'orion' ? '' : x.repo }));
  };
  const submit = async () => {
    setBusy(true);
    setErr('');
    try {
      const m = await missionsApi.create({
        ...f,
        agent: f.agent || undefined,
        repo: f.repo?.trim() || undefined,
        budget_usd: budget ? Number(budget) : undefined,
      });
      onDone(m.race ? `Race started: ${m.race.length} agents on the same task.` : `Mission queued for ${m.agent_label}.`);
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(false);
  };
  const field = 'w-full rounded-lg border border-white/10 bg-black/30 px-2 py-1.5 text-[12px] text-white outline-none focus:border-cyan-300/50';
  const installed = board.agents.filter((a) => a.mode === 'headless' && a.installed);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {board.templates.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => pickTemplate(t.id)}
            className={`rounded-full border px-2.5 py-1 text-[11.5px] ${f.template === t.id ? 'border-cyan-300/60 bg-cyan-300/10 text-cyan-100' : 'border-white/10 text-white/85 hover:text-white'}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <input className={field} placeholder="What should it build or fix?" value={f.title} onChange={(e) => set('title', e.target.value)} />
      <textarea className={`${field} h-24 resize-none`} placeholder="Details (optional)" value={f.description} onChange={(e) => set('description', e.target.value)} />
      <input className={field} placeholder="Repo folder (blank = Orion itself)" value={f.repo} onChange={(e) => set('repo', e.target.value)} />
      <div className="grid grid-cols-2 gap-2">
        <label className="text-[11.5px] text-white/76">
          Agent
          <select className={field} value={f.agent} onChange={(e) => set('agent', e.target.value)}>
            <option value="">Let Orion pick</option>
            {installed.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11.5px] text-white/76">
          Budget (US$)
          <input className={field} inputMode="decimal" placeholder="Orion's default" value={budget} onChange={(e) => setBudget(e.target.value.replace(/[^0-9.]/g, ''))} />
        </label>
        <label className="text-[11.5px] text-white/76">
          Time limit (minutes)
          <input className={field} inputMode="numeric" value={f.time_limit_min ?? ''} onChange={(e) => set('time_limit_min', Number(e.target.value.replace(/\D/g, '')) || undefined)} />
        </label>
        <label className="text-[11.5px] text-white/76" title="Several agents get the same task; you pick the best result.">
          Race (agents)
          <select className={field} value={f.race} onChange={(e) => set('race', Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n === 1 ? 'Just one' : `${n} agents`}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!installed.length && <div className="text-[11.5px]" style={{ color: AMBER }}>No coding agent is installed yet (Claude Code, Codex or Gemini CLI).</div>}
      {err && <div className="text-[11.5px]" style={{ color: BAD }}>{err}</div>}
      <div className="text-[11.5px] text-white/72">
        Each mission works in its own copy of the repo. It can't merge, push, spend money or touch secrets; finished work comes back for your review.
      </div>
      <button
        type="button"
        disabled={busy || !f.title.trim()}
        onClick={submit}
        className="rounded-lg border border-cyan-300/50 px-3 py-1.5 text-[12px] font-semibold text-cyan-100 hover:bg-cyan-300/10 disabled:opacity-40"
      >
        {busy ? 'Starting...' : 'Send it out'}
      </button>
    </div>
  );
}

function Detail({ id, onNote }: { id: string; onNote: (s: string) => void }) {
  const [d, setD] = useState<MissionDetail | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [tab, setTab] = useState<'log' | 'diff'>('log');
  const [busy, setBusy] = useState(false);
  const next = useRef(0);
  const logEl = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    next.current = 0;
    setLines([]);
    setD(null);
    const load = async () => {
      try {
        const [det, log] = await Promise.all([missionsApi.detail(id), missionsApi.log(id, next.current)]);
        if (!alive) return;
        setD(det);
        if (log.lines.length) {
          next.current = log.next;
          setLines((l) => [...l, ...log.lines].slice(-600));
        }
      } catch {
        /* daemon offline: keep what we have */
      }
    };
    void load();
    const t = window.setInterval(load, 2000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [id]);
  useEffect(() => {
    logEl.current?.scrollTo({ top: logEl.current.scrollHeight });
  }, [lines]);

  if (!d) return <div className="p-4 text-[12px] text-white/76">Loading...</div>;
  const act = async (op: MissionOp) => {
    setBusy(true);
    const r = await missionsApi.act(d.id, op);
    onNote(r.message);
    setBusy(false);
  };
  const btn = 'px-3 py-1.5 rounded-lg text-[12px] font-semibold border transition-colors disabled:opacity-35';
  const live = d.state === 'running' || d.state === 'needs_you' || d.state === 'queued';
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="text-[14px] font-bold text-white">{d.title}</div>
      <div className="text-[11.5px] text-white/78">
        {d.agent_label}
        {d.model ? ` (${d.model})` : ''} · {d.repo_name} · <span className="font-mono">{d.branch}</span>
      </div>
      <div className="mt-1 text-[12px] text-white/80">{d.summary}</div>
      {d.why && <div className="mt-0.5 text-[11.5px] text-white/72">Why this agent: {d.why}</div>}
      <div className="mt-2 grid grid-cols-3 gap-2 text-[11.5px] text-white/80">
        <div>
          <div className="text-white/68">Cost so far</div>
          <div className="text-white/90" title={d.cost_note}>
            {usd(d.cost_usd)} {d.cost_estimated && <span className="text-white/74">(estimate)</span>}
          </div>
        </div>
        <div>
          <div className="text-white/68">Budget</div>
          <div className="text-white/90">{usd(d.budget_usd)}</div>
        </div>
        <div>
          <div className="text-white/68">Time</div>
          <div className="text-white/90">
            {Math.round(d.active_s / 60)} of {Math.round(d.time_limit_min)} min
          </div>
        </div>
      </div>
      {(d.checks || d.review) && (
        <div className="mt-2 space-y-1 rounded-lg border border-white/10 bg-white/[0.03] p-2 text-[12px]">
          {d.checks && (
            <div>
              <span style={{ color: verdictColor(d.checks.verdict) }}>Build and tests: {d.checks.verdict}</span>
              <span className="text-white/76"> {d.checks.reason}</span>
            </div>
          )}
          {d.review && (
            <div>
              <span style={{ color: verdictColor(d.review.verdict) }}>
                Second review ({d.review.reviewer || 'none'}): {d.review.verdict}
              </span>
              {d.review.notes && <div className="mt-0.5 line-clamp-4 text-white/78">{d.review.notes}</div>}
            </div>
          )}
        </div>
      )}
      <div className="mt-2 flex gap-1">
        <button type="button" onClick={() => setTab('log')} className={`rounded-full px-2 py-0.5 text-[11.5px] ${tab === 'log' ? 'bg-white/10 text-white' : 'text-white/76'}`}>
          Live log
        </button>
        <button type="button" onClick={() => setTab('diff')} className={`rounded-full px-2 py-0.5 text-[11.5px] ${tab === 'diff' ? 'bg-white/10 text-white' : 'text-white/76'}`}>
          Changes ({d.changed_files.length})
        </button>
      </div>
      {tab === 'log' ? (
        <div ref={logEl} className="mt-1 min-h-0 flex-1 overflow-y-auto rounded-lg bg-black/40 p-2 font-mono text-[11.5px] leading-snug text-white/88">
          {lines.length ? lines.map((l, i) => <div key={i} className="whitespace-pre-wrap break-words">{l}</div>) : <div className="text-white/72">Nothing yet.</div>}
        </div>
      ) : (
        <pre className="mt-1 min-h-0 flex-1 overflow-auto rounded-lg bg-black/40 p-2 font-mono text-[11.5px] leading-snug">
          {d.diff
            ? d.diff.split('\n').map((l, i) => (
                <div key={i} style={{ color: l.startsWith('+') && !l.startsWith('+++') ? GOOD : l.startsWith('-') && !l.startsWith('---') ? BAD : l.startsWith('@@') ? '#7DF0FF' : 'rgba(255,255,255,0.7)' }}>
                  {l || ' '}
                </div>
              ))
            : <span className="text-white/72">No changes saved yet.</span>}
        </pre>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {d.state === 'running' && (
          <button type="button" disabled={busy} className={`${btn} border-blue-300/40 text-blue-100 hover:bg-blue-300/10`} onClick={() => act('pause')}>
            Pause
          </button>
        )}
        {(d.state === 'needs_you' || d.state === 'failed' || d.state === 'stopped') && (
          <button type="button" disabled={busy} className={`${btn} border-blue-300/40 text-blue-100 hover:bg-blue-300/10`} onClick={() => act('resume')}>
            {d.paused ? 'Resume' : 'Try again'}
          </button>
        )}
        {live && (
          <button type="button" disabled={busy} className={`${btn} border-rose-300/40 text-rose-100 hover:bg-rose-300/10`} onClick={() => act('stop')}>
            Stop
          </button>
        )}
        {d.state === 'review' && (
          <>
            <button
              type="button"
              disabled={busy || d.merge === 'waiting_signature'}
              className={`${btn} border-emerald-300/50 text-emerald-100 hover:bg-emerald-300/10`}
              title="Asks for the merge. You still sign it on your phone or the boardroom."
              onClick={() => act('approve_review')}
            >
              {d.merge === 'waiting_signature' ? 'Waiting for your signature' : 'Approve'}
            </button>
            <button type="button" disabled={busy} className={`${btn} border-rose-300/40 text-rose-100 hover:bg-rose-300/10`} onClick={() => act('reject')}>
              Turn down
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default function MissionControl() {
  const [open, setOpen] = useState(false);
  const [board, setBoard] = useState<MissionBoard | null>(null);
  const [online, setOnline] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [note, setNote] = useState('');

  const refresh = useCallback(() => {
    missionsApi
      .board()
      .then((b) => {
        setBoard(b);
        setOnline(true);
      })
      .catch(() => setOnline(false));
  }, []);
  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, open ? 2500 : 20000);
    return () => window.clearInterval(t);
  }, [open, refresh]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  // [simple-menu] opens from the menu ("missions", or Projects > Mission Control) instead of a floating button
  useEffect(() => onOrionOpen('missions', () => { setOpen(true); refresh(); }), [refresh]);
  const ms = board?.missions ?? [];
  const running = ms.filter((m) => m.state === 'running').length;
  const needs = ms.filter((m) => m.state === 'needs_you' || m.state === 'review').length;
  useEffect(() => setMenuBadge('missions', needs), [needs]);
  if (!online && !board) return null;

  return (
    <>
      <AnimatePresence>
        {open && board && (
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            transition={{ type: 'spring', stiffness: 320, damping: 30 }}
            className="orion-glass orion-glass--dense fixed inset-x-4 top-12 bottom-24 z-[55] mx-auto flex max-w-[1180px] flex-col text-white"
            role="dialog"
            aria-label="Mission Control"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 border-b border-white/5 px-4 py-3">
              <Rocket size={15} className="text-[#7DF0FF]" />
              <span className="text-[13px] font-semibold text-white/90">Mission Control</span>
              {running > 0 && <Loader2 size={12} className="animate-spin text-[#00E5FF]" aria-label={`${running} running`} />}
              <span className="text-[11.5px] text-white/72">
                {board.agents.filter((a) => a.installed && a.mode === 'headless').map((a) => a.label).join(', ') || 'No agents installed'}
              </span>
              <button
                type="button"
                onClick={() => {
                  setCreating(true);
                  setSelected(null);
                }}
                className="ml-auto flex items-center gap-1 rounded-lg border border-cyan-300/40 px-2.5 py-1 text-[12px] text-cyan-100 hover:bg-cyan-300/10"
              >
                <Plus size={12} /> New mission
              </button>
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg p-1 text-white/72 hover:bg-white/10 hover:text-white" title="Close">
                <X size={14} />
              </button>
            </div>
            {note && <div className="border-b border-white/5 px-4 py-1.5 text-[12px] text-white/85">{note}</div>}
            <div className="flex min-h-0 flex-1 flex-col gap-3 p-3 md:flex-row">
              <div className="flex min-h-0 flex-1 gap-2 overflow-x-auto">
                {COLUMNS.map((col) => {
                  const items = ms.filter((m) => col.states.includes(m.state));
                  return (
                    <div key={col.id} className="flex min-h-0 w-[190px] shrink-0 flex-col">
                      <div className="mb-1.5 flex items-center justify-between px-1 text-[11.5px] font-bold uppercase tracking-wider text-white/74">
                        <span style={{ color: col.id === 'needs_you' && items.length ? AMBER : undefined }}>{col.label}</span>
                        <span>{items.length}</span>
                      </div>
                      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
                        {items.map((m) => (
                          <Card
                            key={m.id}
                            m={m}
                            active={selected === m.id}
                            onClick={() => {
                              setSelected(m.id);
                              setCreating(false);
                            }}
                          />
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="min-h-0 w-full overflow-y-auto rounded-xl border border-white/10 bg-black/20 p-3 md:w-[400px] md:shrink-0">
                {creating ? (
                  <NewMissionForm
                    board={board}
                    onDone={(msg) => {
                      setNote(msg);
                      setCreating(false);
                      refresh();
                    }}
                  />
                ) : selected ? (
                  <Detail key={selected} id={selected} onNote={(s) => { setNote(s); refresh(); }} />
                ) : (
                  <div className="text-[12px] text-white/76">
                    Pick a mission to see its live log, changes and cost, or start a new one.
                    {ms[0] && <div className="mt-2 text-white/68">Latest: {ms[0].title} ({ago(ms[0].created)} ago)</div>}
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
