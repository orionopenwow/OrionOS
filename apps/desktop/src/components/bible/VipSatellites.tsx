/**
 * VIP orbiting satellites (Feature Bible D2): VIP threads with unread messages orbit the core.
 * Look pass: hovering one makes it bigger, stops its pulse and holds the orbit still until the pointer leaves.
 */
import { useRef, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import type { CommsThread } from '@orion/shared-types';
import { sourceColors } from '../../theme/colors';
import { useCalmMotion } from '../../lib/hudPrefs';
import { COUNTER_ROTATE, useOrbit } from '../../lib/orbit';

interface Props {
  threads: CommsThread[];
  size: number;
  onOpen: (threadKey: string) => void;
}

export default function VipSatellites({ threads, size, onOpen }: Props) {
  const vips = threads.filter((t) => t.vip && t.unread > 0).slice(0, 6);
  const [hovered, setHovered] = useState<string | null>(null);
  const still = !!useReducedMotion() || useCalmMotion();
  const orbitEl = useRef<HTMLDivElement>(null);
  const hold = useRef(false);
  hold.current = !!hovered;
  useOrbit(orbitEl, hold, 120, still, [vips.length]);
  if (!vips.length) return null;
  const r = size * 0.62;
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-label="VIP messages">
      <div ref={orbitEl} className="relative" style={{ width: 0, height: 0, transform: 'rotate(var(--orbit, 0rad))' }}>
        {vips.map((t, i) => {
          const angle = (i / vips.length) * Math.PI * 2 - Math.PI / 2;
          const color = (sourceColors as Record<string, string>)[t.platform] ?? '#FFB300';
          const on = hovered === t.key;
          const initials = t.thread_name
            .split(/\s+/)
            .map((w) => w[0])
            .join('')
            .slice(0, 2)
            .toUpperCase();
          return (
            <div key={t.key} className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: Math.cos(angle) * r, top: Math.sin(angle) * r }}>
              <div style={COUNTER_ROTATE} className="relative">
                <motion.button
                  type="button"
                  title={`${t.thread_name} · ${t.unread} unread on ${t.platform}`}
                  aria-label={`${t.thread_name}: ${t.unread} unread on ${t.platform}. Open`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(t.key);
                  }}
                  onPointerEnter={() => setHovered(t.key)}
                  onPointerLeave={() => setHovered((h) => (h === t.key ? null : h))}
                  onFocus={() => setHovered(t.key)}
                  onBlur={() => setHovered((h) => (h === t.key ? null : h))}
                  className="pointer-events-auto relative w-11 h-11 rounded-full flex items-center justify-center font-display text-[13px] font-bold text-white"
                  style={{
                    background: `radial-gradient(circle at 35% 30%, ${color}cc, #11151B 75%)`,
                    border: `1px solid ${color}`,
                    boxShadow: `0 0 ${on ? 26 : 16}px ${color}, inset 0 1px 1px rgba(255,255,255,0.3)`,
                  }}
                  animate={on ? { scale: 1.35 } : still ? { scale: 1 } : { scale: [1, 1.06, 1] }}
                  transition={on ? { type: 'spring', stiffness: 260, damping: 20 } : { duration: 2.4, repeat: Infinity }}
                >
                  {initials}
                  <span className="absolute -top-1 -right-1 min-w-[17px] h-[17px] px-1 rounded-full bg-[#FF2A85] text-[11px] font-bold leading-[17px]">
                    {t.unread}
                  </span>
                </motion.button>
                {on && (
                  <span className="pointer-events-none absolute left-1/2 top-full mt-3 -translate-x-1/2 whitespace-nowrap rounded-md border border-white/15 bg-[#11151B]/90 px-2 py-0.5 text-[12px] font-semibold text-ink">
                    {t.thread_name}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
