/** Small shared building blocks for the Feature Bible panels (Tactile Cosmic Emboss style). */
import type { ReactNode } from 'react';

export function Section({
  title,
  color = '#00E5FF',
  icon,
  right,
  children,
}: {
  title: string;
  color?: string;
  icon?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="emboss rounded-2xl p-3.5 space-y-2.5">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0 text-white/90">
          <span className="shrink-0 opacity-90" style={{ color }}>{icon}</span>
          <span className="text-[13px] font-bold truncate">{title}</span>
        </div>
        {right}
      </header>
      {children}
    </section>
  );
}

export function PillButton({
  children,
  onClick,
  color = '#00E5FF',
  disabled,
  title,
  active,
}: {
  children: ReactNode;
  onClick?: () => void;
  color?: string;
  disabled?: boolean;
  title?: string;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onClick?.();
      }}
      className="emboss-pill inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold transition-all hover:brightness-125 active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed"
      style={{
        color: active ? '#fff' : 'rgba(255,255,255,0.85)',
        background: active ? `${color}30` : undefined,
      }}
    >
      {children}
    </button>
  );
}

export function Bar({ value, color, warn }: { value: number; color: string; warn?: boolean }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="h-2 w-full rounded-full bg-black/50 shadow-inner overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-700 ${warn ? 'animate-pulse' : ''}`}
        style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${color}88, ${color})`, boxShadow: `0 0 10px ${color}` }}
      />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-[11px] text-white/45 leading-relaxed">{children}</p>;
}

export function timeAgo(ts: number | null | undefined): string {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function fmtDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function fmtMoney(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}
