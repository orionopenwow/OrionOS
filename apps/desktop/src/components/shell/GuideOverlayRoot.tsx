/**
 * What the Guide Mode overlay windows show on Windows (lib/guideOverlay.ts opens them; main.tsx picks this root
 * by the URL's #hash):
 *  - #guide-ring: only the ring, in a full-screen, transparent, click-through window;
 *  - #guide-card: the step card with Done / Back / Stop, in a small always-on-top window.
 */
import { useEffect, useState } from 'react';
import { makeClickThrough, onGuideState, type GuideState } from '../../lib/guideOverlay';
import { GuideCard, GuideRing } from './GuideMode';

export function isGuideOverlayHash(hash: string): boolean {
  return hash === '#guide-ring' || hash === '#guide-card';
}

export default function GuideOverlayRoot({ which }: { which: string }) {
  const [state, setState] = useState<GuideState>({ active: false });

  useEffect(() => {
    document.documentElement.style.background = 'transparent';
    document.body.style.background = 'transparent';
    if (which === '#guide-ring') void makeClickThrough();
    let off: () => void = () => {};
    void onGuideState(setState).then((u) => (off = u));
    return () => off();
  }, [which]);

  if (which === '#guide-ring') return <GuideRing state={state} scale={window.devicePixelRatio || 1} />;
  return (
    <GuideCard
      state={state}
      onChange={(s) => s && setState(s)}
      style={{ position: 'fixed', left: 8, right: 8, bottom: 8, top: 8, transform: 'none', width: 'auto' }}
    />
  );
}
