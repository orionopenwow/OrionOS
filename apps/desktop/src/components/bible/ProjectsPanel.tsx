/** Projects tab (Feature Bible D1/D3): workspaces, Battle Stations, multi-repo git radar, harvester, devlog. */
import { useEffect, useMemo } from 'react';
import type { GitRepoStatus, Workspace } from '@orion/shared-types';
import { FolderGit2, GitBranch, Radar, RefreshCw, Rocket, ScrollText, Sparkles, Sprout, Swords } from 'lucide-react';
import type { BibleSend, BibleState } from '../../hooks/bibleState';
import { Bar, Empty, PillButton, Section, timeAgo } from './ui';

interface Props {
  bible: BibleState;
  send: BibleSend;
  connected?: boolean;
}

export default function ProjectsPanel({ bible, send, connected }: Props) {
  useEffect(() => {
    if (connected) send('GIT_RADAR');
  }, [connected, send]);

  const custom = bible.stations.filter((s) => !s.auto);
  const ctx = bible.projectContext;

  return (
    <div className="space-y-3">
      {/* Battle Stations */}
      <Section
        title="Battle Stations"
        color="#FF2A85"
        icon={<Swords size={14} />}
        right={bible.activeStation ? <span className="text-[10px] font-mono text-[#FF2A85]">● {bible.activeStation}</span> : null}
      >
        {custom.length ? (
          <div className="flex flex-wrap gap-1.5">
            {custom.map((s) => (
              <PillButton
                key={s.name}
                color="#FF2A85"
                active={bible.activeStation === s.name}
                onClick={() => send('BATTLE_STATION', { name: s.name })}
              >
                <Rocket size={11} /> {s.name}
              </PillButton>
            ))}
          </div>
        ) : (
          <Empty>
            Say “switch to &lt;project&gt; mode” to open its IDE, start its dev servers and load its context. Add custom
            stations (close apps, launch tools, producer mode) in <code>workspace_manifest.json</code>.
          </Empty>
        )}
      </Section>

      <Constellation bible={bible} send={send} />

      {/* Active project context */}
      {ctx && (
        <Section title={`Context · ${ctx.workspace.name}`} color="#3DFFA8" icon={<FolderGit2 size={14} />}>
          {ctx.git && (
            <div className="text-[11px] font-mono text-white/70">
              <GitBranch size={11} className="inline mr-1" />
              {ctx.git.branch} · {ctx.git.dirty_files} uncommitted · last commit {timeAgo(ctx.git.last_commit_at)}
            </div>
          )}
          <div className="space-y-1.5 max-h-40 overflow-y-auto pr-1">
            {ctx.memories.slice(0, 5).map((m) => (
              <p key={m.id} className="text-[11px] text-white/75 line-clamp-2 bg-black/30 rounded-lg p-2">
                <span className="text-[#3DFFA8] font-mono mr-1">{m.source}</span>
                {m.content}
              </p>
            ))}
            {!ctx.memories.length && <Empty>No memories about this project yet.</Empty>}
          </div>
        </Section>
      )}

      {/* Workspaces */}
      <Section
        title={`Workspaces (${bible.workspaces.length})`}
        color="#3DFFA8"
        icon={<FolderGit2 size={14} />}
        right={
          <PillButton color="#3DFFA8" disabled={bible.harvest?.running} onClick={() => send('HARVEST_REQUEST')}>
            <Sprout size={11} /> {bible.harvest?.running ? 'Harvesting…' : 'Harvest'}
          </PillButton>
        }
      >
        {bible.harvest && (
          <div className="space-y-1">
            <div className="text-[10px] font-mono text-white/60 truncate">
              {bible.harvest.error ? `⚠ ${bible.harvest.error}` : bible.harvest.message}
              {!bible.harvest.running && bible.harvest.result && (
                <span className="text-[#3DFFA8]">
                  {' '}
                  · {Object.entries(bible.harvest.result)
                    .filter(([, v]) => typeof v === 'number')
                    .map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`)
                    .join(' · ')}
                </span>
              )}
            </div>
            {bible.harvest.running && <Bar value={bible.harvest.progress} color="#3DFFA8" />}
          </div>
        )}
        {bible.workspaces.length ? (
          <div className="space-y-1.5">
            {bible.workspaces.map((ws) => (
              <div
                key={ws.id}
                className="flex items-center justify-between gap-2 rounded-xl bg-black/35 border border-white/5 px-2.5 py-2"
              >
                <div className="min-w-0">
                  <div className={`text-xs font-semibold truncate ${ws.exists ? 'text-starlight' : 'text-white/35 line-through'}`}>
                    {ws.name}
                  </div>
                  <div className="text-[10px] font-mono text-white/40 truncate" title={ws.path}>
                    {ws.path}
                    {ws.ports.length ? ` · :${ws.ports.join(', :')}` : ''}
                  </div>
                </div>
                <PillButton
                  color="#3DFFA8"
                  disabled={!ws.exists}
                  title={ws.exists ? 'Open IDE, start servers, load context' : 'Folder not found on this PC'}
                  onClick={() => send('WORKSPACE_LAUNCH', { id: ws.id })}
                >
                  <Rocket size={11} /> Engage
                </PillButton>
              </div>
            ))}
          </div>
        ) : (
          <Empty>No workspaces yet. Hit Harvest to scan your projects folders, or edit workspace_manifest.json.</Empty>
        )}
      </Section>

      {/* Git radar */}
      <Section
        title="Multi-repo radar"
        color="#00E5FF"
        icon={<Radar size={14} />}
        right={
          <div className="flex gap-1.5">
            <PillButton onClick={() => send('GIT_RADAR')} title="Refresh">
              <RefreshCw size={11} />
            </PillButton>
            <PillButton color="#C084FC" onClick={() => send('DEVLOG_REQUEST')} title="Draft a devlog from recent commits">
              <ScrollText size={11} /> Devlog
            </PillButton>
          </div>
        }
      >
        {bible.radar.length ? (
          <div className="space-y-1.5">
            {bible.radar.map((r) => (
              <div key={r.id} className="rounded-xl bg-black/35 border border-white/5 px-2.5 py-2 space-y-1">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="font-semibold text-starlight truncate">{r.name}</span>
                  <span className="font-mono text-white/50 shrink-0">
                    {r.branch}
                    {r.dirty_files > 0 && <span className="text-[#FFB300]"> · {r.dirty_files}✎</span>}
                    {' · '}
                    {timeAgo(r.last_commit_at)}
                  </span>
                </div>
                {r.recent[0] && <div className="text-[10px] text-white/55 truncate">{r.recent[0].subject}</div>}
              </div>
            ))}
          </div>
        ) : (
          <Empty>Git repos in your workspaces show up here with branch, uncommitted files and last commit.</Empty>
        )}
      </Section>
    </div>
  );
}

// ------------------------------------------------------------------ constellation switcher
/** Each workspace is a star. Recent commits make it brighter; uncommitted work gives it an amber halo.
 *  Clicking a star makes it the active venture: Orion loads its context and memories. */
function hash(str: string) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) h = Math.imul(h ^ str.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967295;
}

function Constellation({ bible, send }: { bible: BibleState; send: BibleSend }) {
  const stars = useMemo(() => {
    const byId = new Map<string, GitRepoStatus>(bible.radar.map((r) => [r.id, r]));
    const list = bible.workspaces.filter((w) => w.exists);
    return list.map((w: Workspace, i) => {
      const r = byId.get(w.id);
      const ageDays = r?.last_commit_at ? (Date.now() / 1000 - r.last_commit_at) / 86400 : 90;
      const glow = Math.max(0.25, 1 - Math.min(1, ageDays / 30));
      // evenly around an ellipse, alternating inner/outer ring, with a small stable per-project jitter
      const n = Math.max(1, list.length);
      const a = (i / n) * Math.PI * 2 - Math.PI / 2 + (hash(w.id) - 0.5) * (Math.PI / n) * 0.6;
      const ring = n === 1 ? 0 : i % 2 ? 0.72 : 1;
      return {
        w,
        x: 50 + Math.cos(a) * 38 * ring,
        y: 46 + Math.sin(a) * 34 * ring,
        size: 6 + glow * 8,
        glow,
        dirty: (r?.dirty_files ?? 0) > 0,
      };
    });
  }, [bible.workspaces, bible.radar]);

  if (!stars.length) return null;
  const active = stars.find((s) => s.w.id === bible.activeVenture);

  return (
    <Section
      title="Constellation"
      color="#B388FF"
      icon={<Sparkles size={14} />}
      right={active ? <span className="text-[10px] font-mono text-[#B388FF]">★ {active.w.name}</span> : null}
    >
      <div className="relative h-44 rounded-xl bg-black/60 border border-white/5 overflow-hidden" role="list">
        <svg className="absolute inset-0 w-full h-full pointer-events-none" aria-hidden="true">
          {stars.slice(1).map((s, i) => (
            <line key={s.w.id} x1={`${stars[i].x}%`} y1={`${stars[i].y}%`} x2={`${s.x}%`} y2={`${s.y}%`}
              stroke="#B388FF" strokeOpacity="0.15" strokeWidth="1" />
          ))}
        </svg>
        {stars.map((s) => {
          const on = s.w.id === bible.activeVenture;
          return (
            <button
              key={s.w.id}
              type="button"
              role="listitem"
              onClick={(e) => {
                e.stopPropagation();
                send('ACTIVE_VENTURE', { id: s.w.id });
              }}
              title={`${s.w.name}${s.dirty ? ' · uncommitted work' : ''}`}
              aria-label={`Make ${s.w.name} the active project`}
              aria-pressed={on}
              className="group absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-1 bg-transparent border-0 p-1"
              style={{ left: `${s.x}%`, top: `${s.y}%` }}
            >
              <span
                className="block rounded-full transition-transform group-hover:scale-125"
                style={{
                  width: on ? s.size + 6 : s.size,
                  height: on ? s.size + 6 : s.size,
                  background: on ? '#fff' : `rgba(220,210,255,${0.45 + s.glow * 0.55})`,
                  boxShadow: `0 0 ${on ? 18 : 6 + s.glow * 10}px ${s.dirty ? '#FFB300' : on ? '#B388FF' : '#9fb0ff'}`,
                }}
              />
              <span className={`text-[9.5px] font-mono whitespace-nowrap ${on ? 'text-white' : 'text-white/45 group-hover:text-white/80'}`}>
                {s.w.name}
              </span>
            </button>
          );
        })}
      </div>
    </Section>
  );
}
