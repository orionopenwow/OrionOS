/** Small, quiet label shown only in the GitHub Pages preview: "Preview — sample data". Compact on phones. */
import { useState } from 'react';
import { useIsMobile } from '../lib/device';
import { TOUR_START_EVENT } from './Tour';

export default function DemoBanner() {
  const [open, setOpen] = useState(true);
  const mobile = useIsMobile();
  // ?shot=1 hides it for the README screenshots (scripts/capture-screenshots.mjs)
  const shot = new URLSearchParams(window.location.search).get('shot') === '1';
  const embedded = window.self !== window.top; // inside the website's Preview tab, which has its own label
  if (!open || shot || embedded) return null;
  const startTour = () => window.dispatchEvent(new Event(TOUR_START_EVENT));
  return (
    <div
      role="note"
      aria-label="Preview with sample data"
      className={`fixed left-1/2 -translate-x-1/2 z-[9999] flex items-center gap-2.5 rounded-full border border-white/10 bg-[#05070E]/70 backdrop-blur-xl text-[#DDE5F0]/85 ${
        mobile ? 'top-[max(8px,env(safe-area-inset-top))] max-w-[calc(100vw-24px)] px-3 py-1 text-[11.5px]' : 'top-2.5 max-w-[calc(100vw-32px)] px-3.5 py-1 text-[12px]'
      }`}
      style={{ fontFamily: 'Figtree Variable, Figtree, system-ui, sans-serif' }}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-[#2E6BFF] shrink-0" aria-hidden="true" />
      <span className="truncate">
        Preview — sample data
        {!mobile && <span className="text-[#DDE5F0]/74"> · runs only in your browser · click the round Orion button to start</span>}
      </span>
      <button type="button" onClick={startTour} className="shrink-0 font-semibold text-[#DDE5F0] hover:text-[#00E5FF] bg-transparent border-0">
        Tour
      </button>
      <a href="../" className="shrink-0 font-semibold text-[#00E5FF] hover:text-white">
        About OrionOS
      </a>
      <button type="button" onClick={() => setOpen(false)} aria-label="Hide label" className="shrink-0 text-[#DDE5F0]/68 hover:text-white bg-transparent border-0">
        ✕
      </button>
    </div>
  );
}
