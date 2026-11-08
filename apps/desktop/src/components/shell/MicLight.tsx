import { useEffect, useState } from 'react';
import { ORION_HTTP_URL } from '../../lib/assets';

/**
 * The hot mic's light: visible whenever Orion is listening for your voice (OrionOS, `orion-ctl mic on`).
 * Click it, or press Ctrl+Alt+M, to mute.
 */
export default function MicLight() {
  const [on, setOn] = useState(false);
  const [heard, setHeard] = useState<string>('');
  const [wake, setWake] = useState(false); // [v1gaps] openWakeWord listening for "Orion"

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch(`${ORION_HTTP_URL}/hotmic`);
        if (!r.ok) return;
        const s = await r.json();
        if (!alive) return;
        setOn(Boolean(s.listening));
        setWake(Boolean(s.wake?.on && s.wake?.active));
        setHeard(s.last?.heard && s.last?.at && Date.now() / 1000 - s.last.at < 8 ? s.last.heard : '');
      } catch {
        /* daemon not up yet, or not OrionOS */
      }
    };
    poll();
    const t = setInterval(poll, 2500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (!on) return null;
  const mute = () =>
    fetch(`${ORION_HTTP_URL}/hotmic`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ action: 'off' }),
    }).then(() => setOn(false), () => undefined);

  return (
    <button
      onClick={mute}
      title="Orion is listening for your voice. Click or press Ctrl+Alt+M to mute."
      style={{
        position: 'fixed', top: 10, left: '50%', transform: 'translateX(-50%)', zIndex: 60,
        display: 'flex', alignItems: 'center', gap: 8, padding: '6px 14px', borderRadius: 999,
        background: 'rgba(26,31,39,0.85)', border: '1px solid #2F3744', color: '#DDE5F0',
        font: '500 12px/1.2 system-ui, sans-serif', cursor: 'pointer', backdropFilter: 'blur(8px)',
      }}
    >
      <span style={{ width: 9, height: 9, borderRadius: 9, background: '#FF5C8A', boxShadow: '0 0 10px #FF5C8A' }} />
      {heard ? `Heard: “${heard.slice(0, 60)}”` : wake ? 'Listening for “Orion”' : 'Listening for your voice'}
    </button>
  );
}
