import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, Circle, Sparkles, RefreshCw, Plus, Trash2, Dumbbell, Flower2, Wind, GlassWater, AlarmClockOff } from 'lucide-react';
import type { PacingCheckin, RoutineKind } from '@orion/shared-types';
import { pacingColors } from '../theme/colors';

interface RoutineItem {
  id: string;
  text: string;
  completed: boolean;
}

interface Props {
  routines?: string[];
  onRequestRoutines: () => void;
  connected: boolean;
  /** V2 pacing: present when the 90-minute focus arc fires a wellness check-in. */
  checkin?: PacingCheckin | null;
  onStartBreak?: (kind: RoutineKind) => void;
  onSnooze?: () => void;
  /** Only render the wellness check-in (floating card mode). */
  compact?: boolean;
  snoozeMinutes?: number;
}

const ROUTINE_ICON: Record<RoutineKind, typeof Dumbbell> = {
  calisthenics: Dumbbell,
  yoga: Flower2,
  breathwork: Wind,
  hydration: GlassWater,
};

export default function DailyRoutines({
  routines = [],
  onRequestRoutines,
  connected,
  checkin,
  onStartBreak,
  onSnooze,
  compact = false,
  snoozeMinutes = 15,
}: Props) {
  const [items, setItems] = useState<RoutineItem[]>([
    { id: '1', text: 'Review pending PR #42 & merge checks', completed: false },
    { id: '2', text: 'Summarize missed Discord threads (#dev-general)', completed: false },
    { id: '3', text: 'Clean local Docker images & cache', completed: true },
  ]);
  const [newText, setNewText] = useState('');

  // Synchronize incoming suggested routines
  useEffect(() => {
    if (routines.length > 0) {
      const additions = routines.map((text, idx) => ({
        id: `suggested-${Date.now()}-${idx}`,
        text,
        completed: false,
      }));
      setItems((prev) => [...additions, ...prev]);
    }
  }, [routines]);

  const toggleComplete = (id: string) => {
    setItems((prev) =>
      prev.map((item) => (item.id === id ? { ...item, completed: !item.completed } : item))
    );
  };

  const removeItem = (id: string) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  };

  const handleManualAdd = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newText.trim()) return;
    setItems((prev) => [
      { id: `manual-${Date.now()}`, text: newText.trim(), completed: false },
      ...prev,
    ]);
    setNewText('');
  };

  return (
    <div className={`glass flex flex-col ${compact ? 'p-2' : 'h-full p-4'}`}>
      {checkin && (
        <div
          className={`emboss rounded-2xl p-3 ${compact ? '' : 'mb-3'}`}
          style={{ borderColor: `${pacingColors.break_due}66`, boxShadow: `0 0 24px ${pacingColors.break_due}33` }}
        >
          <div className="flex items-center justify-between">
            <h3 className="text-[13px]" style={{ color: pacingColors.break_due }}>
              {checkin.minutes} minutes locked in — time to move
            </h3>
            {onSnooze && (
              <button
                onClick={onSnooze}
                className="emboss-pill flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] text-white/88"
                title={`Remind me in ${snoozeMinutes} minutes`}
              >
                <AlarmClockOff size={11} /> {snoozeMinutes} min
              </button>
            )}
          </div>
          <p className="mt-1 text-[12px] text-white/82">
            Suggested: <b className="text-white">{checkin.suggestion.title}</b> — {checkin.suggestion.detail}
          </p>
          <div className="mt-2.5 grid grid-cols-2 gap-1.5">
            {checkin.options.map((opt) => {
              const Icon = ROUTINE_ICON[opt.kind] ?? Sparkles;
              const suggested = (opt.id ?? opt.kind) === (checkin.suggestion.id ?? checkin.suggestion.kind);
              return (
                <button
                  key={opt.kind}
                  onClick={() => onStartBreak?.(opt.id ?? opt.kind)}
                  data-active={suggested}
                  title={opt.detail}
                  className="emboss-pill flex items-center gap-1.5 rounded-xl px-2.5 py-2 text-left text-[12px]"
                  style={{ color: suggested ? pacingColors.break_due : '#E5E7EB' }}
                >
                  <Icon size={14} className="shrink-0" />
                  <span className="truncate">{opt.title}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {!compact && (
        <>
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles size={14} className="text-pulse" />
              <div className="panel-title">Suggested Daily Routines</div>
            </div>
            <button
              onClick={onRequestRoutines}
              disabled={!connected}
              title="Consolidate memory & suggest new routines"
              className="flex items-center gap-1.5 rounded px-2 py-1 text-[11.5px] text-dust transition-colors hover:bg-white/5 hover:text-starlight disabled:opacity-40"
            >
              <RefreshCw size={12} />
              <span>Refresh</span>
            </button>
          </div>

          <form onSubmit={handleManualAdd} className="mb-3 flex gap-2">
            <input
              type="text"
              value={newText}
              onChange={(e) => setNewText(e.target.value)}
              placeholder="Add quick routine task..."
              className="selectable w-full rounded-lg border border-glass bg-white/[0.03] px-2.5 py-1.5 text-xs text-starlight placeholder:text-dust focus:outline-none focus:border-pulse/50"
            />
            <button
              type="submit"
              disabled={!newText.trim()}
              className="rounded-lg border border-glass bg-white/[0.04] px-2.5 py-1.5 text-dust hover:text-starlight disabled:opacity-30"
            >
              <Plus size={14} />
            </button>
          </form>

          <div className="flex-1 space-y-1.5 overflow-y-auto pr-1">
            <AnimatePresence initial={false}>
              {items.map((item) => (
                <motion.div
                  key={item.id}
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, height: 0 }}
                  className={`group flex items-center justify-between gap-2.5 rounded-lg border border-glass px-2.5 py-2 text-xs transition-colors ${
                    item.completed ? 'bg-white/[0.01] opacity-50' : 'bg-white/[0.03] hover:bg-white/[0.05]'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => toggleComplete(item.id)}
                    className="flex flex-1 items-center gap-2.5 text-left"
                  >
                    {item.completed ? (
                      <CheckCircle2 size={14} className="text-success shrink-0" />
                    ) : (
                      <Circle size={14} className="text-dust hover:text-pulse shrink-0" />
                    )}
                    <span className={`selectable ${item.completed ? 'line-through text-dust' : 'text-starlight'}`}>
                      {item.text}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => removeItem(item.id)}
                    className="opacity-0 group-hover:opacity-100 text-dust hover:text-danger p-1 transition-opacity"
                  >
                    <Trash2 size={12} />
                  </button>
                </motion.div>
              ))}
            </AnimatePresence>
            {items.length === 0 && (
              <div className="py-6 text-center text-xs text-dust">No active routine items.</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
