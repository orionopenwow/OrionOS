/**
 * Games (HUD menu > More > Games): the games Orion found on this PC's Windows drive and here on OrionOS, with one
 * click to set up a launcher (Steam, Heroic for Epic, Lutris for Battle.net, your WoW 1.12 client), copy a game off
 * the Windows drive so it isn't downloaded again, or play.
 * Data: GET /games, POST /games/do {action, id} (apps/daemon/games_routes.py -> os/lab/bin/orion-games).
 * Setting up and copying open a terminal that asks first; nothing installs on its own.
 */
import { useCallback, useEffect, useState } from 'react';
import { Gamepad2 } from 'lucide-react';
import GlassWindow from '../glass/GlassWindow';
import { ORION_HTTP_URL } from '../../lib/assets';

interface Game {
  id: string;
  name: string;
  store: string;
  where: 'windows' | 'here';
  note?: string;
  reuse?: string;
  copied?: boolean;
}
interface Launcher {
  name: string;
  for: string;
  installed: boolean;
}
interface GamesData {
  ok: boolean;
  message?: string;
  windows?: Game[];
  here?: Game[];
  launchers?: Record<string, Launcher>;
  general_note?: string;
  windows_drive?: { present: boolean; open: boolean; bitlocker: boolean };
}

const SETUP: Record<string, string> = {
  steam: 'setup-steam', heroic: 'setup-heroic', battlenet: 'setup-battlenet', wow112: 'setup-wow112',
};

const pill = { background: 'rgba(46,107,255,0.25)', border: '1px solid #3A4352' };

export default function GamesWindow({ onClose, mobile }: { onClose: () => void; mobile?: boolean }) {
  const [d, setD] = useState<GamesData | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${ORION_HTTP_URL}/games`);
      setD(r.ok ? ((await r.json()) as GamesData) : { ok: false, message: "Orion's brain didn't answer." });
    } catch {
      setD({ ok: false, message: "Orion's brain isn't running." });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const act = async (action: string, id = '') => {
    setBusy(true);
    try {
      const r = await fetch(`${ORION_HTTP_URL}/games/do`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ action, id }),
      });
      const j = (await r.json()) as { message?: string };
      setNote(j.message ?? '');
    } catch {
      setNote("Couldn't reach Orion.");
    } finally {
      setBusy(false);
    }
  };

  const row = (g: Game) => (
    <div key={`${g.where}-${g.id}`} className="flex items-start gap-3 rounded-xl px-3 py-2 hover:bg-white/[0.04]">
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-semibold text-[#DDE5F0]">{g.name}</span>
        {g.where === 'windows' && g.reuse && (
          <span className="block text-[11.5px] leading-snug text-[#DDE5F0]/76">{g.copied ? 'Already copied here.' : g.reuse}</span>
        )}
        {g.note && <span className="block text-[11.5px] leading-snug text-[#DDE5F0]/72">{g.note}</span>}
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={() => void act(g.where === 'here' ? 'play' : 'import', g.id)}
        className="shrink-0 rounded-full px-3 py-1 text-[12px] font-semibold text-white disabled:opacity-40"
        style={pill}
      >
        {g.where === 'here' ? 'Play' : 'Copy here'}
      </button>
    </div>
  );

  const wd = d?.windows_drive;
  return (
    <GlassWindow
      id="games"
      title="Games"
      subtitle="The games you already have"
      icon={<Gamepad2 size={14} className="text-[#5CFFB0]" />}
      onClose={onClose}
      maximisable={false}
      mobile={mobile}
      className="left-4 bottom-[88px] w-[460px] max-w-[calc(100vw-32px)]"
    >
      <div className="p-3 space-y-2 max-h-[64vh] overflow-y-auto">
        {!d && <div className="px-3 text-[13px] text-[#DDE5F0]/85">Looking for your games…</div>}
        {d && !d.ok && <div className="px-3 text-[13px] text-[#DDE5F0]/85">{d.message}</div>}
        {note && <div className="px-3 text-[12px] text-[#FFC66B]">{note}</div>}
        {d?.ok && (
          <>
            <div className="flex flex-wrap gap-2 px-1">
              {Object.entries(d.launchers ?? {}).map(([k, l]) => (
                <button
                  key={k}
                  type="button"
                  disabled={busy}
                  title={l.for}
                  onClick={() => void act(SETUP[k] ?? 'menu')}
                  className="rounded-full px-3 py-1 text-[12px] text-white disabled:opacity-40"
                  style={pill}
                >
                  {l.installed ? l.name : `Set up ${l.name}`}
                </button>
              ))}
              <button type="button" disabled={busy} onClick={() => void act('setup-extras')}
                className="rounded-full px-3 py-1 text-[12px] text-white disabled:opacity-40" style={pill}>
                GameMode + MangoHud
              </button>
            </div>
            {wd?.present && !wd.open && (
              <div className="flex items-center gap-3 rounded-xl px-3 py-2" style={{ background: 'rgba(221,229,240,0.05)' }}>
                <span className="flex-1 text-[12px] text-[#DDE5F0]/80">
                  This PC has a Windows drive. Open it (read-only{wd.bitlocker ? '; BitLocker asks for its recovery key' : ''}) to find games on it.
                </span>
                <button type="button" disabled={busy} onClick={() => void act('menu')}
                  className="rounded-full px-3 py-1 text-[12px] font-semibold text-white" style={pill}>Open</button>
              </div>
            )}
            {(d.here ?? []).length > 0 && <div className="px-3 pt-1 text-[11.5px] uppercase tracking-wide text-[#DDE5F0]/74">Here</div>}
            {(d.here ?? []).map(row)}
            {(d.windows ?? []).length > 0 && <div className="px-3 pt-1 text-[11.5px] uppercase tracking-wide text-[#DDE5F0]/74">On the Windows drive</div>}
            {(d.windows ?? []).map(row)}
            {!(d.here ?? []).length && !(d.windows ?? []).length && (
              <div className="px-3 text-[12.5px] text-[#DDE5F0]/82">No games found yet. Set up a launcher above and sign in.</div>
            )}
            {d.general_note && <div className="px-3 pt-1 text-[11.5px] leading-snug text-[#DDE5F0]/72">{d.general_note}</div>}
            <div className="px-3">
              <button type="button" onClick={() => void load()} className="text-[12px] text-[#8fc7ff] underline">Look again</button>
            </div>
          </>
        )}
      </div>
    </GlassWindow>
  );
}
