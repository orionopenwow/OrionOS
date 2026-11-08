import { useEffect, useRef, useState } from 'react';
import { Snowflake } from 'lucide-react';
import { ORION_HTTP_URL } from '../../lib/assets';
import { setBlackHoleSignals } from '../../lib/blackhole';

/**
 * Safety net on the HUD (complete plan step 3):
 *  - polls the health monitor (GET /health/status?source=hud, which also tells Orion the screen is alive) and
 *    feeds its `turbulence` into the black hole: calm when all is well, stormier when something needs attention
 *  - the kill switch: a small snowflake button (tap twice to freeze); while frozen, a pill that can ask your phone
 *    to unfreeze (only the phone or the boardroom can actually unfreeze)
 */
interface Check {
  id: string;
  label: string;
  status: 'ok' | 'warn' | 'fail';
  reason: string;
  fix_words?: string;
}
interface Health {
  status: 'ok' | 'warn' | 'fail';
  words: string;
  turbulence: number;
  checks: Check[];
}
interface Freeze {
  frozen: boolean;
  by?: string;
}

const pill: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderRadius: 999,
  background: 'rgba(26,31,39,0.85)', border: '1px solid #2F3744', color: '#DDE5F0',
  font: '500 12px/1.2 system-ui, sans-serif', backdropFilter: 'blur(8px)',
};

export default function SafetyLight({ inline = false }: { inline?: boolean }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [freeze, setFreeze] = useState<Freeze>({ frozen: false });
  const [armed, setArmed] = useState(false);
  const [note, setNote] = useState('');
  const [open, setOpen] = useState(false);
  const armTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const [h, f] = await Promise.all([
          fetch(`${ORION_HTTP_URL}/health/status?source=hud`).then((r) => (r.ok ? r.json() : null)),
          fetch(`${ORION_HTTP_URL}/freeze`).then((r) => (r.ok ? r.json() : null)),
        ]);
        if (!alive) return;
        if (h && Array.isArray(h.checks)) {
          setHealth(h);
          setBlackHoleSignals({ turbulence: Number(h.turbulence) || 0 });
        }
        if (f?.freeze) setFreeze(f.freeze);
      } catch {
        /* daemon not up yet */
      }
    };
    poll();
    const t = setInterval(poll, 15000);
    return () => {
      alive = false;
      clearInterval(t);
      setBlackHoleSignals({ turbulence: 0 });
    };
  }, []);

  const post = async (path: string) => {
    try {
      const r = await fetch(`${ORION_HTTP_URL}${path}`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ by: 'the HUD' }),
      });
      return r.ok ? await r.json() : null;
    } catch {
      return null;
    }
  };

  const onFreeze = async () => {
    if (!armed) {
      setArmed(true);
      clearTimeout(armTimer.current);
      armTimer.current = setTimeout(() => setArmed(false), 3000);
      return;
    }
    setArmed(false);
    const res = await post('/freeze');
    if (res?.freeze) setFreeze(res.freeze);
    setNote(res ? 'Frozen. Unfreeze from your phone.' : "Couldn't reach Orion.");
  };

  const askUnfreeze = async () => {
    const res = await post('/freeze/unfreeze');
    setNote(res?.message ?? "Couldn't reach Orion.");
  };

  const problems = (health?.checks ?? []).filter((c) => c.status !== 'ok' && c.id !== 'freeze');
  const color = health?.status === 'fail' ? '#FF5C8A' : health?.status === 'warn' ? '#FFC66B' : '#5CFFB0';

  return (
    <div style={inline
      ? { position: 'relative', display: 'flex', gap: 6, alignItems: 'center' }
      : { position: 'fixed', left: 16, bottom: 86, zIndex: 55, display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
      {open && problems.length > 0 && (
        <div role="status" style={{ ...pill, flexDirection: 'column', alignItems: 'flex-start', borderRadius: 14, maxWidth: 340,
          ...(inline ? { position: 'absolute', right: 0, bottom: 'calc(100% + 8px)', width: 'max-content' } : {}) }}>
          {problems.map((c) => (
            <div key={c.id}>
              <b style={{ color: c.status === 'fail' ? '#FF5C8A' : '#FFC66B' }}>{c.label}:</b> {c.reason}
              {c.fix_words ? <div style={{ opacity: 0.75 }}>{c.fix_words}</div> : null}
            </div>
          ))}
        </div>
      )}
      {note && <div role="status" style={inline ? { ...pill, position: 'absolute', right: 0, bottom: 'calc(100% + 8px)', width: 'max-content' } : pill}>{note}</div>}
      <div style={{ display: 'flex', gap: 6, flexDirection: inline ? 'row-reverse' : 'row' }}>
        {freeze.frozen ? (
          <button style={{ ...pill, cursor: 'pointer', borderColor: '#7FF2FF' }} onClick={askUnfreeze}
            title="Orion is frozen: no self-changes, updates or agents. Click to ask your phone to unfreeze.">
            <Snowflake size={14} color="#7FF2FF" /> Frozen · ask phone to unfreeze
          </button>
        ) : (
          <button style={{ ...pill, cursor: 'pointer', padding: '6px 9px', borderColor: armed ? '#7FF2FF' : '#2F3744' }}
            onClick={onFreeze} title="Kill switch: stop all self-changes, updates and agents (tap twice)"
            aria-label={armed ? 'Tap again to freeze Orion' : 'Freeze Orion (tap twice)'}>
            <Snowflake size={14} color={armed ? '#7FF2FF' : '#8A93C2'} />
            {armed ? 'Tap again to freeze' : null}
          </button>
        )}
        {inline && problems.length === 0 && (
          <span role="img" style={{ ...pill, padding: '6px 9px' }} title={health?.words ?? 'Checking Orion'}
            aria-label={health ? `Health: ${health.words}` : 'Checking Orion'}>
            <span style={{ width: 9, height: 9, borderRadius: 9, background: health ? color : '#5A6088', boxShadow: health ? `0 0 10px ${color}` : 'none' }} />
          </span>
        )}
        {problems.length > 0 && (
          <button style={{ ...pill, cursor: 'pointer' }} onClick={() => setOpen((o) => !o)} title={health?.words} aria-expanded={open}>
            <span style={{ width: 9, height: 9, borderRadius: 9, background: color, boxShadow: `0 0 10px ${color}` }} />
            {problems.length === 1 ? problems[0].label : `${problems.length} things need a look`}
          </button>
        )}
      </div>
    </div>
  );
}
