import { useEffect, useState } from 'react';
import {
  presenceCalibrate,
  presenceCalibration,
  presenceConfig,
  presenceSwitch,
  type Calibration,
  type PresenceStatus,
} from '../../lib/presence';

/**
 * The calibration screen: shows the gesture zone and a dot where your hand is (points only, never a picture).
 * "Set my zone" records where you comfortably hold your hand for a few seconds and saves that as the zone.
 */
const SWITCHES: [string, string][] = [
  ['lock_when_away', 'Lock the screen when I walk away'],
  ['greet', 'Greet me when I come back'],
  ['greet_aloud', 'Say the greeting out loud'],
  ['pause_media', 'Pause music and video when I look away'],
  ['gestures', 'Hand gestures'],
  ['pointer', 'Point and pinch moves the pointer'],
  ['off_on_battery', 'Switch off on battery'],
  ['pause_in_fullscreen', 'Rest during full-screen games and videos'],
  ['start_with_orion', 'Start with Orion (off means it always starts off)'],
];

const HELP = [
  ['Hold your hand in the zone for half a second', 'before any gesture counts'],
  ['Point, then pinch', 'move the pointer, click'],
  ['Open palm, hold still', 'freeze Orion and pause all agents'],
  ['Swipe left or right', 'switch desktop'],
  ['Draw a rectangle with two fingers', 'new desktop'],
  ['Make a fist, throw, open', 'left or right: move the window; down: minimise'],
  ['Two hands apart or together', 'zoom the HUD'],
  ['Thumbs up', 'approve a small information card only (never money or boardroom)'],
];

export default function GestureCalibration({ status, onClose }: { status: PresenceStatus | null; onClose: () => void }) {
  const [cal, setCal] = useState<Calibration | null>(null);
  const [cfg, setCfg] = useState<Record<string, unknown>>(status?.config ?? {});
  const [msg, setMsg] = useState('');

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      const c = await presenceCalibration();
      if (alive && c) setCal(c);
    };
    poll();
    const t = setInterval(poll, 250);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const flip = async (key: string) => {
    const s = await presenceConfig({ [key]: !cfg[key] });
    if (s) setCfg(s.config);
  };
  const zone = cal?.zone ?? [0.12, 0.06, 0.88, 0.85];
  const W = 320;
  const H = 240;

  return (
    <div
      role="dialog"
      aria-label="Camera presence and gestures"
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, zIndex: 80, background: 'rgba(4,6,14,0.6)', display: 'grid', placeItems: 'center' }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(720px, 94vw)', maxHeight: '90vh', overflowY: 'auto', padding: 20, borderRadius: 20,
          background: 'rgba(26,31,39,0.95)', border: '1px solid rgba(46,107,255,0.55)', color: '#DDE5F0',
          font: '400 13px/1.45 Figtree, system-ui, sans-serif', display: 'flex', flexWrap: 'wrap', gap: 20,
        }}
      >
        <div style={{ flex: '1 1 320px' }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 16 }}>Gesture zone</h3>
          <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ background: '#070A16', borderRadius: 12, maxWidth: '100%' }}>
            <rect
              x={zone[0] * W} y={zone[1] * H} width={(zone[2] - zone[0]) * W} height={(zone[3] - zone[1]) * H}
              fill="rgba(46,107,255,0.12)" stroke={cal?.armed ? '#5CFFB0' : '#2E6BFF'} strokeDasharray="6 4" rx={10}
            />
            {cal?.hand && (
              <circle cx={cal.hand.x * W} cy={cal.hand.y * H} r={9} fill={cal.armed ? '#5CFFB0' : '#FFB35C'} />
            )}
          </svg>
          <p style={{ margin: '8px 0', opacity: 0.8 }}>
            {!cal?.on
              ? 'The camera is off.'
              : cal.calibrating
              ? `Move your hand around where it's comfortable... (${cal.samples})`
              : cal.hand
              ? `Hand seen: ${cal.hand.pose}${cal.armed ? ', gestures ready' : ''}`
              : 'Hold a hand up to the camera.'}
          </p>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {!cal?.calibrating ? (
              <button onClick={async () => setCal(await presenceCalibrate('start'))}>Set my zone</button>
            ) : (
              <button
                onClick={async () => {
                  const r = await presenceCalibrate('finish');
                  if (r) {
                    setCal(r);
                    setMsg(r.message ?? '');
                  }
                }}
              >
                Finish
              </button>
            )}
            <button onClick={async () => setCal(await presenceCalibrate('reset'))}>Reset zone</button>
            <button
              onClick={async () => {
                await presenceSwitch('off');
                onClose();
              }}
            >
              Turn the camera off
            </button>
          </div>
          {msg && <p style={{ margin: '8px 0 0' }}>{msg}</p>}
          <p style={{ margin: '10px 0 0', opacity: 0.7 }}>
            Everything happens on this computer. Pictures are never saved or sent. Today's screen time:{' '}
            {status?.screen_time_today_min ?? 0} min.
          </p>
        </div>
        <div style={{ flex: '1 1 260px' }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 16 }}>Settings</h3>
          {SWITCHES.map(([key, label]) => (
            <label key={key} style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '4px 0' }}>
              <input type="checkbox" checked={Boolean(cfg[key])} onChange={() => flip(key)} />
              {label}
            </label>
          ))}
          <h3 style={{ margin: '14px 0 6px', fontSize: 16 }}>Gestures</h3>
          {HELP.map(([g, does]) => (
            <div key={g} style={{ margin: '3px 0' }}>
              <b style={{ fontWeight: 600 }}>{g}</b>: {does}
            </div>
          ))}
          <button onClick={onClose} style={{ marginTop: 12 }}>Done</button>
        </div>
      </div>
    </div>
  );
}
