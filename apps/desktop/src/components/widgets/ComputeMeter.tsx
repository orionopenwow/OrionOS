/**
 * Compute meter (the "gas meter"): what Orion's model calls cost today, in tokens and estimated dollars.
 * A small pill; click it for the per-model breakdown, this session vs today, and tips to spend less.
 */
import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { tokens, usd, useDaemonPoll, type ComputePeriod, type ComputeSnapshot } from '../../lib/swarmCompute';
import '../../styles/glass.css';

function Rows({ period }: { period: ComputePeriod }) {
  if (!period.models.length) return <div className="text-[12px] text-white/45 py-1">No model calls yet.</div>;
  return (
    <table className="w-full text-[11.5px]">
      <thead>
        <tr className="text-white/40 text-[10px] uppercase tracking-wider">
          <th className="text-left font-semibold pb-1">Model</th>
          <th className="text-right font-semibold pb-1">Calls</th>
          <th className="text-right font-semibold pb-1">In / out</th>
          <th className="text-right font-semibold pb-1">Cost</th>
        </tr>
      </thead>
      <tbody>
        {period.models.map((m) => (
          <tr key={`${m.provider}|${m.model}`} className="border-t border-white/5">
            <td className="py-1 pr-2 max-w-[130px] truncate text-white/85" title={`${m.model} (${m.provider})`}>
              {m.model}
              {m.local && <span className="ml-1 text-[9.5px] text-emerald-300/80">local</span>}
            </td>
            <td className="py-1 text-right text-white/60">{m.calls}</td>
            <td className="py-1 text-right text-white/60">
              {tokens(m.tokens_in)} / {tokens(m.tokens_out)}
            </td>
            <td
              className="py-1 text-right font-semibold text-white"
              title={
                m.local
                  ? 'Runs on this PC: free'
                  : `About $${m.price_in} in / $${m.price_out} out per million tokens${m.price_known ? '' : ' (guess: no price for this model)'}`
              }
            >
              {m.local ? 'free' : usd(m.cost)}
              {!m.local && !m.price_known && <span className="text-amber-300">*</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ComputeMeter() {
  const [snap, reload] = useDaemonPoll<ComputeSnapshot>('/compute', 5000);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'today' | 'session'>('today');
  if (!snap) return null;
  const today = snap.today;
  const period = tab === 'today' ? snap.today : snap.session;
  const maxDay = Math.max(0.0001, ...snap.history.map((h) => h.cost));

  return (
    <div className="relative select-none" onClick={(e) => e.stopPropagation()}>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.97 }}
            className="orion-glass orion-glass--dense absolute bottom-full right-0 mb-2 w-[min(340px,calc(100vw-32px))] p-3.5 text-white"
            role="dialog"
            aria-label="Compute meter"
          >
            <div className="flex items-center justify-between">
              <div className="text-[13px] font-bold">Model spend</div>
              <div className="flex rounded-lg bg-white/5 p-0.5 text-[11px]">
                {(['today', 'session'] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setTab(t)}
                    className={`px-2 py-0.5 rounded-md ${tab === t ? 'bg-violet-400/25 text-white' : 'text-white/55 hover:text-white'}`}
                  >
                    {t === 'today' ? 'Today' : 'This session'}
                  </button>
                ))}
              </div>
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-[22px] font-bold text-cyan-100">{usd(period.cost)}</span>
              <span className="text-[11.5px] text-white/50">
                {tokens(period.tokens_in + period.tokens_out)} tokens · {period.calls} calls
              </span>
            </div>
            {snap.history.length > 1 && (
              <div className="mt-1.5 flex items-end gap-1 h-8" aria-label="Last days">
                {snap.history.map((h) => (
                  <div
                    key={h.date}
                    className="flex-1 rounded-sm bg-gradient-to-t from-violet-500/60 to-cyan-300/70"
                    style={{ height: `${Math.max(6, (h.cost / maxDay) * 100)}%`, opacity: h.date === today.date ? 1 : 0.55 }}
                    title={`${h.date}: ${usd(h.cost)}`}
                  />
                ))}
              </div>
            )}
            <div className="mt-2">
              <Rows period={period} />
            </div>
            {snap.tips.length > 0 && (
              <div className="mt-2.5 space-y-1">
                <div className="text-[10.5px] font-bold uppercase tracking-wider text-white/40">Spend less</div>
                {snap.tips.map((t) => (
                  <div key={t} className="text-[11.5px] leading-snug text-white/80 rounded-lg bg-cyan-300/[0.06] border border-cyan-300/10 px-2 py-1">
                    {t}
                  </div>
                ))}
              </div>
            )}
            <div className="mt-2 text-[10px] text-white/35">{snap.note}</div>
          </motion.div>
        )}
      </AnimatePresence>
      <button
        type="button"
        onClick={() => {
          setOpen((o) => !o);
          void reload();
        }}
        className="flex items-center gap-2 rounded-full border border-violet-300/25 bg-black/55 backdrop-blur-md px-3 py-1.5 text-white shadow-[0_6px_20px_rgba(0,0,0,0.45)] hover:border-cyan-300/40"
        title="Model spend today (estimate). Click for details."
        aria-expanded={open}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="#7df0ff" strokeWidth="2" strokeLinecap="round">
          <path d="M4 18a8 8 0 1 1 16 0" />
          <path d="M12 18l4-6" />
        </svg>
        <span className="text-[12px] font-bold">{usd(today.cost)}</span>
        <span className="text-[11px] text-white/50">{tokens(today.tokens_in + today.tokens_out)} tok today</span>
      </button>
    </div>
  );
}
