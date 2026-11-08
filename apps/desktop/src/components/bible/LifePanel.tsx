/** Life tab (Feature Bible D4–D7): hydration & wind-down, Runway / Exp Bar, ideas, producer, rules, vault. */
import { useEffect, useState } from 'react';
import { soundscape, type SoundscapeKind } from '../../lib/soundscape';
import {
  BookOpen,
  Droplets,
  Lightbulb,
  Mic,
  Moon,
  Music2,
  Sparkles,
  Sun,
  Plus,
  RefreshCw,
  Scale,
  Star,
  Timer,
  Trash2,
  Wallet,
} from 'lucide-react';
import type { RunwaySummary } from '@orion/shared-types';
import type { BibleSend, BibleState } from '../../hooks/bibleState';
import { Bar, Empty, PillButton, Section, fmtDuration, timeAgo } from './ui';

interface Props {
  bible: BibleState;
  send: BibleSend;
  connected?: boolean;
}

export default function LifePanel({ bible, send, connected }: Props) {
  useEffect(() => {
    if (!connected) return;
    send('RUNWAY_GET');
    send('IDEAS_LIST');
    send('RULES_LIST');
    send('VAULT_LIST');
  }, [connected, send]);

  return (
    <div className="space-y-3">
      <XpCard bible={bible} send={send} />
      <BodyCard bible={bible} send={send} />
      <SunMoonCard bible={bible} send={send} />
      <SoundscapeCard />
      <ProducerCard bible={bible} send={send} />
      <RunwayCard runway={bible.runway} send={send} />
      <IdeasCard bible={bible} />
      <RulesCard bible={bible} send={send} />
      <VaultCard bible={bible} send={send} />
    </div>
  );
}

// ------------------------------------------------------------------ body (hydration + circadian)
function BodyCard({ bible, send }: { bible: BibleState; send: BibleSend }) {
  const h = bible.hydration;
  const c = bible.circadian;
  const glasses = h ? Array.from({ length: Math.max(h.goal, h.today) }) : [];
  return (
    <Section
      title="Body"
      color="#26A5E4"
      icon={<Droplets size={14} />}
      right={
        <PillButton color="#26A5E4" onClick={() => send('HYDRATION_LOG', { delta: 1 })}>
          <Plus size={11} /> Glass
        </PillButton>
      }
    >
      {h && (
        <div className="flex items-center gap-1 flex-wrap">
          {glasses.map((_, i) => (
            <span
              key={i}
              className="w-4 h-5 rounded-b-md border transition-all"
              style={{
                borderColor: '#26A5E466',
                background: i < h.today ? 'linear-gradient(180deg,#26A5E455,#26A5E4)' : 'transparent',
                boxShadow: i < h.today ? '0 0 6px #26A5E4' : undefined,
              }}
            />
          ))}
          <span className="ml-2 text-[11px] font-mono text-white/60">
            {h.today}/{h.goal} today
          </span>
        </div>
      )}
      {c && (
        <div className="flex items-center gap-2 text-[11px] text-white/70">
          <Moon size={12} className={c.phase === 'normal' ? 'text-white/40' : 'text-[#FFB300]'} />
          {c.phase === 'normal' && <>Bedtime {c.bedtime} · wind-down starts {c.wind_down_starts}</>}
          {c.phase === 'wind_down' && <span className="text-[#FFB300]">Wind-down — screen warmed, bedtime {c.bedtime}</span>}
          {c.phase === 'night' && <span className="text-[#FFB300]">Past bedtime ({c.bedtime}). Orion is in night mode.</span>}
        </div>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------ XP / skins
const XP_LABEL: Record<string, string> = {
  focus_block: 'Focus block',
  recovery: 'Recovery routine',
  commit: 'Commits',
  idea: 'Idea captured',
  water: 'Water',
  approval: 'Queue cleared',
  devlog: 'Devlog',
};

function XpCard({ bible, send }: { bible: BibleState; send: BibleSend }) {
  const x = bible.xp;
  if (!x) return null;
  const span = Math.max(1, x.next_level - x.level_start);
  const frac = Math.min(1, (x.xp - x.level_start) / span);
  return (
    <Section
      title={`Architect · Level ${x.level}`}
      color="#B388FF"
      icon={<Sparkles size={14} />}
      right={<span className="text-[10px] font-mono text-[#B388FF]">+{x.today} XP today</span>}
    >
      <Bar value={frac} color="#B388FF" />
      <div className="mt-1 text-[10.5px] font-mono text-white/50">
        {x.xp} / {x.next_level} XP to level {x.level + 1}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {x.skins.map((s) => (
          <PillButton
            key={s.id}
            color="#B388FF"
            active={x.skin === s.id}
            disabled={!s.unlocked}
            title={s.unlocked ? `Use the ${s.name} core` : `Unlocks at level ${s.level}`}
            onClick={() => send('XP_SKIN', { skin: s.id })}
          >
            {s.unlocked ? s.name : `🔒 ${s.name} · L${s.level}`}
          </PillButton>
        ))}
      </div>
      {x.recent.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {x.recent.slice(0, 4).map((r) => (
            <div key={r.at} className="flex justify-between text-[11px] text-white/60">
              <span>{XP_LABEL[r.kind] ?? r.kind}{r.note ? ` · ${r.note}` : ''}</span>
              <span className="font-mono text-[#B388FF]">+{r.xp}</span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------ Sun & Moon (location)
function SunMoonCard({ bible, send }: { bible: BibleState; send: BibleSend }) {
  const loc = bible.location;
  const sun = bible.circadian?.sun;
  const [lat, setLat] = useState(loc ? String(loc.lat) : '');
  const [lon, setLon] = useState(loc ? String(loc.lon) : '');
  const [msg, setMsg] = useState('');
  useEffect(() => {
    if (loc) {
      setLat(String(loc.lat));
      setLon(String(loc.lon));
    }
  }, [loc]);

  const save = (la: number, lo: number, label = '') => {
    if (!Number.isFinite(la) || !Number.isFinite(lo) || Math.abs(la) > 90 || Math.abs(lo) > 180) {
      setMsg('Latitude must be −90…90 and longitude −180…180.');
      return;
    }
    send('LOCATION_SET', { lat: la, lon: lo, label });
    setMsg('Saved — Orion now follows your sunset.');
  };
  const detect = () => {
    if (!navigator.geolocation) return setMsg('This device can’t share its location — type it in instead.');
    setMsg('Asking for your location…');
    navigator.geolocation.getCurrentPosition(
      (p) => save(Math.round(p.coords.latitude * 100) / 100, Math.round(p.coords.longitude * 100) / 100),
      () => setMsg('Location wasn’t shared — type it in instead.'),
      { timeout: 10000 },
    );
  };

  return (
    <Section title="Sun & Moon" color="#FFB300" icon={<Sun size={14} />}>
      <div className="text-[11px] text-white/70">
        {sun ? (
          <>
            {sun.is_day ? 'Sun is up' : 'Sun is down'} · sunset {sun.sunset ?? '—'} · sunrise {sun.sunrise ?? '—'}
            <div className="text-white/45 mt-0.5">After sunset the HUD warms, the voice softens and work alerts wait until morning.</div>
          </>
        ) : (
          <>Set your location so Orion can follow the real sunset (it stays on this PC).</>
        )}
      </div>
      <div className="mt-2 flex gap-1.5 items-center">
        <input value={lat} onChange={(e) => setLat(e.target.value)} placeholder="Latitude" aria-label="Latitude"
          className="w-24 rounded-lg bg-white/5 border border-white/10 px-2 py-1 text-[11px] text-white outline-none focus:border-[#FFB300]/60" />
        <input value={lon} onChange={(e) => setLon(e.target.value)} placeholder="Longitude" aria-label="Longitude"
          className="w-24 rounded-lg bg-white/5 border border-white/10 px-2 py-1 text-[11px] text-white outline-none focus:border-[#FFB300]/60" />
        <PillButton color="#FFB300" onClick={() => save(parseFloat(lat), parseFloat(lon))}>Save</PillButton>
        <PillButton color="#FFB300" onClick={detect}>Detect</PillButton>
      </div>
      {msg && <div className="mt-1 text-[10.5px] text-white/50">{msg}</div>}
    </Section>
  );
}

// ------------------------------------------------------------------ soundscape
function SoundscapeCard() {
  const [kind, setKind] = useState<SoundscapeKind>(soundscape.kind);
  const [vol, setVol] = useState(soundscape.volume);
  const options: Array<[SoundscapeKind, string]> = [
    ['off', 'Off'],
    ['drone', 'Cosmic drone'],
    ['rain', 'Rain'],
    ['campfire', 'Campfire'],
  ];
  return (
    <Section title="Soundscape" color="#7DF0FF" icon={<Music2 size={14} />}>
      <div className="flex flex-wrap gap-1.5">
        {options.map(([k, label]) => (
          <PillButton key={k} color="#7DF0FF" active={kind === k} onClick={() => { soundscape.play(k); setKind(k); }}>
            {label}
          </PillButton>
        ))}
      </div>
      <label className="mt-2 flex items-center gap-2 text-[11px] text-white/60">
        Volume
        <input type="range" min={0} max={1} step={0.05} value={vol} aria-label="Soundscape volume"
          onChange={(e) => { const v = parseFloat(e.target.value); soundscape.setVolume(v); setVol(v); }}
          className="flex-1 accent-[#7DF0FF]" />
      </label>
      <div className="text-[10.5px] text-white/40 mt-1">Quiets itself while Orion talks and follows the core's mode.</div>
    </Section>
  );
}

// ------------------------------------------------------------------ producer
function ProducerCard({ bible, send }: { bible: BibleState; send: BibleSend }) {
  const p = bible.producer;
  const [topic, setTopic] = useState('');
  return (
    <Section
      title="Podcast producer"
      color="#FF2A85"
      icon={<Mic size={14} />}
      right={
        p ? (
          <PillButton color="#FF5C8A" onClick={() => send('PRODUCER', { action: 'stop' })}>
            End & save notes
          </PillButton>
        ) : (
          <PillButton color="#FF2A85" onClick={() => send('PRODUCER', { action: 'start' })}>
            <Mic size={11} /> Start session
          </PillButton>
        )
      }
    >
      {p ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-starlight font-semibold truncate">{p.title}</span>
            <span className="font-mono text-[#FF2A85]">
              <Timer size={11} className="inline mr-1" />
              {fmtDuration(p.elapsed)}
            </span>
          </div>
          {p.topics.length > 0 && (
            <div className="text-[11px] text-white/70">
              Topic: <b className="text-white">{p.topics[p.topics.length - 1].title}</b> ·{' '}
              {fmtDuration(p.elapsed - p.topics[p.topics.length - 1].at)}
            </div>
          )}
          <div className="flex gap-1.5">
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && topic.trim()) {
                  send('PRODUCER', { action: 'topic', title: topic.trim() });
                  setTopic('');
                }
              }}
              placeholder="Next topic…"
              className="flex-1 rounded-lg bg-black/40 border border-white/10 px-2 py-1 text-[11px] text-starlight outline-none select-text"
            />
            <PillButton color="#FFB300" onClick={() => send('PRODUCER', { action: 'highlight' })} title="Say “mark that”">
              <Star size={11} /> Mark ({p.highlights.length})
            </PillButton>
          </div>
        </div>
      ) : (
        <Empty>
          “Start podcast mode” tracks topics, shows silent fact cards from your docs while you talk, and saves highlight
          timestamps when you say “mark that”.
          {bible.producerNotesPath && <span className="block mt-1 text-[#3DFFA8]">Last notes: {bible.producerNotesPath}</span>}
        </Empty>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------ runway / Exp bar
type Row = { name: string; amount: number; due_day?: number };

function RunwayCard({ runway, send }: { runway: RunwaySummary | null; send: BibleSend }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<{ balances: Row[]; monthly_costs: Row[]; monthly_revenue: Row[]; slug: string } | null>(null);
  const cur = (runway as any)?.currency || 'USD';
  const money = (n: number | null | undefined) =>
    n === null || n === undefined ? '—' : n.toLocaleString(undefined, { style: 'currency', currency: cur, maximumFractionDigits: 0 });

  const startEdit = () => {
    setDraft({
      balances: runway?.balances ?? [],
      monthly_costs: runway?.monthly_costs ?? [],
      monthly_revenue: runway?.monthly_revenue ?? [],
      slug: runway?.opencollective_slug ?? '',
    });
    setEditing(true);
  };
  const save = () => {
    if (!draft) return;
    send('RUNWAY_SAVE', {
      runway: {
        currency: cur,
        balances: draft.balances,
        monthly_costs: draft.monthly_costs,
        monthly_revenue: draft.monthly_revenue,
        milestones: runway?.milestones ?? [],
        opencollective_slug: draft.slug,
      },
    });
    setEditing(false);
  };

  const covered = runway && runway.monthly_costs_total ? runway.monthly_revenue_total / runway.monthly_costs_total : 0;

  return (
    <Section
      title="Runway · Exp bar"
      color="#3DFFA8"
      icon={<Wallet size={14} />}
      right={
        editing ? (
          <div className="flex gap-1.5">
            <PillButton color="#8F90A6" onClick={() => setEditing(false)}>Cancel</PillButton>
            <PillButton color="#3DFFA8" onClick={save}>Save</PillButton>
          </div>
        ) : (
          <PillButton color="#3DFFA8" onClick={startEdit}>Edit</PillButton>
        )
      }
    >
      {!editing && runway && (
        <div className="space-y-2.5">
          <div className="grid grid-cols-3 gap-2 text-center">
            <Stat label="Balance" value={money(runway.total_balance)} />
            <Stat label="Net / mo" value={money(runway.monthly_net)} color={runway.monthly_net >= 0 ? '#3DFFA8' : '#FF5C8A'} />
            <Stat
              label="Runway"
              value={runway.runway_months === null ? '∞' : `${runway.runway_months} mo`}
              color={runway.runway_months !== null && runway.runway_months < 3 ? '#FF5C8A' : '#3DFFA8'}
            />
          </div>
          <div className="space-y-1">
            <div className="flex justify-between text-[10px] font-mono text-white/55">
              <span>Lv · costs covered by supporters</span>
              <span>{Math.round(covered * 100)}%</span>
            </div>
            <Bar value={covered} color="#3DFFA8" />
          </div>
          {runway.milestone_progress.map((m) => (
            <div key={m.name} className="space-y-1">
              <div className="flex justify-between text-[10px] font-mono text-white/55">
                <span className="truncate">{m.name}</span>
                <span>{Math.round(m.progress * 100)}%</span>
              </div>
              <Bar value={m.progress} color="#C084FC" />
            </div>
          ))}
          {runway.monthly_costs.length > 0 && (
            <div className="text-[10px] text-white/50">
              Bills:{' '}
              {runway.monthly_costs
                .map((c) => `${c.name} ${money(c.amount)}${c.due_day ? ` (day ${c.due_day})` : ''}`)
                .join(' · ')}
            </div>
          )}
        </div>
      )}
      {!editing && !runway && <Empty>Loading…</Empty>}
      {editing && draft && (
        <div className="space-y-2">
          {(['balances', 'monthly_costs', 'monthly_revenue'] as const).map((key) => (
            <RowEditor
              key={key}
              label={key === 'balances' ? 'Balances' : key === 'monthly_costs' ? 'Monthly bills' : 'Monthly revenue (Ko-fi, Patreon…)'}
              rows={draft[key]}
              withDay={key === 'monthly_costs'}
              onChange={(rows) => setDraft({ ...draft, [key]: rows })}
            />
          ))}
          <label className="block text-[10px] font-mono text-white/55">
            Open Collective slug (public balance, optional)
            <input
              value={draft.slug}
              onChange={(e) => setDraft({ ...draft, slug: e.target.value })}
              className="mt-1 w-full rounded-lg bg-black/40 border border-white/10 px-2 py-1 text-[11px] text-starlight outline-none select-text"
            />
          </label>
          <p className="text-[10px] text-white/40">Stored only on this PC. No bank logins — just the numbers you type.</p>
        </div>
      )}
    </Section>
  );
}

function RowEditor({
  label,
  rows,
  withDay,
  onChange,
}: {
  label: string;
  rows: Row[];
  withDay?: boolean;
  onChange: (rows: Row[]) => void;
}) {
  const set = (i: number, patch: Partial<Row>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px] font-mono text-white/55">
        <span>{label}</span>
        <button type="button" className="text-[#3DFFA8]" onClick={() => onChange([...rows, { name: '', amount: 0 }])}>
          + add
        </button>
      </div>
      {rows.map((r, i) => (
        <div key={i} className="flex gap-1">
          <input
            value={r.name}
            placeholder="name"
            onChange={(e) => set(i, { name: e.target.value })}
            className="flex-1 min-w-0 rounded bg-black/40 border border-white/10 px-1.5 py-0.5 text-[11px] text-starlight outline-none select-text"
          />
          <input
            type="number"
            value={r.amount}
            onChange={(e) => set(i, { amount: Number(e.target.value) })}
            className="w-20 rounded bg-black/40 border border-white/10 px-1.5 py-0.5 text-[11px] text-starlight outline-none select-text"
          />
          {withDay && (
            <input
              type="number"
              min={1}
              max={31}
              value={r.due_day ?? ''}
              placeholder="day"
              onChange={(e) => set(i, { due_day: e.target.value ? Number(e.target.value) : undefined })}
              className="w-12 rounded bg-black/40 border border-white/10 px-1.5 py-0.5 text-[11px] text-starlight outline-none select-text"
            />
          )}
          <button type="button" onClick={() => onChange(rows.filter((_, j) => j !== i))} className="text-white/40 hover:text-[#FF5C8A]">
            <Trash2 size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}

function Stat({ label, value, color = '#FFFFFF' }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-xl bg-black/35 border border-white/5 py-1.5">
      <div className="text-[13px] font-bold" style={{ color }}>
        {value}
      </div>
      <div className="text-[9px] font-mono uppercase text-white/45">{label}</div>
    </div>
  );
}

// ------------------------------------------------------------------ ideas
function IdeasCard({ bible }: { bible: BibleState }) {
  const ideas = [...bible.ideas].reverse().slice(0, 8);
  return (
    <Section title={`Idea catcher (${bible.ideas.length})`} color="#FFB300" icon={<Lightbulb size={14} />}>
      {ideas.length ? (
        <div className="space-y-1.5">
          {ideas.map((i) => (
            <div key={i.id} className="rounded-lg bg-black/35 px-2 py-1.5 text-[11px]">
              <span className="font-mono text-[10px] text-[#FFB300] mr-1.5">
                {i.kind} · {i.project}
              </span>
              <span className="text-white/80">{i.text}</span>
              <span className="text-white/30 ml-1.5">{timeAgo(i.at)}</span>
            </div>
          ))}
        </div>
      ) : (
        <Empty>Say “log a realm idea: …” or “new lore idea for OpenWoW: …” — it lands in that project's IDEAS.md.</Empty>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------ rules (two-speed training, speed 1)
function RulesCard({ bible, send }: { bible: BibleState; send: BibleSend }) {
  const [text, setText] = useState('');
  const add = () => {
    if (text.trim().length < 4) return;
    send('RULE_ADD', { text: text.trim() });
    setText('');
  };
  return (
    <Section title={`Standing rules (${bible.rules.length})`} color="#C084FC" icon={<Scale size={14} />}>
      <div className="flex gap-1.5">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && add()}
          placeholder="e.g. Keep Discord announcements casual, no corporate tone"
          className="flex-1 rounded-lg bg-black/40 border border-white/10 px-2 py-1 text-[11px] text-starlight outline-none select-text"
        />
        <PillButton color="#C084FC" onClick={add}>
          <Plus size={11} />
        </PillButton>
      </div>
      <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
        {bible.rules.map((r) => (
          <div key={r.id} className="flex items-start gap-2 rounded-lg bg-black/35 px-2 py-1.5 text-[11px]">
            <span
              className="text-[9px] font-mono uppercase px-1 rounded shrink-0 mt-px"
              style={{ color: r.tier === 0 ? '#3DFFA8' : '#C084FC', background: r.tier === 0 ? '#3DFFA81f' : '#C084FC1f' }}
              title={r.tier === 0 ? 'From identity_and_rules.md — edit the file to change' : 'Learned from you'}
            >
              {r.tier === 0 ? 'core' : 'learned'}
            </span>
            <span className="flex-1 text-white/80">{r.text}</span>
            {r.tier !== 0 && (
              <button type="button" onClick={() => send('RULE_DELETE', { id: r.id })} className="text-white/35 hover:text-[#FF5C8A]">
                <Trash2 size={11} />
              </button>
            )}
          </div>
        ))}
        {!bible.rules.length && <Empty>Rules you state (“from now on…”) are injected into every prompt.</Empty>}
      </div>
    </Section>
  );
}

// ------------------------------------------------------------------ offline knowledge vault
function VaultCard({ bible, send }: { bible: BibleState; send: BibleSend }) {
  const v = bible.vault;
  return (
    <Section
      title={`Knowledge vault (${v?.files.length ?? 0})`}
      color="#00E5FF"
      icon={<BookOpen size={14} />}
      right={
        <PillButton onClick={() => send('VAULT_SYNC')} title="Index new or changed files">
          <RefreshCw size={11} /> Sync
        </PillButton>
      }
    >
      <Empty>
        Drop manuals, wikis and PDFs into <code className="text-white/70 select-text">{v?.dir ?? '~/.orion/vault'}</code> —
        Orion answers from them offline.
      </Empty>
      {v && v.files.length > 0 && (
        <div className="space-y-0.5 max-h-32 overflow-y-auto pr-1">
          {v.files.map((f) => (
            <div key={f.name} className="flex justify-between text-[10px] font-mono text-white/60">
              <span className="truncate">{f.name}</span>
              <span className={f.indexed ? 'text-[#3DFFA8]' : 'text-white/35'}>{f.indexed ? 'indexed' : 'pending'}</span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
