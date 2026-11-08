/**
 * Phone / small-screen detection. Orion switches to its mobile layout automatically when the screen is narrow,
 * or when it's a touch-first phone or small tablet. `?view=mobile` / `?view=desktop` forces a layout.
 */
import { useEffect, useState } from 'react';
import { isTauri } from './tauri';

const NARROW = '(max-width: 760px)';
const TOUCH_SMALL = '(pointer: coarse) and (max-width: 1024px)';

function forced(): boolean | null {
  try {
    const v = new URLSearchParams(window.location.search).get('view');
    if (v === 'mobile') return true;
    if (v === 'desktop') return false;
  } catch {
    /* ignore */
  }
  return null;
}

export function detectMobile(): boolean {
  if (typeof window === 'undefined') return false;
  const f = forced();
  if (f !== null) return f;
  const ua = navigator.userAgent || '';
  const phoneUa = /Android.+Mobile|iPhone|iPod|Windows Phone|Mobi/i.test(ua);
  return window.matchMedia(NARROW).matches || window.matchMedia(TOUCH_SMALL).matches || (phoneUa && window.innerWidth < 1100);
}

export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(detectMobile);
  useEffect(() => {
    const update = () => setMobile(detectMobile());
    const queries = [window.matchMedia(NARROW), window.matchMedia(TOUCH_SMALL)];
    queries.forEach((q) => q.addEventListener('change', update));
    window.addEventListener('resize', update);
    window.addEventListener('orientationchange', update);
    return () => {
      queries.forEach((q) => q.removeEventListener('change', update));
      window.removeEventListener('resize', update);
      window.removeEventListener('orientationchange', update);
    };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.device = mobile ? 'mobile' : 'desktop';
    // Mobile mode in the desktop app (touch laptops, small screens): take the whole screen instead of a window.
    if (isTauri() && window.matchMedia('(pointer: coarse)').matches) {
      import('@tauri-apps/api/window')
        .then(({ getCurrentWindow }) => getCurrentWindow().setFullscreen(mobile))
        .catch(() => undefined);
    }
  }, [mobile]);
  return mobile;
}
