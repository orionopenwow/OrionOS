import { useEffect, useState } from 'react';
import { Cpu, X } from 'lucide-react';
import { ORION_HTTP_URL } from '../../lib/assets';

/**
 * Hardware report card (cheap feature 2). While you "Try Orion" from the USB stick, this card says whether this PC
 * is safe to install on: a green verdict, or the short red list of what to fix first, and it can send the report to
 * your phone. Data: GET /hardware/report (orion-check-hardware). Shown once per stick session; Settings > This PC's
 * hardware has it any time.
 */
interface Row {
  id: string;
  label: string;
  status: 'ok' | 'warn' | 'fail' | 'na';
  text: string;
  fix: string;
}
interface Report {
  available: boolean;
  safe?: boolean;
  headline?: string;
  try_mode?: boolean;
  rows?: Row[];
}

const SEEN = 'orion.hardwareCard.closed';
const card: React.CSSProperties = {
  position: 'fixed', right: 24, bottom: 96, zIndex: 56, width: 380, maxHeight: '60vh', overflowY: 'auto',
  padding: '16px 18px', borderRadius: 20, background: 'rgba(26,31,39,0.86)', border: '1px solid rgba(46,107,255,0.55)',
  color: '#DDE5F0', font: '400 13px/1.45 Figtree, system-ui, sans-serif', backdropFilter: 'blur(14px)',
  boxShadow: '0 18px 50px rgba(2,3,10,0.6)',
};
const btn: React.CSSProperties = {
  padding: '6px 14px', borderRadius: 999, border: '1px solid #3A4352', background: 'rgba(46,107,255,0.18)',
  color: '#FFFFFF', cursor: 'pointer', font: '600 12px Figtree, system-ui, sans-serif',
};

function closedBefore(): boolean {
  try {
    return sessionStorage.getItem(SEEN) === '1';
  } catch {
    return false;
  }
}

export default function HardwareCard() {
  const [rep, setRep] = useState<Report | null>(null);
  const [hidden, setHidden] = useState(closedBefore());
  const [note, setNote] = useState('');

  useEffect(() => {
    if (hidden) return;
    let alive = true;
    // give the desktop a moment to settle (sound and screens come up a little after the HUD)
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`${ORION_HTTP_URL}/hardware/report`);
        const body = r.ok ? ((await r.json()) as Report) : null;
        if (alive && body) setRep(body);
      } catch {
        /* daemon not up yet: no card */
      }
    }, 8000);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [hidden]);

  if (hidden || !rep?.available || !rep.try_mode) return null;
  const close = () => {
    try {
      sessionStorage.setItem(SEEN, '1');
    } catch {
      /* private mode: closes for now */
    }
    setHidden(true);
  };
  const send = async () => {
    try {
      const r = await fetch(`${ORION_HTTP_URL}/hardware/report/send`, { method: 'POST' });
      const body = r.ok ? await r.json() : null;
      setNote(body?.sent ? 'Sent to your phone.' : body?.message ?? "Couldn't reach Orion.");
    } catch {
      setNote("Couldn't reach Orion.");
    }
  };
  const fails = (rep.rows ?? []).filter((r) => r.status === 'fail');
  const notes = (rep.rows ?? []).filter((r) => r.status === 'warn');
  return (
    <section style={card} aria-label="Hardware report" onClick={(e) => e.stopPropagation()}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <Cpu size={18} color={rep.safe ? '#5CFFB0' : '#FF5C8A'} />
        <b style={{ flex: 1, color: rep.safe ? '#5CFFB0' : '#FF8FB0', fontSize: 14 }}>{rep.headline}</b>
        <button style={{ ...btn, padding: 4, background: 'transparent', border: 'none' }} onClick={close} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      {rep.safe && <div style={{ opacity: 0.85 }}>Graphics, Wi-Fi, sound, sleep and the rest work on this PC.</div>}
      {fails.map((r) => (
        <div key={r.id} style={{ marginTop: 8 }}>
          <b style={{ color: '#FF5C8A' }}>{r.label}:</b> {r.text}
          {r.fix && <div style={{ opacity: 0.75 }}>Fix: {r.fix}</div>}
        </div>
      ))}
      {notes.length > 0 && (
        <div style={{ marginTop: 10, opacity: 0.8 }}>
          {notes.map((r) => (
            <div key={r.id}>
              <b style={{ color: '#FFC66B' }}>{r.label}:</b> {r.text}
            </div>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
        <button style={btn} onClick={send}>Send to my phone</button>
        {note && <span style={{ opacity: 0.8 }}>{note}</span>}
      </div>
    </section>
  );
}
