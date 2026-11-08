import { Minus, Square, X } from 'lucide-react';
import { isTauri, windowAction } from '../../lib/tauri';
import { desktop } from '../../lib/desktop';

/**
 * Frameless-window title bar for the desktop app: drag anywhere on it, double-click to maximise,
 * and X hides Orion to the system tray (Quit lives in the tray menu). Renders nothing in a browser.
 */
export default function TitleBar() {
  if (!isTauri()) return null;
  return (
    <div
      data-tauri-drag-region
      onDoubleClick={() => windowAction('toggleMaximize')}
      className="fixed inset-x-0 top-0 z-[90] flex h-8 items-center justify-between pl-3 select-none"
      style={{ background: 'linear-gradient(180deg, rgba(5,7,14,0.85), rgba(5,7,14,0))' }}
    >
      <div data-tauri-drag-region className="flex items-center gap-2 text-[11px] font-bold tracking-[0.18em] text-white/55">
        <img src={`${import.meta.env.BASE_URL}black-hole-core.png`} alt="" className="h-4 w-4 rounded-full pointer-events-none" />
        ORION
      </div>
      <div className="flex h-full">
        <button
          aria-label="Minimize"
          onClick={() => windowAction('minimize')}
          className="flex h-full w-11 items-center justify-center text-white/60 hover:bg-white/10 hover:text-white"
        >
          <Minus size={14} />
        </button>
        <button
          aria-label="Maximize"
          onClick={() => windowAction('toggleMaximize')}
          className="flex h-full w-11 items-center justify-center text-white/60 hover:bg-white/10 hover:text-white"
        >
          <Square size={11} />
        </button>
        <button
          aria-label="Hide to tray"
          title="Orion keeps running in the tray"
          onClick={() => desktop.hideWindow()}
          className="flex h-full w-11 items-center justify-center text-white/60 hover:bg-[#E81123] hover:text-white"
        >
          <X size={15} />
        </button>
      </div>
    </div>
  );
}
