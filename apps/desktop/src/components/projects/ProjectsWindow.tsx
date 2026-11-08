/**
 * Projects (menu: Projects): "Start a project with Claude", and the front door to Mission Control.
 *
 * New project, three steps: 1. name it, 2. pick a starter (web game, website, Python tool, empty),
 * 3. say what you want in plain words. Orion makes ~/Orion Projects/<name>, writes the rules (CLAUDE.md) and a
 * README, makes the first git commit, and starts a mission so Claude Code builds the first version.
 * A project shows its newest mission's progress live, with Open folder, Play it (web game or website) and
 * Ask for a change (a follow-up mission on the same folder). Daemon: apps/daemon/projects.py.
 *
 * The Test Center can open the wizard filled in with a sample idea (you still press Start):
 *   window.dispatchEvent(new CustomEvent('orion:projects-prefill', { detail: { name, starter, idea } }))
 */
import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import { ArrowLeft, FolderGit2, FolderOpen, Loader2, Play, Plus, Rocket, Send } from 'lucide-react';
import GlassWindow, { useWindowShown } from '../glass/GlassWindow';
import { onOrionOpen, openOrion, setMenuBadge } from '../../lib/menu';
import { windowManager } from '../../lib/windowManager';
import { missionsApi, STATE_LABEL, type MissionDetail } from '../../lib/missions';
import { folderName, projectsApi, type Project, type ProjectsSnapshot, type Starter } from '../../lib/projects';

const field =
  'w-full rounded-lg border border-white/10 bg-black/30 px-2.5 py-2 text-[13px] text-white placeholder:text-white/30 focus:border-[#7C5CFF]/60 focus:outline-none';
const primary =
  'flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold text-white disabled:opacity-40';
const primaryStyle = { background: 'rgba(124,92,255,0.45)', border: '1px solid rgba(176,156,255,0.6)' };
const quiet =
  'flex items-center gap-1.5 rounded-full border border-white/10 px-3 py-1.5 text-[12px] text-white/80 hover:bg-white/[0.06] disabled:opacity-40';

const STATE_COLOR: Record<string, string> = {
  queued: '#8A90B8', running: '#7FF2FF', needs_you: '#FFC66B', review: '#FFC66B', done: '#5CFFB0', failed: '#FF5C8A', stopped: '#8A90B8',
};
const IDEA_HINT: Record<Starter['id'], string> = {
  'web-game': 'For example: a space game where a little ship catches stars and dodges rocks. It gets faster over time.',
  website: 'For example: a one-page site for my band with our next gigs and a contact form.',
  'python-tool': 'For example: a tool that renames my photos by the date they were taken.',
  empty: 'Say what you want built, in your own words.',
};

export const PREFILL_EVENT = 'orion:projects-prefill';
export interface ProjectPrefill {
  name: string;
  starter: Starter['id'];
  idea: string;
}

function Wizard({ snap, onMade, prefill }: { snap: ProjectsSnapshot; onMade: (name: string, note: string) => void; prefill?: ProjectPrefill | null }) {
  const [step, setStep] = useState(prefill ? 2 : 0);
  const [name, setName] = useState(prefill?.name ?? '');
  const [starter, setStarter] = useState<Starter['id']>(prefill?.starter ?? 'web-game');
  const [idea, setIdea] = useState(prefill?.idea ?? '');
  const [agent, setAgent] = useState<'claude' | ''>('claude');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const clean = folderName(name);
  const taken = snap.projects.some((p) => p.name.toLowerCase() === clean.toLowerCase());
  const sep = snap.root.includes('\\') ? '\\' : '/';

  const make = async () => {
    setBusy(true);
    setErr('');
    try {
      const r = await projectsApi.create({ name: clean, starter, idea, agent: agent || null });
      onMade(r.project.name, r.message);
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const canNext = step === 0 ? !!clean && !taken : true;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2" aria-live="polite">
        {['Name it', 'Pick a starter', 'Say what you want'].map((t, i) => (
          <span key={t} className="flex items-center gap-1.5 text-[11.5px]" style={{ color: i === step ? '#D8DEFF' : 'rgba(216,222,255,0.4)' }}>
            <span
              aria-hidden
              className="grid h-5 w-5 place-items-center rounded-full text-[10.5px] font-bold"
              style={{ background: i <= step ? 'rgba(124,92,255,0.5)' : 'rgba(216,222,255,0.08)' }}
            >
              {i + 1}
            </span>
            <span className={i === step ? 'font-semibold' : 'sr-only sm:not-sr-only'}>{t}</span>
          </span>
        ))}
        <span className="sr-only">Step {step + 1} of 3</span>
      </div>

      {step === 0 && (
        <div className="space-y-2">
          <label className="block text-[12.5px] font-semibold" htmlFor="project-name">What's it called?</label>
          <input
            id="project-name"
            autoFocus
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && canNext && setStep(1)}
            placeholder="Star Catcher"
            className={field}
          />
          <div className="text-[11.5px] text-white/45">
            {taken ? <span className="text-[#FFC66B]">You already have a project called {clean}.</span> : clean ? <>Orion makes the folder {snap.root}{sep}{clean}</> : 'Letters, numbers, spaces, - and _.'}
          </div>
        </div>
      )}

      {step === 1 && (
        <div role="radiogroup" aria-label="Starter" className="grid gap-2 sm:grid-cols-2">
          {snap.starters.map((s) => (
            <button
              key={s.id}
              type="button"
              role="radio"
              aria-checked={starter === s.id}
              onClick={() => setStarter(s.id)}
              className="rounded-xl px-3 py-2.5 text-left outline-none focus-visible:ring-1 focus-visible:ring-[#00E5FF]/70"
              style={{
                background: starter === s.id ? 'rgba(124,92,255,0.22)' : 'rgba(216,222,255,0.04)',
                border: `1px solid ${starter === s.id ? 'rgba(176,156,255,0.6)' : 'rgba(216,222,255,0.08)'}`,
              }}
            >
              <span className="block text-[13px] font-semibold text-[#D8DEFF]">{s.label}</span>
              <span className="block text-[11.5px] leading-snug text-[#D8DEFF]/55">{s.description}</span>
            </button>
          ))}
        </div>
      )}

      {step === 2 && (
        <div className="space-y-2">
          <label className="block text-[12.5px] font-semibold" htmlFor="project-idea">What do you want? Plain words are fine.</label>
          <textarea id="project-idea" autoFocus value={idea} onChange={(e) => setIdea(e.target.value)} rows={5} placeholder={IDEA_HINT[starter]} className={field} />
          <label className="flex items-center gap-2 text-[12px] text-white/70">
            Who builds it
            <select value={agent} onChange={(e) => setAgent(e.target.value as 'claude' | '')} className="rounded-md border border-white/10 bg-black/40 px-2 py-1 text-[12px] text-white">
              <option value="claude">Claude Code</option>
              <option value="">Let Orion pick</option>
            </select>
          </label>
          <div className="text-[11.5px] text-white/45">
            Claude works on a copy and never pushes anything. When it's done you check it, and your phone signs the merge.
          </div>
        </div>
      )}

      {err && <div role="alert" className="text-[12px] text-[#FF5C8A]">{err}</div>}
      <div className="flex items-center gap-2">
        {step > 0 && (
          <button type="button" className={quiet} onClick={() => setStep(step - 1)} disabled={busy}>
            <ArrowLeft size={13} /> Back
          </button>
        )}
        <span className="flex-1" />
        {step < 2 ? (
          <button type="button" className={primary} style={primaryStyle} disabled={!canNext} onClick={() => setStep(step + 1)}>
            Next
          </button>
        ) : (
          <button type="button" className={primary} style={primaryStyle} disabled={busy || !clean} onClick={make}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />} Start it with Claude
          </button>
        )}
      </div>
    </div>
  );
}

function Progress({ id }: { id: string }) {
  const [d, setD] = useState<MissionDetail | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => missionsApi.detail(id).then((x) => alive && setD(x), () => undefined);
    load();
    const t = window.setInterval(load, 2500);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [id]);
  if (!d) return <div className="text-[12px] text-white/45">Loading the mission…</div>;
  const c = STATE_COLOR[d.state] ?? '#8A90B8';
  return (
    <div className="space-y-1.5 rounded-xl border border-white/10 bg-black/20 p-2.5" aria-live="polite">
      <div className="flex items-center gap-2 text-[12.5px]">
        {d.state === 'running' ? <Loader2 size={13} className="animate-spin" style={{ color: c }} /> : <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: c }} />}
        <span className="font-semibold" style={{ color: c }}>{STATE_LABEL[d.state]}</span>
        <span className="min-w-0 flex-1 truncate text-white/80">{d.title}</span>
        <span className="shrink-0 text-[11px] text-white/45" title={d.cost_note}>${d.cost_usd.toFixed(2)} of ${d.budget_usd.toFixed(2)}</span>
      </div>
      {d.summary && <div className="text-[12px] text-white/65">{d.summary}</div>}
      {d.log_tail?.length > 0 && (
        <pre className="selectable max-h-28 overflow-y-auto whitespace-pre-wrap rounded-lg bg-black/30 p-2 font-mono text-[10.5px] leading-snug text-white/55">
          {d.log_tail.slice(-8).join('\n')}
        </pre>
      )}
      {(d.state === 'review' || d.state === 'needs_you') && (
        <div className="text-[12px] text-[#FFC66B]">
          Claude needs you: check the work in Mission Control. Approving asks your phone to sign the merge.
        </div>
      )}
    </div>
  );
}

function Detail({ p, onNote, onChanged }: { p: Project; onNote: (s: string) => void; onChanged: () => void }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<{ message?: string }>) => {
    setBusy(true);
    try {
      const r = await fn();
      onNote(r.message ?? '');
      onChanged();
    } catch (e) {
      onNote(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };
  const canPlay = p.playable || p.starter === 'web-game' || p.starter === 'website';
  const latest = p.missions[p.missions.length - 1];
  return (
    <div className="space-y-3">
      <div>
        <div className="text-[15px] font-semibold text-[#D8DEFF]">{p.name}</div>
        <div className="selectable truncate text-[11.5px] text-white/45" title={p.path}>{p.path}</div>
        {p.idea && <div className="mt-1 text-[12.5px] text-white/70">{p.idea}</div>}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={quiet} disabled={busy || !p.exists} onClick={() => run(() => projectsApi.open(p.name))}>
          <FolderOpen size={13} /> Open folder
        </button>
        {canPlay && (
          <button type="button" className={primary} style={primaryStyle} disabled={busy || !p.playable} onClick={() => run(() => projectsApi.play(p.name))}
            title={p.playable ? 'Opens it in your browser (only on this computer)' : 'Nothing to play yet'}>
            <Play size={13} /> Play it
          </button>
        )}
        <button type="button" className={quiet} onClick={() => openOrion('missions')}>
          <Rocket size={13} /> Mission Control
        </button>
      </div>
      {latest ? <Progress key={latest} id={latest} /> : <div className="text-[12px] text-white/50">No mission yet. Ask for a change below to start one.</div>}
      <div className="space-y-1.5">
        <label htmlFor="project-change" className="block text-[12.5px] font-semibold">Ask for a change</label>
        <div className="flex gap-1.5">
          <input
            id="project-change"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && text.trim() && !busy) void run(() => projectsApi.change(p.name, text)).then(() => setText(''));
            }}
            placeholder="Make the ship faster and add a high score"
            className={field}
          />
          <button type="button" aria-label="Send the change to Claude" className={primary} style={primaryStyle} disabled={busy || !text.trim()}
            onClick={() => void run(() => projectsApi.change(p.name, text)).then(() => setText(''))}>
            <Send size={13} />
          </button>
        </div>
        {p.missions.length > 1 && <div className="text-[11px] text-white/40">{p.missions.length} missions on this project so far.</div>}
      </div>
    </div>
  );
}

export default function ProjectsWindow({ mobile }: { mobile?: boolean }) {
  const [open, setOpen] = useState(false);
  const shown = useWindowShown('projects', open);
  const [snap, setSnap] = useState<ProjectsSnapshot | null>(null);
  const [view, setView] = useState<'list' | 'new' | string>('list');
  const [note, setNote] = useState('');
  const [offline, setOffline] = useState(false);
  const [prefill, setPrefill] = useState<ProjectPrefill | null>(null);

  const load = useCallback(() => {
    projectsApi.list().then((s) => { setSnap(s); setOffline(false); }, () => setOffline(true));
  }, []);
  useEffect(() => onOrionOpen('projects', () => { windowManager.restore('projects'); setOpen(true); load(); }), [load]);
  useEffect(() => {
    const onPrefill = (e: Event) => {
      const d = (e as CustomEvent<Partial<ProjectPrefill>>).detail;
      if (!d || typeof d.name !== 'string') return;
      const starter = (['web-game', 'website', 'python-tool', 'empty'] as const).find((x) => x === d.starter) ?? 'web-game';
      setPrefill({ name: d.name.slice(0, 80), starter, idea: String(d.idea ?? '').slice(0, 4000) });
      windowManager.restore('projects');
      setOpen(true);
      setNote('A sample idea is filled in. Check it, then press Start it with Claude.');
      setView('new');
      load();
    };
    window.addEventListener(PREFILL_EVENT, onPrefill);
    return () => window.removeEventListener(PREFILL_EVENT, onPrefill);
  }, [load]);
  useEffect(() => {
    if (!open) return;
    const t = window.setInterval(load, 4000);
    return () => window.clearInterval(t);
  }, [open, load]);
  useEffect(() => {
    load();
  }, [load]);
  const needs = (snap?.projects ?? []).filter((p) => p.latest && (p.latest.state === 'review' || p.latest.state === 'needs_you')).length;
  useEffect(() => setMenuBadge('projects', needs), [needs]);

  const current = snap?.projects.find((p) => p.name === view);
  const body = !snap ? (
    <div className="text-[12.5px] text-white/50">{offline ? "Orion's brain isn't running, so projects can't be made right now." : 'Loading…'}</div>
  ) : view === 'new' ? (
    <Wizard key={prefill?.name ?? 'blank'} snap={snap} prefill={prefill} onMade={(name, msg) => { setNote(msg); setPrefill(null); load(); setView(name); }} />
  ) : current ? (
    <Detail p={current} onNote={setNote} onChanged={load} />
  ) : (
    <div className="space-y-2">
      <button type="button" onClick={() => { setPrefill(null); setView('new'); setNote(''); }} className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left outline-none focus-visible:ring-1 focus-visible:ring-[#00E5FF]/70"
        style={{ background: 'rgba(124,92,255,0.18)', border: '1px solid rgba(176,156,255,0.45)' }}>
        <Plus size={18} className="text-[#C4B5FD]" />
        <span>
          <span className="block text-[13.5px] font-semibold text-[#D8DEFF]">Start a project with Claude</span>
          <span className="block text-[11.5px] text-white/55">A game, a website or a tool. Name it, pick a starter, say what you want.</span>
        </span>
      </button>
      {snap.projects.length === 0 && <div className="px-1 text-[12px] text-white/45">No projects yet. They live in {snap.root}.</div>}
      {snap.projects.map((p) => {
        const st = p.latest?.state;
        return (
          <button key={p.name} type="button" onClick={() => setView(p.name)} className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left hover:bg-white/[0.05] focus-visible:bg-white/[0.08] focus-visible:outline-none">
            <FolderGit2 size={15} className="shrink-0 text-white/60" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-semibold text-[#D8DEFF]">{p.name}</span>
              <span className="block truncate text-[11.5px] text-white/45">{p.idea || snap.starters.find((s) => s.id === p.starter)?.label}</span>
            </span>
            {st && <span className="shrink-0 text-[11px]" style={{ color: STATE_COLOR[st] }}>{STATE_LABEL[st]}</span>}
          </button>
        );
      })}
      <button type="button" className={`${quiet} mt-1`} onClick={() => openOrion('missions')}>
        <Rocket size={13} /> Open Mission Control (every agent job)
      </button>
    </div>
  );

  return (
    <AnimatePresence>
      {shown && (
        <GlassWindow
          key="projects"
          id="projects"
          title="Projects"
          subtitle={view === 'new' ? 'Start a project with Claude' : current ? current.name : 'Make something with Claude'}
          icon={<FolderGit2 size={14} className="text-[#9D85FF]" />}
          onClose={() => { setOpen(false); setView('list'); setNote(''); }}
          maximisable={false}
          mobile={mobile}
          className="top-16 w-[520px] max-w-[calc(100vw-32px)]"
          style={mobile ? undefined : { left: 'max(16px, calc(50% - 260px))' }}
          titleExtra={view !== 'list' ? (
            <button type="button" onClick={() => { setView('list'); setNote(''); }} className="flex items-center gap-1 rounded-md px-2 py-0.5 text-[11.5px] text-white/60 hover:bg-white/10 hover:text-white">
              <ArrowLeft size={12} /> All projects
            </button>
          ) : undefined}
        >
          <div className="max-h-[70vh] space-y-3 overflow-y-auto p-4 text-[#D8DEFF]">
            {note && <div role="status" className="rounded-lg bg-white/[0.05] px-3 py-1.5 text-[12px] text-white/75">{note}</div>}
            {body}
          </div>
        </GlassWindow>
      )}
    </AnimatePresence>
  );
}
