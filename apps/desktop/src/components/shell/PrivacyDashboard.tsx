/**
 * One privacy dashboard (OrionOS 1.0 plan, K.): everything Orion can see, hear or keep, with its state and a
 * one-tap off for each, plus "Turn everything off". Turning things on stays in each feature's own settings.
 * Data: GET /privacy/dashboard, POST /privacy/off {id}, POST /privacy/off-all (apps/daemon/privacy_dashboard.py).
 * OrionOS Settings has the same list (Settings > Privacy dashboard).
 */
import { useCallback, useEffect, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import GlassWindow from '../glass/GlassWindow';
import { ORION_HTTP_URL } from '../../lib/assets';

export const OPEN_PRIVACY_EVENT = 'orion:open-privacy';

interface Sensor {
  id: string;
  label: string;
  what: string;
  how_on: string;
  on: boolean;
  available: boolean;
  can_off: boolean;
}
interface Dash {
  sensors: Sensor[];
  on: number;
  words: string;
  message?: string;
}

export default function PrivacyDashboard({ onClose, mobile }: { onClose: () => void; mobile?: boolean }) {
  const [d, setD] = useState<Dash | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${ORION_HTTP_URL}/privacy/dashboard`);
      if (r.ok) setD((await r.json()) as Dash);
      else setNote("Orion's brain didn't answer.");
    } catch {
      setNote("Orion's brain isn't running.");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const call = async (path: string, body: unknown) => {
    setBusy(true);
    try {
      const r = await fetch(`${ORION_HTTP_URL}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = (await r.json()) as Dash;
      if (j.sensors) setD(j);
      setNote(j.message ?? '');
    } catch {
      setNote("Couldn't reach Orion.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <GlassWindow
      id="privacy"
      title="Privacy"
      subtitle="What Orion can see and hear"
      icon={<ShieldCheck size={14} className="text-[#5CFFB0]" />}
      onClose={onClose}
      maximisable={false}
      mobile={mobile}
      className="left-4 bottom-[88px] w-[420px] max-w-[calc(100vw-32px)]"
    >
      <div className="p-3 space-y-2 max-h-[64vh] overflow-y-auto">
        <div className="flex items-center gap-3 rounded-xl px-3 py-2" style={{ background: 'rgba(216,222,255,0.05)' }}>
          <span className="flex-1 text-[13px] text-[#D8DEFF]">{d ? d.words : 'Checking…'}</span>
          <button
            type="button"
            disabled={busy || !d || d.on === 0}
            onClick={() => void call('/privacy/off-all', {})}
            className="rounded-full px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-40"
            style={{ background: 'rgba(255,92,138,0.28)', border: '1px solid rgba(255,92,138,0.6)' }}
          >
            Turn everything off
          </button>
        </div>
        {note && <div className="px-3 text-[12px] text-[#FFC66B]">{note}</div>}
        {(d?.sensors ?? []).map((s) => (
          <div key={s.id} className="flex items-start gap-3 rounded-xl px-3 py-2 hover:bg-white/[0.04]">
            <span
              aria-hidden
              className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ background: s.on ? '#FF5C8A' : s.available ? '#5CFFB0' : '#5A6088',
                       boxShadow: s.on ? '0 0 8px #FF5C8A' : 'none' }}
            />
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold text-[#D8DEFF]">
                {s.label}
                <span className="ml-2 text-[11px] font-normal text-[#D8DEFF]/60">
                  {s.on ? 'On' : s.available ? 'Off' : 'Not on this computer'}
                </span>
              </span>
              <span className="block text-[11.5px] leading-snug text-[#D8DEFF]/55">{s.what}</span>
              {!s.on && s.available && s.how_on && (
                <span className="block text-[11px] text-[#D8DEFF]/40">To turn on: {s.how_on}</span>
              )}
            </span>
            {s.can_off && (
              <button
                type="button"
                disabled={busy}
                aria-label={`Turn off ${s.label}`}
                onClick={() => void call('/privacy/off', { id: s.id })}
                className="shrink-0 rounded-full px-3 py-1 text-[12px] font-semibold text-white"
                style={{ background: 'rgba(124,92,255,0.25)', border: '1px solid #3A3F6B' }}
              >
                Turn off
              </button>
            )}
          </div>
        ))}
      </div>
    </GlassWindow>
  );
}
