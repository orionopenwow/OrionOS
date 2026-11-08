/** Tools tab (Feature Bible D1): Living Grimoire search/run, self-healing run console, macro recorder. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { BookOpen, CircleDot, Play, Search, Square, Terminal, Wand2, Wrench } from 'lucide-react';
import type { RunInfo } from '@orion/shared-types';
import type { BibleSend, BibleState } from '../../hooks/bibleState';
import { Empty, PillButton, Section, timeAgo } from './ui';

interface Props {
  bible: BibleState;
  send: BibleSend;
}

const KIND_COLOR: Record<string, string> = {
  shell: '#00E5FF',
  workflow: '#7FA6FF',
  internal: '#3DFFA8',
  snippet: '#FFB300',
  userscript: '#FF2A85',
};

export default function GrimoirePanel({ bible, send }: Props) {
  const [query, setQuery] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    clearTimeout(timer.current);
    if (query.trim().length < 2) return;
    timer.current = setTimeout(() => send('GRIMOIRE_SEARCH', { query: query.trim() }), 250);
    return () => clearTimeout(timer.current);
  }, [query, send]);

  const entries = useMemo(() => {
    if (query.trim().length >= 2 && bible.grimoireResults?.query === query.trim()) return bible.grimoireResults.results;
    return bible.grimoire;
  }, [query, bible.grimoire, bible.grimoireResults]);

  return (
    <div className="space-y-3">
      <Section
        title={`Scripts (${bible.grimoire.length})`}
        color="#7FA6FF"
        icon={<BookOpen size={14} />}
        right={
          bible.recording ? (
            <PillButton color="#FF5C8A" active onClick={() => send('MACRO_STOP')}>
              <Square size={11} /> Stop watching
            </PillButton>
          ) : (
            <PillButton color="#FF5C8A" onClick={() => send('MACRO_START')} title="Record your next actions as a macro">
              <CircleDot size={11} /> Watch & learn
            </PillButton>
          )
        }
      >
        {bible.recordingMessage && !bible.recording && (
          <div className="text-[11px] text-[#FFB86B]">{bible.recordingMessage}</div>
        )}
        <label className="flex items-center gap-2 rounded-xl bg-black/40 border border-white/10 px-2.5 py-1.5">
          <Search size={13} className="text-white/72" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search scripts, macros & snippets… (or just say “run …”)"
            className="flex-1 bg-transparent text-xs text-starlight placeholder:text-white/55 outline-none select-text"
          />
        </label>
        <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
          {entries.map((e) => (
            <div key={e.id} className="flex items-center gap-2 rounded-xl bg-black/35 border border-white/5 px-2.5 py-2">
              <span
                className="text-[10.5px] font-mono uppercase px-1.5 py-0.5 rounded shrink-0"
                style={{ color: KIND_COLOR[e.kind] ?? '#fff', background: `${KIND_COLOR[e.kind] ?? '#fff'}1f` }}
              >
                {e.kind}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[11.5px] text-starlight truncate">{e.description}</div>
                <div className="text-[11px] font-mono text-white/72 truncate">
                  {e.voice_triggers?.[0] ? `“${e.voice_triggers[0]}”` : e.command ?? e.path ?? ''}
                </div>
              </div>
              <PillButton
                color={KIND_COLOR[e.kind] ?? '#00E5FF'}
                title={e.requires_approval ? 'Asks for approval first' : 'Run'}
                onClick={() => send('GRIMOIRE_RUN', { id: e.id })}
              >
                <Play size={11} /> {e.requires_approval ? 'Run…' : 'Run'}
              </PillButton>
            </div>
          ))}
          {!entries.length && (
            <Empty>
              Nothing matches. Drop .bat / .ps1 / .py / .js / .user.js files into the scripts folder (~/.orion/grimoire), or say “watch what I
              do next” to record a macro.
            </Empty>
          )}
        </div>
      </Section>

      <Section
        title="Run console"
        color="#00E5FF"
        icon={<Terminal size={14} />}
        right={<span className="text-[11px] font-mono text-white/74">{bible.runs.filter((r) => r.running).length} running</span>}
      >
        {bible.runs.length ? (
          <div className="space-y-2">
            {bible.runs.slice(0, 6).map((r) => (
              <RunCard key={r.id} run={r} send={send} />
            ))}
          </div>
        ) : (
          <Empty>Scripts, dev servers and Battle Station services stream here. Failures get a one-click fix.</Empty>
        )}
      </Section>
    </div>
  );
}

function RunCard({ run, send }: { run: RunInfo; send: BibleSend }) {
  const [open, setOpen] = useState(run.running || (run.exit_code ?? 0) !== 0);
  const failed = !run.running && run.exit_code !== 0 && run.exit_code !== null && run.exit_code !== undefined;
  const color = run.running ? '#00E5FF' : failed ? '#FF5C8A' : '#3DFFA8';
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [run.tail.length, open]);

  return (
    <div className="rounded-xl bg-black/40 border border-white/5 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between gap-2 px-2.5 py-2 text-left"
      >
        <div className="min-w-0 flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full shrink-0 ${run.running ? 'animate-pulse' : ''}`} style={{ background: color, boxShadow: `0 0 6px ${color}` }} />
          <span className="text-[11.5px] text-starlight truncate">{run.label || run.command}</span>
          {run.kind === 'service' && <span className="text-[10.5px] font-mono text-white/72">service</span>}
        </div>
        <span className="text-[11px] font-mono shrink-0" style={{ color }}>
          {run.running ? timeAgo(run.started_at) : failed ? `exit ${run.exit_code}` : 'done'}
          {run.restarts ? ` · ↻${run.restarts}` : ''}
        </span>
      </button>
      {open && (
        <div className="px-2.5 pb-2.5 space-y-2">
          <pre
            ref={ref}
            className="max-h-40 overflow-y-auto rounded-lg bg-black/60 p-2 text-[11px] leading-snug font-mono text-white/85 whitespace-pre-wrap break-all select-text"
          >
            {run.tail.join('\n') || run.command}
          </pre>
          {failed && run.diagnosis && (
            <div className="rounded-lg border border-[#FF5C8A]/30 bg-[#FF5C8A]/10 p-2 text-[11.5px] text-white/85 space-y-1">
              <div className="flex items-center gap-1.5 text-[#FF5C8A] font-semibold">
                <Wand2 size={12} /> Self-healing diagnosis
              </div>
              <p>{run.diagnosis.diagnosis}</p>
              {run.diagnosis.fix_command && (
                <code className="block text-[11px] font-mono text-[#3DFFA8] break-all">{run.diagnosis.fix_command}</code>
              )}
            </div>
          )}
          <div className="flex gap-1.5">
            {run.running ? (
              <PillButton color="#FF5C8A" onClick={() => send('RUN_STOP', { run_id: run.id })}>
                <Square size={11} /> Stop
              </PillButton>
            ) : (
              failed && (
                <PillButton
                  color="#3DFFA8"
                  disabled={!run.diagnosis?.fix_command}
                  title={run.diagnosis?.fix_command ? 'Apply the fix and restart' : 'No safe automatic fix found'}
                  onClick={() => send('RUN_FIX', { run_id: run.id })}
                >
                  <Wrench size={11} /> Fix & Restart
                </PillButton>
              )
            )}
          </div>
        </div>
      )}
    </div>
  );
}
