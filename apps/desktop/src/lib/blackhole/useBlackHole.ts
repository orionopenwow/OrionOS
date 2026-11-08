/**
 * React glue: mount a BlackHoleRenderer on a canvas, keep it fed with a preset + the live signals, and pick
 * the quality tier automatically (GPU, battery, reduced motion, Settings override).
 *
 *   const { canvasRef, ok, renderer } = useBlackHole({ preset, transparent: true });
 *   return ok ? <canvas ref={canvasRef} /> : <img src={painting} />;
 */
import { useEffect, useRef, useState } from 'react';
import { BlackHoleRenderer, supportsWebGL2, type BlackHoleUniforms } from './renderer';
import {
  prefersReducedMotion,
  QUALITY_EVENT,
  QUALITY_STORAGE_KEY,
  readQualitySetting,
  resolveQuality,
  watchBattery,
  type BlackHoleQuality,
} from './quality';
import {
  applySignals,
  getBlackHoleBodies,
  getBlackHoleSignals,
  subscribeBlackHoleBodies,
  subscribeBlackHoleSignals,
  type BlackHoleSignals,
} from './signals';

export interface UseBlackHoleOptions {
  preset: BlackHoleUniforms;
  diskUrl?: string;
  transparent?: boolean;
  halfResolution?: boolean;
  /** Force a tier (otherwise Settings/auto). */
  quality?: BlackHoleQuality;
  /** Play the boot timeline when mounted. */
  boot?: boolean;
}

export function useBlackHole(opts: UseBlackHoleOptions) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rendererRef = useRef<BlackHoleRenderer | null>(null);
  const presetRef = useRef(opts.preset);
  presetRef.current = opts.preset;
  const [ok, setOk] = useState(() => supportsWebGL2());
  const [quality, setQuality] = useState<BlackHoleQuality>(() => opts.quality ?? resolveQuality());

  // mount / unmount
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!ok || !canvas) return;
    const setting = readQualitySetting();
    const initial = opts.quality ?? resolveQuality(setting);
    const r = BlackHoleRenderer.create(canvas, {
      diskUrl: opts.diskUrl ?? `${import.meta.env.BASE_URL}black-hole-core.png`,
      quality: initial,
      transparent: opts.transparent,
      halfResolution: opts.halfResolution,
      bootFrom: opts.boot ? 0 : undefined,
      autoDowngrade: opts.quality === undefined && setting === 'auto',
      onFailure: () => setOk(false),
      onQualityChange: setQuality,
    });
    if (!r) {
      setOk(false);
      return;
    }
    rendererRef.current = r;
    r.set(applySignals(presetRef.current, getBlackHoleSignals()));
    r.setBodies(getBlackHoleBodies());
    // a full-screen game in front (daemon signal): stop drawing until it's gone
    let paused = false;
    const followPause = (s: Readonly<BlackHoleSignals>) => {
      const p = s.paused >= 0.5;
      if (p === paused) return;
      paused = p;
      if (p) r.stop();
      else r.start();
    };
    r.start();
    followPause(getBlackHoleSignals());
    setQuality(r.quality);
    const unsubBodies = subscribeBlackHoleBodies((b) => r.setBodies(b));

    let lastSlow = '';
    const unsub = subscribeBlackHoleSignals((s) => {
      followPause(s);
      const u = applySignals(presetRef.current, s);
      const { voice: _v, ripple: _r, ...slow } = u;
      const key = JSON.stringify(slow);
      // Still tier: never redraw at mic rate; only real changes of mood or state repaint the frame
      if (r.quality === 0) {
        if (key !== lastSlow) r.setTarget(slow);
        lastSlow = key;
        return;
      }
      lastSlow = key;
      r.setTarget(u);
      r.set({ voice: u.voice, ripple: u.ripple }); // already smoothed at the source: no extra lag
    });

    // re-evaluate when the OS motion preference or the Settings value changes
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const reevaluate = () => {
      if (opts.quality !== undefined) return;
      const q = resolveQuality();
      r.setQuality(q);
      setQuality(r.quality); // Ultra may have settled on High if its shader can't run here
    };
    mq?.addEventListener?.('change', reevaluate);
    const onStorage = (e: StorageEvent) => {
      if (e.key === QUALITY_STORAGE_KEY) reevaluate();
    };
    window.addEventListener('storage', onStorage);
    window.addEventListener(QUALITY_EVENT, reevaluate);
    // on battery below 50 %: Battery saver (only in auto, and never upgrades past what auto chose)
    const stopBattery = watchBattery((save) => {
      if (opts.quality !== undefined || readQualitySetting() !== 'auto' || prefersReducedMotion()) return;
      const q = save ? (Math.min(1, resolveQuality()) as BlackHoleQuality) : resolveQuality();
      r.setQuality(q);
      setQuality(r.quality); // Ultra may have settled on High if its shader can't run here
    });

    return () => {
      unsub();
      unsubBodies();
      mq?.removeEventListener?.('change', reevaluate);
      window.removeEventListener('storage', onStorage);
      window.removeEventListener(QUALITY_EVENT, reevaluate);
      stopBattery();
      r.dispose();
      rendererRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ok, opts.transparent, opts.halfResolution, opts.quality, opts.diskUrl]);

  // preset changes glide
  useEffect(() => {
    rendererRef.current?.setTarget(applySignals(opts.preset, getBlackHoleSignals()));
  }, [opts.preset]);

  return { canvasRef, ok, quality, renderer: rendererRef };
}
