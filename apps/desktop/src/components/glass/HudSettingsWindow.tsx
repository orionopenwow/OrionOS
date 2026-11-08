/** Settings window for the HUD's look & motion: graphics quality, rare sky events, parallax, depth and calm motion. */
import { useState } from 'react';
import { Settings2 } from 'lucide-react';
import GlassWindow from './GlassWindow';
import { hudPrefs, useHudPrefs, systemReducedMotion, type HudPrefs } from '../../lib/hudPrefs';
import { readQualitySetting, writeQualitySetting, type QualitySetting } from '../../lib/blackhole';

const QUALITY_CHOICES: Array<[QualitySetting, string]> = [
  ['auto', 'Automatic'],
  ['ultra', 'Ultra: real 3D black hole'],
  ['high', 'High'],
  ['balanced', 'Balanced'],
  ['battery', 'Battery saver'],
  ['still', 'Still picture'],
];

function QualityRow() {
  const [v, setV] = useState<QualitySetting>(readQualitySetting);
  return (
    <label className="flex items-start gap-3 rounded-xl px-3 py-2.5 hover:bg-white/[0.04]">
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-semibold text-[#D8DEFF]">Graphics quality</span>
        <span className="block text-[11.5px] leading-snug text-[#D8DEFF]/55">
          Automatic picks from your graphics card and battery, and steps down if it gets slow.
        </span>
      </span>
      <select
        aria-label="Graphics quality"
        value={v}
        onChange={(e) => {
          const next = e.target.value as QualitySetting;
          setV(next);
          writeQualitySetting(next);
        }}
        className="mt-0.5 shrink-0 rounded-lg border border-[rgba(216,222,255,0.14)] bg-[rgba(11,15,30,0.9)] px-2 py-1 text-[12px] text-[#D8DEFF] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#00E5FF]"
      >
        {QUALITY_CHOICES.map(([k, label]) => (
          <option key={k} value={k}>
            {label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Toggle({ k, label, hint, disabled }: { k: keyof HudPrefs; label: string; hint: string; disabled?: string }) {
  const p = useHudPrefs();
  // When the system forces reduced motion, calm motion reads "on" and the other two read "off".
  const on = disabled ? k === 'calmMotion' : p[k];
  return (
    <label className={`flex items-start gap-3 rounded-xl px-3 py-2.5 hover:bg-white/[0.04] ${disabled ? 'opacity-55' : 'cursor-pointer'}`}>
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-semibold text-[#D8DEFF]">{label}</span>
        <span className="block text-[11.5px] leading-snug text-[#D8DEFF]/55">{disabled ?? hint}</span>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={label}
        disabled={!!disabled}
        onClick={() => hudPrefs.set({ [k]: !p[k] } as Partial<HudPrefs>)}
        className="relative mt-0.5 h-[22px] w-[38px] shrink-0 rounded-full border transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#00E5FF]"
        style={{
          background: on ? 'rgba(124,92,255,0.55)' : 'rgba(216,222,255,0.08)',
          borderColor: on ? 'rgba(176,156,255,0.7)' : 'rgba(216,222,255,0.12)',
          boxShadow: on ? '0 0 12px rgba(124,92,255,0.45)' : 'none',
        }}
      >
        <span
          className="absolute top-[2px] h-4 w-4 rounded-full bg-white transition-[left] duration-200"
          style={{ left: on ? 18 : 2 }}
        />
      </button>
    </label>
  );
}

export default function HudSettingsWindow({ onClose, mobile }: { onClose: () => void; mobile?: boolean }) {
  const sysCalm = systemReducedMotion();
  return (
    <GlassWindow
      id="settings"
      title="Settings"
      subtitle="Look & motion"
      icon={<Settings2 size={14} className="text-[#B09CFF]" />}
      onClose={onClose}
      maximisable={false}
      mobile={mobile}
      className="left-4 bottom-[88px] w-[360px] max-w-[calc(100vw-32px)]"
    >
      <div className="p-2 space-y-0.5">
        <QualityRow />
        <Toggle
          k="rareSkyEvents"
          label="Rare sky events"
          hint="Now and then a shooting star, or a comet that curves around the hole. A few times an hour at most, never while you type."
          disabled={sysCalm ? 'Off while your system asks for reduced motion.' : undefined}
        />
        <Toggle
          k="parallax"
          label="Parallax space"
          hint="The stars drift slightly with your mouse (or tilt, on a tablet)."
          disabled={sysCalm ? 'Off while your system asks for reduced motion.' : undefined}
        />
        <Toggle
          k="depth"
          label="Depth"
          hint="The screen edges stay flat and space gently curves down toward the black hole."
        />
        <Toggle
          k="calmMotion"
          label="Calm motion"
          hint="Simple fades instead of windows growing, spiralling and flying in."
          disabled={sysCalm ? 'On: your system asks for reduced motion.' : undefined}
        />
        {/* [v1gaps] privacy dashboard + Guide Mode (components/shell/V1Shell.tsx) */}
        <div className="flex gap-2 px-3 pt-2 pb-1">
          {([['orion:open-privacy', 'Privacy dashboard'], ['orion:open-guide', 'Show me how to…']] as const).map(([ev, label]) => (
            <button key={ev} type="button" onClick={() => window.dispatchEvent(new Event(ev))}
              className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-[12px] font-semibold text-[#D8DEFF] hover:bg-white/[0.1]">
              {label}
            </button>
          ))}
        </div>
      </div>
    </GlassWindow>
  );
}
