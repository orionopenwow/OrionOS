/**
 * Guide Mode in the HUD (OrionOS 1.0 plan, D. "Guide Mode"). Say "Orion, show me how to …" or type it here.
 *
 *  - A step card (bottom centre): the step in one sentence, "Step 2 of 4", Done, Back and Stop. Steps move on by
 *    themselves when Orion sees you did them; Done always works.
 *  - The ring: on Windows a click-through always-on-top window (lib/guideOverlay.ts); on OrionOS the daemon starts
 *    the gtk-layer-shell overlay; in a plain browser it's drawn inside this page.
 *  - Orion never clicks for you here: "Orion does it" stays on the phone's next-step cards, behind your fingerprint.
 *
 * Data: GET /guide (polled while open), POST /guide/start | next | back | stop (apps/daemon/guide_mode.py).
 */
import { useEffect, useRef, useState } from 'react';
import { Compass, X } from 'lucide-react';
import { ORION_HTTP_URL } from '../../lib/assets';
import { overlayKind, ringBox, syncOverlay, type GuideState } from '../../lib/guideOverlay';
import { stepWords } from '../../lib/guideGeometry';

export const OPEN_GUIDE_EVENT = 'orion:open-guide';

const card: React.CSSProperties = {
  position: 'fixed', left: '50%', bottom: 96, transform: 'translateX(-50%)', zIndex: 70, width: 440,
  maxWidth: 'calc(100vw - 32px)', padding: '14px 16px', borderRadius: 18, background: 'rgba(11,15,30,0.9)',
  border: '1px solid rgba(0,229,255,0.45)', color: '#D8DEFF', font: '400 13px/1.45 Figtree, system-ui, sans-serif',
  backdropFilter: 'blur(14px)', boxShadow: '0 18px 50px rgba(2,3,10,0.6)',
};
export const pill: React.CSSProperties = {
  padding: '6px 14px', borderRadius: 999, border: '1px solid #3A3F6B', background: 'rgba(124,92,255,0.18)',
  color: '#FFFFFF', cursor: 'pointer', font: '600 12px Figtree, system-ui, sans-serif',
};

async function post(path: string, body?: unknown): Promise<GuideState | null> {
  try {
    const r = await fetch(`${ORION_HTTP_URL}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    const j = await r.json();
    if (!r.ok) return { active: false, withheld: String(j?.detail ?? "Couldn't start that guide.") };
    return j as GuideState;
  } catch {
    return null;
  }
}

/** The ring, drawn in this page (browser) or in the full-screen overlay window (Windows). */
export function GuideRing({ state, scale }: { state: GuideState; scale: number }) {
  if (!state.active || !state.target) return null;
  const b = ringBox(state.target, scale);
  return (
    <div
      aria-hidden
      className="orion-guide-ring"
      style={{
        position: 'fixed', left: b.left, top: b.top, width: b.size, height: b.size, borderRadius: '50%',
        border: '4px solid #00E5FF', boxShadow: '0 0 0 6px rgba(124,92,255,0.55), 0 0 28px #00E5FF',
        pointerEvents: 'none', zIndex: 2147483000, animation: 'orionGuidePulse 1.4s ease-in-out infinite',
      }}
    >
      <style>{'@keyframes orionGuidePulse{0%,100%{transform:scale(1)}50%{transform:scale(1.12)}}'}</style>
    </div>
  );
}

/** Step text + Done / Back / Stop. Used in the HUD and in the Windows always-on-top card window. */
export function GuideCard({ state, onChange, style }: {
  state: GuideState;
  onChange: (s: GuideState | null) => void;
  style?: React.CSSProperties;
}) {
  if (!state.active) return null;
  const act = async (what: 'next' | 'back' | 'stop') => onChange(await post(`/guide/${what}`));
  return (
    <section style={{ ...card, ...style }} aria-label="Guide" aria-live="polite" onClick={(e) => e.stopPropagation()}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Compass size={16} color="#00E5FF" />
        <b style={{ flex: 1, fontSize: 13 }}>{state.title}</b>
        <span style={{ opacity: 0.65, fontSize: 12 }}>{stepWords(state.index, state.total)}</span>
      </div>
      <div style={{ fontSize: 15, color: '#FFFFFF' }}>{state.text}</div>
      {state.withheld ? (
        <div style={{ marginTop: 6, color: '#FFC66B' }}>{state.withheld}</div>
      ) : state.find && !state.target ? (
        <div style={{ marginTop: 6, opacity: 0.7 }}>Looking for “{state.find}” on your screen…</div>
      ) : state.auto ? (
        <div style={{ marginTop: 6, opacity: 0.7 }}>I'll move on when I see you've done it.</div>
      ) : null}
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <button style={{ ...pill, background: 'rgba(0,229,255,0.22)' }} onClick={() => void act('next')}>Done</button>
        <button style={pill} onClick={() => void act('back')} disabled={!state.index}>Back</button>
        <span style={{ flex: 1 }} />
        <button style={{ ...pill, background: 'transparent' }} onClick={() => void act('stop')}>Stop</button>
      </div>
    </section>
  );
}

export default function GuideMode() {
  const [state, setState] = useState<GuideState>({ active: false });
  const [asking, setAsking] = useState(false);
  const [task, setTask] = useState('');
  const [note, setNote] = useState('');
  const kind = overlayKind();
  const last = useRef('');

  const apply = (s: GuideState | null) => {
    if (!s) return;
    if (s.finished) setNote(`All done: ${s.finished}.`);
    setState(s);
  };

  // follow the daemon: fast while a guide runs, slowly otherwise (a guide can start by voice)
  useEffect(() => {
    let alive = true;
    let t: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await fetch(`${ORION_HTTP_URL}/guide`);
        if (r.ok && alive) setState((await r.json()) as GuideState);
      } catch {
        /* daemon not up yet */
      }
      if (alive) t = setTimeout(poll, state.active ? 1200 : 4000);
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [state.active]);

  useEffect(() => {
    const key = JSON.stringify([state.active, state.index, state.target, state.withheld]);
    if (key === last.current) return;
    last.current = key;
    void syncOverlay(state);
  }, [state]);

  useEffect(() => {
    const open = () => setAsking(true);
    window.addEventListener(OPEN_GUIDE_EVENT, open);
    return () => window.removeEventListener(OPEN_GUIDE_EVENT, open);
  }, []);

  const start = async () => {
    if (!task.trim()) return;
    const s = await post('/guide/start', { task });
    if (s && !s.active) setNote(s.withheld ?? "Couldn't start that guide.");
    else if (s) {
      setAsking(false);
      setTask('');
      setNote('');
    } else setNote("Couldn't reach Orion.");
    apply(s);
  };

  return (
    <>
      {kind === 'in-page' && <GuideRing state={state} scale={window.devicePixelRatio || 1} />}
      <GuideCard state={state} onChange={apply} />
      {!state.active && (asking || note) && (
        <section style={card} aria-label="Show me how" onClick={(e) => e.stopPropagation()}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <Compass size={16} color="#00E5FF" />
            <b style={{ flex: 1 }}>Show me how to…</b>
            <button style={{ ...pill, padding: 4, background: 'transparent', border: 'none' }} aria-label="Close"
                    onClick={() => { setAsking(false); setNote(''); }}>
              <X size={16} />
            </button>
          </div>
          {asking && (
            <form onSubmit={(e) => { e.preventDefault(); void start(); }} style={{ display: 'flex', gap: 8 }}>
              <input
                autoFocus
                value={task}
                onChange={(e) => setTask(e.target.value)}
                placeholder="install Discord, connect to Wi-Fi, pair my phone…"
                aria-label="What to do"
                style={{ flex: 1, padding: '7px 12px', borderRadius: 999, border: '1px solid #3A3F6B',
                         background: 'rgba(216,222,255,0.06)', color: '#FFFFFF', font: 'inherit' }}
              />
              <button style={pill} type="submit">Guide me</button>
            </form>
          )}
          {note && <div style={{ marginTop: 8, opacity: 0.85 }}>{note}</div>}
          <div style={{ marginTop: 8, opacity: 0.55, fontSize: 11.5 }}>
            You can also say “Orion, show me how to …”. Orion only looks at the screen while a guide runs.
          </div>
        </section>
      )}
    </>
  );
}
