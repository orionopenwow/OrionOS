/**
 * Self-changes history (menu: More > Self-changes history): every change Orion built for itself, newest first,
 * with what happened to it, plus Undo while the latest swap is still on its 2-minute watch.
 * The ghost preview (GhostPreview.tsx) still pops up by itself when a new change is ready.
 */
import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import { History, RotateCcw } from 'lucide-react';
import GlassWindow, { useWindowShown } from '../glass/GlassWindow';
import { onOrionOpen } from '../../lib/menu';
import { windowManager } from '../../lib/windowManager';
import { selfprog, type SelfChange, type SelfState } from './selfprogApi';

const WORDS: Record<SelfChange['status'], { text: string; color: string }> = {
  building: { text: 'Building and testing', color: '#7FF2FF' },
  ready: { text: 'Ready for your OK', color: '#FFC66B' },
  failed: { text: 'Failed its checks', color: '#FF5C8A' },
  applied: { text: 'In use', color: '#5CFFB0' },
  rejected: { text: 'You said no', color: '#8A90B8' },
  rolled_back: { text: 'Put back', color: '#8A90B8' },
};

function Body() {
  const [state, setState] = useState<SelfState | null>(null);
  const [note, setNote] = useState('');
  const load = useCallback(() => {
    selfprog.state().then(setState, () => setNote("Orion's brain isn't answering."));
  }, []);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [load]);
  const changes = state?.changes ?? [];
  return (
    <div className="max-h-[60vh] space-y-2 overflow-y-auto p-3 text-[#D8DEFF]">
      {state?.probation && (
        <div className="flex items-center gap-2 rounded-xl px-3 py-2" style={{ background: 'rgba(124,92,255,0.14)' }}>
          <span className="flex-1 text-[12.5px]">The newest change is on its 2-minute watch.</span>
          <button
            type="button"
            className="emboss-pill flex items-center gap-1 rounded-full px-3 py-1 text-[12px]"
            onClick={() => selfprog.undo().then((r) => { setNote(r.message); load(); }, (e: Error) => setNote(e.message))}
          >
            <RotateCcw size={12} /> Undo
          </button>
        </div>
      )}
      {note && <div role="status" className="px-1 text-[12px] text-[#FFC66B]">{note}</div>}
      {state && changes.length === 0 && <div className="px-1 py-3 text-[12.5px] text-white/50">Orion hasn't changed itself yet.</div>}
      {changes.map((c) => {
        const w = WORDS[c.status] ?? { text: c.status, color: '#8A90B8' };
        return (
          <div key={c.id} className="rounded-xl px-3 py-2 hover:bg-white/[0.04]">
            <div className="flex items-center gap-2 text-[12.5px] font-semibold">
              <span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: w.color }} />
              <span className="min-w-0 flex-1 truncate">{c.summary || c.request}</span>
              <span className="shrink-0 text-[11px] font-normal" style={{ color: w.color }}>{w.text}</span>
            </div>
            {c.reason && <div className="mt-0.5 text-[11.5px] text-white/50">{c.reason}</div>}
            {c.kind === 'protected_core' && <div className="text-[11px] text-white/40">Protected core: needs your phone.</div>}
          </div>
        );
      })}
    </div>
  );
}

export default function SelfHistory({ mobile }: { mobile?: boolean }) {
  const [open, setOpen] = useState(false);
  const shown = useWindowShown('selfchanges', open);
  useEffect(() => onOrionOpen('selfchanges', () => { windowManager.restore('selfchanges'); setOpen(true); }), []);
  return (
    <AnimatePresence>
      {shown && (
        <GlassWindow
          key="selfchanges"
          id="selfchanges"
          title="Self-changes"
          subtitle="What Orion changed in itself"
          icon={<History size={14} className="text-[#C4B5FD]" />}
          onClose={() => setOpen(false)}
          maximisable={false}
          mobile={mobile}
          className="right-4 bottom-[72px] w-[440px] max-w-[calc(100vw-32px)]"
        >
          <Body />
        </GlassWindow>
      )}
    </AnimatePresence>
  );
}
