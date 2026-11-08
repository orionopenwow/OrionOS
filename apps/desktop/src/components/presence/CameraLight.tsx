import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, CameraOff, SlidersHorizontal } from 'lucide-react';
import { onDaemonEvent } from '../../lib/daemonEvents';
import { GESTURE_WORDS, presenceStatus, presenceSwitch, type PresenceStatus } from '../../lib/presence';
import GestureCalibration from './GestureCalibration';
import { onOrionOpen } from '../../lib/menu';

/**
 * Presence camera (after-1.0 plan §6): one tap turns the webcam's presence and gestures on or off, silently.
 * While it's on this light stays visible (a red dot and "Camera on"), so the camera is never on unseen.
 * Two-hand spread / squeeze zoom the HUD; other parts can listen for the `orion:presence-gesture` window event.
 */
const pill: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderRadius: 999,
  background: 'rgba(26,31,39,0.85)', border: '1px solid #2F3744', color: '#DDE5F0',
  font: '500 12px/1.2 system-ui, sans-serif', backdropFilter: 'blur(8px)', cursor: 'pointer',
};

const ZOOM_MIN = 0.8;
const ZOOM_MAX = 1.3;

export default function CameraLight({ inline = false }: { inline?: boolean }) {
  const [st, setSt] = useState<PresenceStatus | null>(null);
  const [note, setNote] = useState('');
  const [panel, setPanel] = useState(false);
  const noteTimer = useRef<ReturnType<typeof setTimeout>>();
  const zoom = useRef(1);

  const say = useCallback((text: string, ms = 3500) => {
    setNote(text);
    clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => setNote(''), ms);
  }, []);

  const refresh = useCallback(async () => {
    const s = await presenceStatus();
    if (s) setSt(s);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, st?.on ? 2000 : 5000);
    return () => clearInterval(t);
  }, [refresh, st?.on]);

  useEffect(
    () =>
      onDaemonEvent((ev) => {
        if (ev.event === 'PRESENCE_STATE') {
          refresh();
        } else if (ev.event === 'PRESENCE' && ev.kind === 'back' && typeof ev.text === 'string') {
          say(ev.text, 5000);
        } else if (ev.event === 'PRESENCE_GESTURE') {
          const g = String(ev.gesture || '');
          const result = ev.result as { ok?: boolean; reason?: string } | string | undefined;
          if (g === 'spread' || g === 'squeeze') {
            zoom.current = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoom.current + (g === 'spread' ? 0.1 : -0.1)));
            document.documentElement.style.setProperty('zoom', String(Math.round(zoom.current * 100) / 100));
          }
          window.dispatchEvent(new CustomEvent('orion:presence-gesture', { detail: ev }));
          if (g === 'thumbs_up' && result && typeof result === 'object' && result.ok === false) {
            say(result.reason || 'A thumbs up only approves small cards.', 5000);
          } else if (typeof result === 'string' && result !== 'ok' && result !== 'hud') {
            say(result, 4500);
          } else if (GESTURE_WORDS[g]) {
            say(GESTURE_WORDS[g]);
          }
        }
      }),
    [refresh, say],
  );

  const toggle = async () => {
    const s = await presenceSwitch('toggle');
    if (s) {
      setSt(s);
      if (s.error) say(s.error, 6000);
    } else {
      say("Couldn't reach Orion, so the camera stays off.");
    }
  };

  // [simple-menu] More > Camera presence turns it on or off
  const toggleRef = useRef(toggle);
  toggleRef.current = toggle;
  useEffect(() => onOrionOpen('camera', () => void toggleRef.current()), []);

  const on = Boolean(st?.on);
  const resting = on && Boolean(st?.paused);
  if (inline && !on && !note && !panel) return null;
  return (
    <>
      <div style={inline
        ? { position: 'relative', display: 'flex', gap: 6, alignItems: 'center' }
        : { position: 'fixed', left: 16, bottom: 46, zIndex: 55, display: 'flex', gap: 6, alignItems: 'center' }}>
        <button
          onClick={toggle}
          aria-pressed={on}
          title={
            on
              ? 'The camera is on (presence and gestures, all on this computer). Tap to turn it off.'
              : 'The camera is off. Tap to turn on presence and hand gestures. Nothing is recorded or sent.'
          }
          style={{ ...pill, opacity: on ? 1 : 0.6, borderColor: on ? '#FF5C8A' : '#2F3744' }}
        >
          {on ? <Camera size={14} /> : <CameraOff size={14} />}
          {on && (
            <span
              style={{
                width: 8, height: 8, borderRadius: 8, background: resting ? '#8A90B8' : '#FF5C8A',
                boxShadow: resting ? 'none' : '0 0 10px #FF5C8A',
              }}
            />
          )}
          {on ? (resting ? 'Camera resting' : 'Camera on') : null}
          <span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{on ? '' : 'Camera off'}</span>
        </button>
        {on && (
          <button onClick={() => setPanel(true)} title="Gesture zone and presence settings" style={pill}>
            <SlidersHorizontal size={14} />
          </button>
        )}
        {note && <div role="status" style={{ ...pill, cursor: 'default', maxWidth: 360, ...(inline ? { position: 'absolute', right: 0, bottom: 'calc(100% + 8px)', width: 'max-content' } : {}) }}>{note}</div>}
      </div>
      {panel && <GestureCalibration status={st} onClose={() => { setPanel(false); refresh(); }} />}
    </>
  );
}
