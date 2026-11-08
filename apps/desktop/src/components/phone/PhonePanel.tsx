/**
 * Phone panel: connect this PC and your phone in one go, on Windows and OrionOS.
 *
 * Open it with   window.dispatchEvent(new CustomEvent('orion:open', { detail: 'phone' }))   (the main menu does this).
 *
 *  - No phone yet: a big QR code + the code. "On your phone: open your camera and point it at this." Then
 *    "Do these words match your phone?" and Confirm. If Windows Firewall would block the phone, one plain button
 *    ("Let my phone in") runs the Open Phone Access step, which asks Windows for admin permission once.
 *  - Paired: the phone's name, online / last seen, battery, "Send a test card to my phone", "Unpair".
 *
 * Data: apps/daemon/phone_pairing_ui.py (POST /phones/pair/open, GET /phones/pair/status, POST /phones/{id}/confirm,
 * POST /phones/pair/close, GET /phones, POST /phones/test-card, POST /phones/{id}/unpair, POST /phones/open-access).
 * The QR code arrives as an SVG the daemon draws itself (qr_svg.py), so nothing is fetched from the internet.
 *
 * [start-here] No phone yet and the app is being shared on the Wi-Fi (phone_app_share.py, GET /phones/app-share):
 * "1. Install Orion Mobile" with its own QR code comes first, then the pairing QR as "2.".
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import { Smartphone } from 'lucide-react';
import GlassWindow from '../glass/GlassWindow';
import { ORION_HTTP_URL } from '../../lib/assets';

export const OPEN_EVENT = 'orion:open';

interface Lan {
  ok: boolean | null;
  words: string;
  fix: 'open_access' | 'vbox_open_access' | null;
  fix_words: string;
}
interface Phone {
  device_id: string;
  name: string;
  fingerprint: string;
  words: string;
  last_seen: number | null;
  online: boolean;
  battery: number | null;
  charging?: boolean | null;
  network?: string | null;
  link?: Link;   // [phonelink]
}
// [phonelink] the link as the PC sees it (phone_link.py link_info) and the "Fix the connection" checks
interface Link {
  state: 'connected' | 'reconnecting' | 'away' | 'never';
  words: string;
  quality: 'good' | 'fair' | 'poor' | null;
  last_seen: number | null;
  rtt_ms: number | null;
  via?: string | null;
  clock_words?: string;
}
interface LinkCheck {
  id: string;
  name: string;
  ok: boolean | null;
  words: string;
  fix: string;
}
interface LinkReport {
  ok: boolean;
  say: string;
  checks: LinkCheck[];
}
interface Pending {
  device_id: string;
  name: string;
  fingerprint: string;
  words: string;
}
interface Opened {
  code: string;
  url: string;
  qr_svg: string;
  expires: number;
  address: string;
  how: string;
  lan: Lan;
}
interface AppShare {
  open: boolean;
  apk: boolean;
  url: string;
  qr_svg: string;
  until: number | null;
}
interface Status {
  open: boolean;
  expires: number | null;
  pending: Pending[];
  phones: Phone[];
  lan: Lan | null;
  pin?: string;
}

function ago(ts: number | null): string {
  if (!ts) return 'not seen yet';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const r = await fetch(`${ORION_HTTP_URL}${path}`, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : method === 'POST' ? '{}' : undefined,
  });
  const j = (await r.json().catch(() => ({}))) as T & { error?: string; detail?: string };
  if (!r.ok) throw new Error(j.error ?? (r.status === 404 ? 'Update Orion on this PC to pair from here.' : `Orion answered ${r.status}`));
  return j;
}

const btn = 'rounded-full px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-40';
const primary = { background: 'linear-gradient(90deg,#2E6BFF,#00B8D9)' };
const quiet = { background: 'rgba(46,107,255,0.18)', border: '1px solid #3A4352' };

function PhoneWindow({ onClose, mobile }: { onClose: () => void; mobile?: boolean }) {
  const [phones, setPhones] = useState<Phone[] | null>(null);
  const [pair, setPair] = useState<Opened | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [pairing, setPairing] = useState(false);   // showing the QR even though a phone is paired ("add another")
  const confirmedRef = useRef(false);
  const [pcName, setPcName] = useState('');   // what phones call this PC ("Orion PC" unless you name it)
  const [share, setShare] = useState<AppShare | null>(null);   // [start-here] the app shared on the Wi-Fi
  const [report, setReport] = useState<LinkReport | null>(null);   // [phonelink] "Fix the connection"

  const fixLink = async (deviceId: string) => {   // [phonelink]
    setBusy(true);
    try {
      setReport(await call<LinkReport>(`/phones/link/check?device_id=${encodeURIComponent(deviceId)}`));
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn't run the checks.");
    } finally {
      setBusy(false);
    }
  };

  const loadShare = useCallback(async () => {
    try {
      setShare(await call<AppShare>('/phones/app-share'));
    } catch {
      setShare(null);   // an older Orion without app sharing: just the pairing QR
    }
  }, []);

  const loadPhones = useCallback(async () => {
    try {
      const j = await call<{ phones: Phone[]; pc?: { name?: string } }>('/phones');
      setPhones(j.phones);
      if (j.pc?.name) setPcName(j.pc.name);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Orion's brain isn't running.");
      setPhones([]);
    }
  }, []);

  const open = useCallback(async () => {
    setBusy(true);
    setNote('');
    confirmedRef.current = false;
    try {
      const j = await call<Opened>('/phones/pair/open', 'POST');
      setPair(j);
      setPairing(true);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn't start pairing.");
    } finally {
      setBusy(false);
    }
  }, []);

  // first look: paired phones; nobody paired -> start pairing straight away (one tap from the menu)
  useEffect(() => {
    void (async () => {
      try {
        const j = await call<{ phones: Phone[] }>('/phones');
        setPhones(j.phones);
        if (j.phones.length === 0) {
          void open();
          void loadShare();
        }
      } catch (e) {
        setPhones([]);
        setNote(e instanceof Error ? e.message : "Orion's brain isn't running.");
      }
    })();
  }, [open, loadShare]);

  // while pairing: watch for the phone (every 2 s); otherwise refresh "online / last seen" every 10 s
  useEffect(() => {
    const tick = async () => {
      if (pairing && pair) {
        try {
          const s = await call<Status>('/phones/pair/status');
          setStatus(s);
          if (!s.open && !confirmedRef.current) setNote('The code ran out. Press "New code".');
          if (s.phones.length === 0) void loadShare();
        } catch {
          /* keep the last state */
        }
      } else {
        void loadPhones();
      }
    };
    const t = setInterval(tick, pairing ? 2000 : 10000);
    return () => clearInterval(t);
  }, [pairing, pair, loadPhones, loadShare]);

  const shareApp = async () => {
    setBusy(true);
    try {
      setShare(await call<AppShare>('/phones/app-share', 'POST', { minutes: 15 }));
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn't share the app.");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (p: Pending) => {
    setBusy(true);
    try {
      await call(`/phones/${encodeURIComponent(p.device_id)}/confirm`, 'POST', { fingerprint: p.fingerprint });
      confirmedRef.current = true;
      setPairing(false);
      setPair(null);
      setStatus(null);
      setNote(`Paired with ${p.name}. Try "Send a test card to my phone".`);
      await loadPhones();
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn't confirm.");
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    setBusy(true);
    try {
      await call('/phones/pair/close', 'POST');
    } catch {
      /* closing is best effort */
    }
    setPair(null);
    setStatus(null);
    setPairing(false);
    setBusy(false);
    if (phones && phones.length === 0) setNote('Pairing cancelled. Nothing was trusted.');
  };

  const action = async (path: string, done?: (j: { ok?: boolean; words?: string }) => void) => {
    setBusy(true);
    try {
      const j = await call<{ ok?: boolean; words?: string }>(path, 'POST');
      setNote(j.words ?? '');
      done?.(j);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn't reach Orion.");
    } finally {
      setBusy(false);
    }
  };

  const lan = status?.lan ?? pair?.lan ?? null;
  const pending = status?.pending?.[0];
  const showPairing = pairing && pair;
  const firstPhone = !!phones && phones.length === 0;
  const showInstall = firstPhone && !!share && share.apk;   // "1. Install Orion Mobile", then pairing is "2."
  const close = () => {
    if (showPairing && !confirmedRef.current) void call('/phones/pair/close', 'POST').catch(() => undefined);
    onClose();
  };

  return (
    <GlassWindow
      id="phone"
      title="Phone"
      subtitle={showPairing ? 'Connect your phone' : 'Your phone and this PC'}
      icon={<Smartphone size={14} className="text-[#00E5FF]" />}
      onClose={close}
      maximisable={false}
      mobile={mobile}
      className="left-1/2 top-[12vh] -translate-x-1/2 w-[460px] max-w-[calc(100vw-32px)]"
    >
      <div className="p-4 space-y-3 max-h-[74vh] overflow-y-auto text-[#DDE5F0]">
        {phones === null && <div className="text-[13px] text-[#DDE5F0]/80">Checking…</div>}

        {/* ------------------------------------------------ pairing: QR, code, then the words */}
        {showPairing && !pending && showInstall && share && (
          <div className="rounded-xl px-3 py-3 space-y-2" style={{ background: 'rgba(0,229,255,0.05)', border: '1px solid rgba(0,229,255,0.25)' }}>
            <div className="text-[15px] font-semibold">1. Install Orion Mobile: scan this</div>
            {share.open && share.qr_svg ? (
              <>
                <img
                  className="mx-auto block w-[200px] max-w-full rounded-xl bg-white p-1"
                  style={{ imageRendering: 'pixelated' }}
                  alt="QR code to download Orion Mobile"
                  src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(share.qr_svg)}`}
                />
                <div className="text-center select-all font-mono text-[11.5px] text-[#DDE5F0]/78">{share.url}</div>
                <div className="text-[12px] text-[#DDE5F0]/80">
                  Your phone downloads the app over your Wi-Fi. Open the download and tap Install (Android may ask you
                  to allow installing from your browser once). Already installed? Skip to 2.
                </div>
              </>
            ) : (
              <button type="button" disabled={busy} className={btn} style={quiet} onClick={() => void shareApp()}>
                Share the app on my Wi-Fi for 15 minutes
              </button>
            )}
          </div>
        )}

        {showPairing && !pending && (
          <>
            <div className="text-[15px] font-semibold">
              {showInstall ? '2. Then pair: open your camera and point it at this.' : 'On your phone: open your camera and point it at this.'}
            </div>
            {pair.qr_svg ? (
              <img
                className="mx-auto block w-[240px] max-w-full rounded-xl bg-white p-1"
                style={{ imageRendering: 'pixelated' }}
                alt={`QR code for pairing. Code ${pair.code}`}
                src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(pair.qr_svg)}`}
              />
            ) : (
              <div className="text-[13px] text-[#FFC66B]">{pair.how}</div>
            )}
            <div className="text-center">
              <div className="text-[12px] text-[#DDE5F0]/80">or open Orion Mobile &gt; Pair with my PC and type the code</div>
              <div className="mt-1 select-all font-mono text-[20px] tracking-wider text-[#00E5FF]">{pair.code}</div>
              {pair.address && <div className="text-[11.5px] text-[#DDE5F0]/74">PC address {pair.address}</div>}
            </div>
            <div className="text-[12px] text-[#DDE5F0]/80">{pair.how}</div>
            {lan && lan.ok === false && (
              <div className="rounded-xl px-3 py-2 space-y-2" style={{ background: 'rgba(255,198,107,0.10)', border: '1px solid rgba(255,198,107,0.35)' }}>
                <div className="text-[13px] text-[#FFC66B]">{lan.words}</div>
                {lan.fix_words && <div className="text-[12px] text-[#DDE5F0]/85">{lan.fix_words}</div>}
                {lan.fix === 'open_access' && (
                  <button type="button" disabled={busy} className={btn} style={primary}
                    onClick={() => void action('/phones/open-access')}>
                    Let my phone in
                  </button>
                )}
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className="flex-1 text-[12px] text-[#DDE5F0]/76">Waiting for your phone…</span>
              <button type="button" disabled={busy} className={btn} style={quiet} onClick={() => void open()}>New code</button>
              <button type="button" disabled={busy} className={btn} style={quiet} onClick={() => void cancel()}>Cancel</button>
            </div>
          </>
        )}

        {showPairing && pending && (
          <>
            <div className="text-[15px] font-semibold">Do these words match your phone?</div>
            <div className="rounded-xl px-3 py-4 text-center" style={{ background: 'rgba(0,229,255,0.06)', border: '1px solid rgba(0,229,255,0.3)' }}>
              <div className="text-[24px] font-semibold leading-snug text-[#00E5FF]">{pending.words}</div>
              <div className="mt-2 font-mono text-[11.5px] text-[#DDE5F0]/74">{pending.fingerprint}</div>
            </div>
            <div className="text-[12px] text-[#DDE5F0]/82">
              &ldquo;{pending.name}&rdquo; wants to connect. Only press Confirm if your phone shows exactly these words.
              If they don&apos;t match, press Cancel: something on the network swapped the key.
            </div>
            <div className="flex gap-2">
              <button type="button" disabled={busy} className={`${btn} flex-1`} style={primary} onClick={() => void confirm(pending)}>
                Confirm
              </button>
              <button type="button" disabled={busy} className={btn} style={quiet} onClick={() => void cancel()}>Cancel</button>
            </div>
          </>
        )}

        {/* ------------------------------------------------ paired phones */}
        {!showPairing && phones && phones.length > 0 && (
          <>
            {phones.map((p) => (
              <div key={p.device_id} className="rounded-xl px-3 py-3 space-y-2" style={{ background: 'rgba(221,229,240,0.05)' }}>
                <div className="flex items-center gap-2">
                  <span aria-hidden className="h-2.5 w-2.5 rounded-full"
                    style={{ background: p.online ? '#5CFFB0' : '#5A6088', boxShadow: p.online ? '0 0 8px #5CFFB0' : 'none' }} />
                  <span className="flex-1 text-[15px] font-semibold">{p.name}</span>
                  <span className="text-[12px] text-[#DDE5F0]/80">{p.online ? 'Online' : `Last seen ${ago(p.last_seen)}`}</span>
                </div>
                {p.link && (   /* [phonelink] one plain line: Connected / Reconnecting… / Not connected */
                  <div className="text-[12px] text-[#DDE5F0]/85">
                    {p.link.words}
                    {p.link.state !== 'never' ? ` · last seen ${ago(p.link.last_seen)}` : ''}
                    {p.link.quality ? ` · link ${p.link.quality}` : ''}
                    {p.link.via === 'tailnet' ? ' · through Tailscale' : ''}
                    {p.link.clock_words ? ` · ${p.link.clock_words}` : ''}
                  </div>
                )}
                <div className="text-[12px] text-[#DDE5F0]/78">
                  {p.battery != null ? `Battery ${p.battery}%${p.charging ? ', charging' : ''}` : 'Battery not known yet'}
                  {p.network === 'cellular' ? ' · on mobile data' : ''}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" disabled={busy} className={btn} style={primary}
                    onClick={() => void action('/phones/test-card')}>
                    Send a test card to my phone
                  </button>
                  <button type="button" disabled={busy} className={btn} style={quiet}
                    onClick={() => void fixLink(p.device_id)}>
                    Fix the connection
                  </button>
                  <UnpairButton disabled={busy} onUnpair={() => void action(`/phones/${encodeURIComponent(p.device_id)}/unpair`, () => void loadPhones())} />
                </div>
              </div>
            ))}
            {report && (   /* [phonelink] what the checks found, the first thing to do on top */
              <div className="rounded-xl px-3 py-3 space-y-1 text-[12px]" style={{ background: 'rgba(221,229,240,0.05)' }}>
                <div className="text-[13px] font-semibold" style={{ color: report.ok ? '#5CFFB0' : '#FFB86B' }}>{report.say}</div>
                {report.checks.map((c) => (
                  <div key={c.id} className="text-[#DDE5F0]/85">
                    {c.ok === true ? 'OK' : c.ok === false ? 'Problem' : 'Note'} · {c.name}: {c.words}
                    {c.ok === false && c.fix ? ` ${c.fix}` : ''}
                  </div>
                ))}
                {report.checks.some((c) => c.id === 'firewall' && c.ok === false) && (
                  <button type="button" disabled={busy} className={btn} style={primary}
                    onClick={() => void action('/phones/open-access')}>
                    Let my phone in
                  </button>
                )}
                <button type="button" className={btn} style={quiet} onClick={() => setReport(null)}>Close</button>
              </div>
            )}
            <button type="button" disabled={busy} className={btn} style={quiet} onClick={() => void open()}>Pair another phone</button>
          </>
        )}

        {!showPairing && phones && phones.length === 0 && !busy && (
          <button type="button" className={btn} style={primary} onClick={() => void open()}>Pair my phone</button>
        )}

        {!showPairing && phones !== null && (
          <form className="flex items-center gap-2" onSubmit={(e) => {
            e.preventDefault();
            void call<{ name: string }>('/phones/pc-name', 'POST', { name: pcName })
              .then((j) => { setPcName(j.name); setNote(`Phones now see this PC as ${j.name}.`); })
              .catch((err) => setNote(err instanceof Error ? err.message : 'Couldn\'t save the name.'));
          }}>
            <label className="text-[12px] text-[#DDE5F0]/80" htmlFor="orion-pc-name">Phones call this PC</label>
            <input id="orion-pc-name" value={pcName} maxLength={40} placeholder="Orion PC"
              onChange={(e) => setPcName(e.target.value)}
              className="flex-1 rounded-lg bg-transparent px-2 py-1 text-[13px] outline-none"
              style={{ border: '1px solid rgba(221,229,240,0.2)' }} />
            <button type="submit" disabled={busy} className={btn} style={quiet}>Save</button>
          </form>
        )}

        {note && <div className="text-[12px] text-[#FFC66B]">{note}</div>}
        {status?.pin === 'asked' && (
          <div className="text-[12px] text-[#DDE5F0]/80">
            OrionOS asks for your password once, so your phone&apos;s fingerprint can also unfreeze Orion.
          </div>
        )}
      </div>
    </GlassWindow>
  );
}

function UnpairButton({ disabled, onUnpair }: { disabled: boolean; onUnpair: () => void }) {
  const [armed, setArmed] = useState(false);
  return (
    <button
      type="button"
      disabled={disabled}
      className={btn}
      style={{ background: 'rgba(255,92,138,0.18)', border: '1px solid rgba(255,92,138,0.5)' }}
      onClick={() => (armed ? (setArmed(false), onUnpair()) : setArmed(true))}
      onBlur={() => setArmed(false)}
    >
      {armed ? 'Tap again to unpair' : 'Unpair'}
    </button>
  );
}

/** Mounted once in App.tsx; opens on `orion:open` with detail "phone". */
export default function PhonePanel({ mobile }: { mobile?: boolean }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const onOpen = (e: Event) => {
      const d = (e as CustomEvent).detail as unknown;
      const what = typeof d === 'string' ? d : (d as { id?: string; panel?: string } | null)?.id ?? (d as { panel?: string } | null)?.panel;
      if (what === 'phone') setShown(true);
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);
  return <AnimatePresence>{shown && <PhoneWindow key="phone" mobile={mobile} onClose={() => setShown(false)} />}</AnimatePresence>;
}
