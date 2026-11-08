/** VIP orbiting satellites (Feature Bible D2): VIP threads with unread messages orbit the core. */
import { motion } from 'framer-motion';
import type { CommsThread } from '@orion/shared-types';
import { sourceColors } from '../../theme/colors';

interface Props {
  threads: CommsThread[];
  size: number;
  onOpen: (threadKey: string) => void;
}

export default function VipSatellites({ threads, size, onOpen }: Props) {
  const vips = threads.filter((t) => t.vip && t.unread > 0).slice(0, 6);
  if (!vips.length) return null;
  const r = size * 0.62;
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-label="VIP messages">
      <motion.div
        className="relative"
        style={{ width: 0, height: 0 }}
        animate={{ rotate: 360 }}
        transition={{ duration: 120, repeat: Infinity, ease: 'linear' }}
      >
        {vips.map((t, i) => {
          const angle = (i / vips.length) * Math.PI * 2 - Math.PI / 2;
          const color = (sourceColors as Record<string, string>)[t.platform] ?? '#FFB300';
          const initials = t.thread_name
            .split(/\s+/)
            .map((w) => w[0])
            .join('')
            .slice(0, 2)
            .toUpperCase();
          return (
            <motion.button
              key={t.key}
              type="button"
              title={`${t.thread_name} · ${t.unread} unread on ${t.platform}`}
              onClick={(e) => {
                e.stopPropagation();
                onOpen(t.key);
              }}
              className="pointer-events-auto absolute -translate-x-1/2 -translate-y-1/2 w-11 h-11 rounded-full flex items-center justify-center font-display text-xs font-bold text-white"
              style={{
                left: Math.cos(angle) * r,
                top: Math.sin(angle) * r,
                background: `radial-gradient(circle at 35% 30%, ${color}cc, #0B0D17 75%)`,
                border: `1px solid ${color}`,
                boxShadow: `0 0 18px ${color}, inset 0 1px 1px rgba(255,255,255,0.3)`,
              }}
              animate={{ rotate: -360, scale: [1, 1.08, 1] }}
              transition={{
                rotate: { duration: 120, repeat: Infinity, ease: 'linear' },
                scale: { duration: 2.4, repeat: Infinity },
              }}
            >
              {initials}
              <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-[#FF2A85] text-[9px] leading-4">
                {t.unread}
              </span>
            </motion.button>
          );
        })}
      </motion.div>
    </div>
  );
}
