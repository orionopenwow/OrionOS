import React, { useState } from 'react';

export interface RealmLevelData {
  currentLevel: number;
  currentXpUsd: number;
  nextLevelXpUsd: number;
  kofiTotalUsd: number;
  bitcoinTotalUsd: number;
  openSourceAllocatedUsd: number;
  nextMilestonePerks: string[];
  vendorStockHighlights: string[];
  approvedGitHubProjects: { name: string; url: string; approved: boolean; grantUsd: number }[];
}

const DEFAULT_REALM_DATA: RealmLevelData = {
  currentLevel: 4,
  currentXpUsd: 1840,
  nextLevelXpUsd: 2500,
  kofiTotalUsd: 1280,
  bitcoinTotalUsd: 560,
  openSourceAllocatedUsd: 460, // 25% altruistic tithe
  nextMilestonePerks: [
    '250 In-Game Stardust granted to ALL active player accounts',
    'Free Universal Dual-Spec Talent Switching across the entire realm',
    'Hunter starter pets & AoE area looting enabled by default',
    'Vaelis the Starwright Stock: Celestial Hippogryph & Stardust Robes',
    'Altruism Grant: $250 disbursed to CMaNGOS & Benilla core maintainers',
  ],
  vendorStockHighlights: [
    'Celestial Hippogryph (100% Speed Mount — 500 DUST)',
    'Cosmic Stardust Tabard (Cosmetic Aura — 150 DUST)',
    'Pet: Miniature Void Walker Companion (100 DUST)',
    'Transmog: Pauldrons of the Starwatcher (200 DUST)',
  ],
  approvedGitHubProjects: [
    { name: 'cmangos/mangos-classic', url: 'https://github.com/cmangos/mangos-classic', approved: true, grantUsd: 200 },
    { name: 'vmangos/core', url: 'https://github.com/vmangos/core', approved: true, grantUsd: 150 },
    { name: 'benilla/benilla', url: 'https://github.com/benilla/benilla', approved: true, grantUsd: 110 },
  ],
};

export const RealmExpBar: React.FC<{ data?: RealmLevelData; className?: string }> = ({
  data = DEFAULT_REALM_DATA,
  className = '',
}) => {
  const [showTooltip, setShowTooltip] = useState(false);
  const [showAltruismModal, setShowAltruismModal] = useState(false);

  const pct = Math.min(100, Math.max(0, (data.currentXpUsd / data.nextLevelXpUsd) * 100));
  const ticks = 20; // Authentic classic WoW XP bar has 20 ticks

  return (
    <div className={`relative flex flex-col items-center select-none ${className}`}>
      {/* Top micro-banner */}
      <div className="flex items-center gap-3 text-xs tracking-wider mb-1">
        <span className="text-[#a4a0c8] font-medium drop-shadow-sm">
          Press PLAY to install World of Warcraft 1.12.1 or pick your folder.
        </span>
        <button
          onClick={() => setShowAltruismModal(true)}
          className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-[#1b1933]/80 border border-[#a87f32]/40 hover:border-[#ffd100] text-[#ffdd55] text-[10px] font-semibold transition-all shadow-sm"
          title="Click to view open-source grants and funding transparency"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-[#3DFFA8] animate-pulse" />
          Ko-fi & BTC Powered • 25% Open-Source GitHub Tithe
        </button>
      </div>

      {/* The Central Bar Container with Golden Dragons */}
      <div className="relative flex items-center justify-center w-full max-w-[840px] h-[52px]">
        {/* Left Golden Dragon Head */}
        <div className="relative z-20 flex-shrink-0 -mr-3 transform -scale-x-100 pointer-events-none drop-shadow-[0_4px_8px_rgba(0,0,0,0.8)]">
          <svg width="68" height="52" viewBox="0 0 100 80" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M15 65 C25 60, 35 45, 45 40 C55 35, 70 25, 85 15 C88 22, 85 30, 80 35 C88 38, 95 38, 98 42 C90 48, 80 50, 72 52 C78 56, 85 58, 88 64 C75 66, 62 62, 55 58 C45 68, 30 72, 15 65 Z"
              fill="url(#goldGrad)"
              stroke="#432808"
              strokeWidth="2"
            />
            <path d="M48 38 C58 30, 72 26, 80 18" stroke="#ffe891" strokeWidth="2" strokeLinecap="round" />
            <path d="M52 48 C64 45, 74 44, 82 42" stroke="#ffe891" strokeWidth="1.5" strokeLinecap="round" />
            <circle cx="75" cy="30" r="3" fill="#ffe259" stroke="#5a3800" strokeWidth="1" />
            <defs>
              <linearGradient id="goldGrad" x1="0" y1="0" x2="100" y2="80" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#fff3b0" />
                <stop offset="35%" stopColor="#d4af37" />
                <stop offset="70%" stopColor="#aa7c11" />
                <stop offset="100%" stopColor="#573d08" />
              </linearGradient>
            </defs>
          </svg>
        </div>

        {/* Central WoW XP Bar Frame */}
        <div
          className="relative z-10 flex-1 h-[32px] rounded-[16px] bg-[#0c0a17] border-[2px] border-[#927027] shadow-[inset_0_2px_4px_rgba(0,0,0,0.9),0_0_12px_rgba(212,175,55,0.25)] overflow-hidden cursor-pointer group"
          onMouseEnter={() => setShowTooltip(true)}
          onMouseLeave={() => setShowTooltip(false)}
          onClick={() => setShowTooltip(!showTooltip)}
        >
          {/* Inner metallic beveled rim */}
          <div className="absolute inset-0 rounded-[14px] border border-[#2b2447] pointer-events-none z-20" />

          {/* 20 Notched Tick Marks */}
          <div className="absolute inset-0 flex z-20 pointer-events-none">
            {Array.from({ length: ticks - 1 }).map((_, i) => (
              <div key={i} className="flex-1 border-r border-[#000000]/70 relative">
                <div className="absolute top-0 right-0 w-[1px] h-full bg-[#ffd100]/20" />
              </div>
            ))}
            <div className="flex-1" />
          </div>

          {/* Authentic WoW Purple/Violet Rested-XP Fill */}
          <div
            className="absolute top-0 bottom-0 left-0 bg-gradient-to-r from-[#3e1261] via-[#7b2cbf] to-[#9d4edd] shadow-[0_0_15px_rgba(157,78,221,0.8)] transition-all duration-700 ease-out z-10"
            style={{ width: `${pct}%` }}
          >
            {/* Liquid shimmer overlay */}
            <div className="w-full h-full opacity-40 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-white/40 via-transparent to-transparent" />
          </div>

          {/* Text Overlay: Friz Quadrata Style Fantasy Typography */}
          <div className="absolute inset-0 flex items-center justify-center z-30 pointer-events-none">
            <span
              className="text-[#ffd100] text-[13px] font-bold tracking-wider uppercase drop-shadow-[0_2px_2px_#000000]"
              style={{ fontFamily: 'Georgia, "Times New Roman", serif' }}
            >
              Realm Level {data.currentLevel} — ${data.currentXpUsd.toLocaleString()} / $
              {data.nextLevelXpUsd.toLocaleString()} XP ({pct.toFixed(1)}%)
            </span>
          </div>
        </div>

        {/* Right Golden Dragon Head */}
        <div className="relative z-20 flex-shrink-0 -ml-3 pointer-events-none drop-shadow-[0_4px_8px_rgba(0,0,0,0.8)]">
          <svg width="68" height="52" viewBox="0 0 100 80" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M15 65 C25 60, 35 45, 45 40 C55 35, 70 25, 85 15 C88 22, 85 30, 80 35 C88 38, 95 38, 98 42 C90 48, 80 50, 72 52 C78 56, 85 58, 88 64 C75 66, 62 62, 55 58 C45 68, 30 72, 15 65 Z"
              fill="url(#goldGradRight)"
              stroke="#432808"
              strokeWidth="2"
            />
            <path d="M48 38 C58 30, 72 26, 80 18" stroke="#ffe891" strokeWidth="2" strokeLinecap="round" />
            <path d="M52 48 C64 45, 74 44, 82 42" stroke="#ffe891" strokeWidth="1.5" strokeLinecap="round" />
            <circle cx="75" cy="30" r="3" fill="#ffe259" stroke="#5a3800" strokeWidth="1" />
            <defs>
              <linearGradient id="goldGradRight" x1="0" y1="0" x2="100" y2="80" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#fff3b0" />
                <stop offset="35%" stopColor="#d4af37" />
                <stop offset="70%" stopColor="#aa7c11" />
                <stop offset="100%" stopColor="#573d08" />
              </linearGradient>
            </defs>
          </svg>
        </div>
      </div>

      {/* Classic WoW Hover Tooltip: Next Milestone Unlocks & Anti-P2W Guarantee */}
      {showTooltip && (
        <div className="absolute bottom-[60px] z-50 w-[420px] p-4 rounded-lg bg-[#0d0b1a]/95 border-2 border-[#c69b32] shadow-[0_10px_30px_rgba(0,0,0,0.95)] text-left backdrop-blur-md animate-in fade-in zoom-in-95 duration-200">
          <div className="flex items-center justify-between border-b border-[#ffd100]/30 pb-2 mb-2.5">
            <div>
              <span className="text-[#ffd100] text-sm font-bold uppercase tracking-wider block font-serif">
                Realm Milestone: Level {data.currentLevel + 1}
              </span>
              <span className="text-[#a7a2c7] text-[11px]">
                Progress: ${data.currentXpUsd} of ${data.nextLevelXpUsd} USD (Ko-fi & Bitcoin)
              </span>
            </div>
            <span className="px-2 py-0.5 rounded bg-[#9d4edd]/30 border border-[#9d4edd] text-[#e0aaff] text-xs font-bold">
              {pct.toFixed(0)}% Complete
            </span>
          </div>

          <div className="text-xs text-[#d6c7ff] mb-2 font-medium">Unlocks Globally for Every Player at Level 5:</div>
          <ul className="space-y-1.5 mb-3 text-[11px] text-[#f4effa]">
            {data.nextMilestonePerks.map((perk, idx) => (
              <li key={idx} className="flex items-start gap-1.5">
                <span className="text-[#ffd100] flex-shrink-0">★</span>
                <span>{perk}</span>
              </li>
            ))}
          </ul>

          <div className="border-t border-[#46396e]/60 pt-2 flex items-center justify-between text-[10px] text-[#8e85b0]">
            <span>Vaelis the Starwright cosmic vendor restocks</span>
            <span className="text-[#3DFFA8] font-semibold">100% Non-Pay-to-Win</span>
          </div>
        </div>
      )}

      {/* Altruism & Open-Source Projects Modal */}
      {showAltruismModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-xl bg-[#0f0d1e] border-2 border-[#d4af37] shadow-[0_15px_40px_rgba(0,0,0,0.9)] p-6 text-left relative">
            <button
              onClick={() => setShowAltruismModal(false)}
              className="absolute top-4 right-4 text-[#8a84a6] hover:text-white text-lg font-bold"
            >
              ✕
            </button>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xl">🌟</span>
              <h3 className="text-lg font-bold text-[#ffd100] font-serif">OpenWoW Realm Altruism & Funding Pools</h3>
            </div>
            <p className="text-xs text-[#a9a3ca] mb-4">
              Community contributions to Ko-fi and Bitcoin are strictly non-custodial and partitioned into public buckets.
              Every level-up triggers automated Stardust rewards and upstream open-source donations.
            </p>

            {/* Financial Breakdown */}
            <div className="grid grid-cols-3 gap-2 mb-4 p-3 rounded-lg bg-[#18152b] border border-[#3b335c] text-center">
              <div>
                <div className="text-[10px] text-[#8e87b2]">Ko-fi Donations</div>
                <div className="text-sm font-bold text-[#ffdd55]">${data.kofiTotalUsd}</div>
              </div>
              <div>
                <div className="text-[10px] text-[#8e87b2]">Bitcoin On-Chain</div>
                <div className="text-sm font-bold text-[#f7931a]">${data.bitcoinTotalUsd}</div>
              </div>
              <div>
                <div className="text-[10px] text-[#8e87b2]">25% Open Source Tithe</div>
                <div className="text-sm font-bold text-[#3DFFA8]">${data.openSourceAllocatedUsd}</div>
              </div>
            </div>

            {/* Approved Open Source Repos */}
            <div className="mb-4">
              <div className="text-xs font-semibold text-[#f0e6b2] mb-2 flex items-center justify-between">
                <span>Manually Approved Upstream GitHub Projects:</span>
                <span className="text-[10px] text-[#3DFFA8]">Boardroom Verified [✓]</span>
              </div>
              <div className="space-y-1.5">
                {data.approvedGitHubProjects.map((p, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between p-2 rounded bg-[#131124] border border-[#2e274a] text-xs"
                  >
                    <div>
                      <a
                        href={p.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[#9d4edd] hover:underline font-mono text-[11px]"
                      >
                        {p.name}
                      </a>
                    </div>
                    <span className="px-2 py-0.5 rounded bg-[#3DFFA8]/10 text-[#3DFFA8] text-[10px] font-bold">
                      ${p.grantUsd} USD Earmarked
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Close Button */}
            <div className="flex justify-end pt-2 border-t border-[#31294d]">
              <button
                onClick={() => setShowAltruismModal(false)}
                className="px-4 py-1.5 rounded bg-[#d4af37] hover:bg-[#ffe066] text-[#1a1403] font-bold text-xs transition-colors"
              >
                Close & Return to Realm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default RealmExpBar;
