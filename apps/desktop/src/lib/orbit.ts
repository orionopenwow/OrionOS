/**
 * Look pass: things that orbit the core (VIP satellites, agent robots) turn with a CSS variable instead of an
 * endless animation, so the orbit can ease to a stop while the pointer rests on one of them ("hover to make
 * things bigger and stabilize"). Children counter-rotate with `rotate(calc(var(--orbit) * -1))`.
 */
import { useEffect, type RefObject } from 'react';

export function useOrbit(el: RefObject<HTMLElement>, hold: RefObject<boolean>, periodSec: number, still: boolean, deps: unknown[] = []) {
  useEffect(() => {
    if (still) {
      el.current?.style.setProperty('--orbit', '0rad');
      return;
    }
    let raf = 0;
    let last = performance.now();
    let angle = 0;
    let speed = 1;
    const step = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      speed += ((hold.current ? 0 : 1) - speed) * Math.min(1, dt * 5);
      angle = (angle + dt * speed * ((Math.PI * 2) / periodSec)) % (Math.PI * 2);
      el.current?.style.setProperty('--orbit', `${angle.toFixed(5)}rad`);
      raf = document.hidden ? 0 : requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    const onVis = () => {
      if (!document.hidden && !raf) {
        last = performance.now();
        raf = requestAnimationFrame(step);
      }
    };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('visibilitychange', onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [still, periodSec, ...deps]);
}

export const COUNTER_ROTATE = { transform: 'rotate(calc(var(--orbit, 0rad) * -1))' } as const;
