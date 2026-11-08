/**
 * Drives the black hole's smart reactions in the HUD: daemon events + input + local time + calm motion go
 * through the one reducer (reactions.ts), and the eased result is pushed to every renderer as signals and bodies.
 *
 * Runs at frame rate only while something moves (a comet, an orbiting star, a value easing); otherwise it checks
 * about once a second (idle show, time of day). While the page is hidden or a full-screen game is in front it
 * only keeps its state; the renderer itself is stopped.
 *
 *   const { orbitTotal } = useCoreReactions();   // mount once (the HUD core)
 *   feedTheHole();                               // a file was dropped on the core
 */
import { useEffect, useState } from 'react';
import { calmMotionNow, useCalmMotion } from '../hudPrefs';
import { onDaemonEvent } from '../daemonEvents';
import { BODY_SLOTS } from './renderer';
import { setBlackHoleBodies, setBlackHoleSignals } from './signals';
import {
  initialReactions,
  isAnimating,
  reactionBodies,
  reactionSignals,
  reactionsReducer,
  type CometSize,
  type CoreSignalsMessage,
  type ReactionAction,
  type ReactionState,
} from './reactions';

let dispatchHook: ((a: ReactionAction) => void) | null = null;

const nowS = () => performance.now() / 1000;
const localHour = () => {
  const d = new Date();
  return d.getHours() + d.getMinutes() / 60;
};

/** The disk flares as it swallows a dropped file (the vault confirms with a second, daemon-sent flare). */
export function feedTheHole(strength = 0.6) {
  dispatchHook?.({ type: 'feed', strength, now: nowS() });
}

export function useCoreReactions(): { orbitTotal: number } {
  const calm = useCalmMotion();
  const [orbitTotal, setOrbitTotal] = useState(0);

  useEffect(() => {
    let s: ReactionState = initialReactions(nowS(), localHour(), calmMotionNow());
    let raf = 0;
    let timer = 0;
    let stopped = false;

    const publish = () => {
      setBlackHoleSignals(reactionSignals(s));
      setBlackHoleBodies(reactionBodies(s, BODY_SLOTS));
      setOrbitTotal(s.orbitTotal);
    };
    const cancel = () => {
      if (raf) cancelAnimationFrame(raf);
      if (timer) window.clearTimeout(timer);
      raf = 0;
      timer = 0;
    };
    const schedule = () => {
      if (stopped) return;
      cancel();
      if (!document.hidden && !s.paused && isAnimating(s)) raf = requestAnimationFrame(loop);
      else timer = window.setTimeout(loop, 1000);
    };
    const loop = () => {
      raf = 0;
      timer = 0;
      s = reactionsReducer(s, { type: 'tick', now: nowS(), hour: localHour() });
      publish();
      schedule();
    };
    const dispatch = (a: ReactionAction) => {
      const before = s;
      s = reactionsReducer(s, a);
      if (s === before) return;
      // input only matters when it ends the idle show; everything else should show up right away
      if (a.type !== 'input' || before.level.tilt > 0) loop();
    };
    dispatchHook = dispatch;

    const offEvents = onDaemonEvent((ev) => {
      if (ev.event === 'CORE_SIGNALS') {
        dispatch({ type: 'signals', msg: ev as unknown as CoreSignalsMessage, now: nowS() });
      } else if (ev.event === 'CORE_EVENT') {
        if (ev.kind === 'comet') {
          const size = (['ember', 'normal', 'tiny'].includes(String(ev.size)) ? ev.size : 'normal') as CometSize;
          dispatch({ type: 'comet', size, count: Number(ev.count) || 1, now: nowS() });
        } else if (ev.kind === 'orbit' && (ev.outcome === 'approve' || ev.outcome === 'reject')) {
          dispatch({ type: 'resolve', id: String(ev.id), outcome: ev.outcome, now: nowS() });
        } else if (ev.kind === 'feed') {
          dispatch({ type: 'feed', strength: 0.8, now: nowS() });
        }
      }
    });

    const onInput = () => dispatch({ type: 'input', now: nowS() });
    const inputs = ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart'] as const;
    for (const e of inputs) window.addEventListener(e, onInput, { passive: true });
    const onVisible = () => schedule();
    document.addEventListener('visibilitychange', onVisible);

    // demo and dev builds: a handle for screenshots and trying the reactions by hand
    if (import.meta.env.DEV || import.meta.env.MODE === 'demo') {
      (window as unknown as { __orionCore?: unknown }).__orionCore = {
        dispatch: (a: ReactionAction) => dispatch({ ...a, now: (a as { now?: number }).now ?? nowS() } as ReactionAction),
        state: () => s,
      };
    }

    loop();
    return () => {
      stopped = true;
      cancel();
      offEvents();
      for (const e of inputs) window.removeEventListener(e, onInput);
      document.removeEventListener('visibilitychange', onVisible);
      if (dispatchHook === dispatch) dispatchHook = null;
      setBlackHoleSignals({
        thinking: 0, heat: 0, turbulence: 0, dim: 0, ring2: 0, quiet: 0, tilt: 0, night: 0, dawn: 0, flare: 0, paused: 0,
      });
      setBlackHoleBodies(new Float32Array(0));
    };
  }, []);

  useEffect(() => {
    dispatchHook?.({ type: 'calm', on: calm });
  }, [calm]);

  return { orbitTotal };
}
