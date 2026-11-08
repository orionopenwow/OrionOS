import { useEffect, useState } from 'react';
import { Link2, Send } from 'lucide-react';
import { ORION_HTTP_URL } from '../../lib/assets';
import { colors } from '../../theme/colors';

/**
 * One-time pairing codes (review B1 / B2), shown in the Control drawer.
 *  - Browser extension: type the code into the Orion Bridge popup (apps/daemon/extension_pairing.py).
 *  - Telegram: send "/pair CODE" to your Orion bot; Orion then only obeys that chat (telegram_bridge.py).
 * Codes are short-lived and die after a few wrong tries. Also on the terminal: orion-ctl extension|telegram pair.
 */
type Shown = { code: string; until: number } | null;

async function post(path: string): Promise<{ ok: boolean; data: Record<string, unknown> }> {
  try {
    const r = await fetch(`${ORION_HTTP_URL}${path}`, { method: 'POST' });
    return { ok: r.ok, data: await r.json().catch(() => ({})) };
  } catch {
    return { ok: false, data: { error: "Orion's brain isn't answering." } };
  }
}

function Code({ shown, now }: { shown: Shown; now: number }) {
  if (!shown || now >= shown.until) return null;
  const left = Math.max(0, Math.round((shown.until - now) / 1000));
  return (
    <div className="mt-2 flex items-center justify-between rounded-md border border-glass bg-black/30 px-3 py-2">
      <span className="font-mono text-lg tracking-[0.25em]" style={{ color: colors.cyan }}>{shown.code}</span>
      <span className="text-[11px] text-dust">{left >= 60 ? `${Math.ceil(left / 60)} min` : `${left} s`} left</span>
    </div>
  );
}

export default function PairingPanel({ connected }: { connected: boolean }) {
  const [ext, setExt] = useState<Shown>(null);
  const [tg, setTg] = useState<Shown>(null);
  const [tgPaired, setTgPaired] = useState(false);
  const [note, setNote] = useState('');
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!ext && !tg) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [ext, tg]);

  useEffect(() => {
    if (!connected) return;
    fetch(`${ORION_HTTP_URL}/telegram/pair`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setTgPaired(Boolean(d?.paired)))
      .catch(() => {});
  }, [connected, tg]);

  const pairExtension = async () => {
    const { ok, data } = await post('/extension/pair/start');
    setNote(ok ? 'Type this code into the Orion Bridge popup in your browser.' : String(data.error ?? 'That did not work.'));
    if (ok) setExt({ code: String(data.code), until: Date.now() + Number(data.expires_in ?? 120) * 1000 });
  };

  const pairTelegram = async () => {
    const { ok, data } = await post('/telegram/pair');
    setNote(ok ? `Send /pair ${String(data.code)} to your Orion bot in Telegram.` : String(data.error ?? 'That did not work.'));
    if (ok) setTg({ code: String(data.code), until: Date.now() + Number(data.expires_in ?? 600) * 1000 });
  };

  const row = 'rounded-lg border border-glass bg-white/[0.02] px-3 py-2 text-sm';
  const btn = 'rounded-md border border-glass px-2 py-1 text-[11px] text-starlight hover:bg-white/5 disabled:opacity-40';
  return (
    <section>
      <div className="panel-title mb-2">Pair</div>
      <div className="space-y-1.5">
        <div className={row}>
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-2"><Link2 size={14} className="text-nebula" /> Browser extension</span>
            <button type="button" className={btn} disabled={!connected} onClick={pairExtension}>Show code</button>
          </div>
          <Code shown={ext} now={now} />
        </div>
        <div className={row}>
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-2">
              <Send size={14} className="text-nebula" /> Telegram{tgPaired ? <span className="text-[11px] text-dust">· paired</span> : null}
            </span>
            <button type="button" className={btn} disabled={!connected} onClick={pairTelegram}>
              {tgPaired ? 'Pair again' : 'Show code'}
            </button>
          </div>
          <Code shown={tg} now={now} />
        </div>
        {note ? <div className="px-1 text-[11px] text-dust">{note}</div> : null}
      </div>
    </section>
  );
}
