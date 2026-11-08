/**
 * Orion's voice (the voice programming step): pick how Orion sounds, hear samples, adjust speed and warmth,
 * design a voice from a description (when Voicebox runs on this computer), and say a test phrase back.
 * Opens from the menu ("voice"), Settings, Start Here (POST /hud/open {"what":"voice"}), or the first-time prompt.
 * Data: GET /voice/engines, POST /voice/set, /voice/preview, /voice/design, /voice/hear (apps/daemon/voice_engines.py).
 * OrionOS first-run has the same step in the terminal (os/lab/bin/orion-voice-setup).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import { AudioLines, Mic, Play, Sparkles } from 'lucide-react';
import GlassWindow from '../glass/GlassWindow';
import { ORION_HTTP_URL } from '../../lib/assets';
import { onOrionOpen, openOrion } from '../../lib/menu';

interface Voice { id: string; name: string; kind?: string; gender?: string; engine?: string }
interface Engine { id: string; name: string; available: boolean; voices: Voice[]; delivery: boolean; design: boolean; about: string; gpu?: string | null }
interface Active { engine: string; voice: string; speed: number; warmth: number; delivery: string; chosen: boolean }
interface State {
  active: Active;
  chosen: boolean;
  engines: Engine[];
  voicebox: { reachable: boolean; how: string; gpu?: string | null };
  note: string;
  consent: string;
  sample: string;
}

const PROMPT_KEY = 'orion.voicePromptSeen';
const btn = 'rounded-full px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-40';
const btnStyle = { background: 'rgba(124,92,255,0.25)', border: '1px solid #3A3F6B' };

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${ORION_HTTP_URL}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await r.json()) as T;
}

function VoicePanel({ onClose, mobile }: { onClose: () => void; mobile?: boolean }) {
  const [s, setS] = useState<State | null>(null);
  const [engine, setEngine] = useState('kokoro');
  const [voice, setVoice] = useState('');
  const [speed, setSpeed] = useState(0.92);
  const [warmth, setWarmth] = useState(0);
  const [delivery, setDelivery] = useState('');
  const [consent, setConsent] = useState(false);
  const [describe, setDescribe] = useState('');
  const [phrase, setPhrase] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${ORION_HTTP_URL}/voice/engines`);
      if (!r.ok) return setNote("Orion's brain didn't answer.");
      const j = (await r.json()) as State;
      setS(j);
      return j;
    } catch {
      setNote("Orion's brain isn't running.");
    }
  }, []);

  useEffect(() => {
    void load().then((j) => {
      if (!j) return;
      setEngine(j.active.engine);
      setVoice(j.active.voice);
      setSpeed(j.active.speed);
      setWarmth(j.active.warmth);
      setDelivery(j.active.delivery);
    });
  }, [load]);

  const eng = s?.engines.find((e) => e.id === engine);
  const picked = eng?.voices.find((v) => v.id === voice);
  const cloned = picked?.kind === 'cloned';

  const preview = async (v: string, text?: string) => {
    setBusy(true);
    try {
      const j = await post<{ ok: boolean; engine?: string; note?: string; message?: string }>('/voice/preview', {
        engine, voice: v, speed, warmth, delivery, text: text || s?.sample,
      });
      setNote(j.ok ? j.note || (j.engine === engine ? 'Playing.' : `Playing with ${j.engine}.`) : j.message ?? "Orion couldn't speak.");
    } catch {
      setNote("Couldn't reach Orion.");
    } finally {
      setBusy(false);
    }
  };

  const save = async (skip = false) => {
    setBusy(true);
    try {
      const j = await post<{ ok: boolean; message?: string }>(
        '/voice/set',
        skip ? { chosen: true } : { engine, voice, speed, warmth, delivery, consent, chosen: true },
      );
      setNote(j.message ?? (j.ok ? 'Saved.' : "That didn't save."));
      if (j.ok) void load();
    } catch {
      setNote("Couldn't reach Orion.");
    } finally {
      setBusy(false);
    }
  };

  const design = async () => {
    setBusy(true);
    setNote('Designing a voice from your words. The first time can take a minute.');
    try {
      const j = await post<{ ok: boolean; message?: string; voice?: Voice }>('/voice/design', { description: describe });
      setNote(j.message ?? '');
      if (j.ok && j.voice) {
        await load();
        setEngine('voicebox');
        setVoice(j.voice.id);
      }
    } catch {
      setNote("Couldn't reach Orion.");
    } finally {
      setBusy(false);
    }
  };

  const recorder = useRef<MediaRecorder | null>(null);
  const sayItBack = async () => {
    // Records only while "Listening" shows, about 4 seconds, and only after this button is pressed.
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return setNote('No microphone, or it was not allowed. Type a sentence instead.');
    }
    const rec = new MediaRecorder(stream);
    const parts: Blob[] = [];
    recorder.current = rec;
    rec.ondataavailable = (e) => parts.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setListening(false);
      try {
        const r = await fetch(`${ORION_HTTP_URL}/voice/hear`, { method: 'POST', body: new Blob(parts) });
        const j = (await r.json()) as { ok: boolean; text?: string; message?: string };
        if (j.ok && j.text) {
          setNote(`Orion heard: "${j.text}"`);
          void preview(voice, `You said: ${j.text}`);
        } else setNote(j.message ?? "Orion didn't catch that.");
      } catch {
        setNote("Couldn't reach Orion.");
      }
    };
    setListening(true);
    rec.start();
    window.setTimeout(() => rec.state !== 'inactive' && rec.stop(), 4000);
  };

  const range = (label: string, v: number, set: (n: number) => void, min: number, max: number, hint: string) => (
    <label className="block px-3 py-1.5">
      <span className="flex justify-between text-[12.5px] font-semibold text-[#D8DEFF]">
        {label}
        <span className="font-normal text-[#D8DEFF]/55">{hint}</span>
      </span>
      <input type="range" aria-label={label} min={min} max={max} step={0.01} value={v}
        onChange={(e) => set(Number(e.target.value))} className="w-full accent-[#7C5CFF]" />
    </label>
  );

  return (
    <GlassWindow
      id="voice"
      title="Orion's voice"
      subtitle="How Orion sounds"
      icon={<AudioLines size={14} className="text-[#B09CFF]" />}
      onClose={onClose}
      maximisable={false}
      mobile={mobile}
      className="left-4 bottom-[88px] w-[440px] max-w-[calc(100vw-32px)]"
    >
      <div className="max-h-[66vh] space-y-2 overflow-y-auto p-3 text-[#D8DEFF]">
        <div className="flex flex-wrap gap-1.5 px-1" role="radiogroup" aria-label="Voice engine">
          {(s?.engines ?? []).map((e) => (
            <button key={e.id} type="button" role="radio" aria-checked={engine === e.id} disabled={!e.available}
              onClick={() => { setEngine(e.id); setVoice(e.voices[0]?.id ?? ''); }}
              title={e.available ? e.about : `${e.name} isn't on this computer`}
              className={`${btn} ${engine === e.id ? '' : 'opacity-70'}`}
              style={engine === e.id ? { ...btnStyle, background: 'rgba(124,92,255,0.55)' } : btnStyle}>
              {e.name}{e.available ? '' : ' (not here)'}
            </button>
          ))}
        </div>
        {eng && <div className="px-2 text-[11.5px] text-[#D8DEFF]/55">{eng.about}{eng.gpu ? ` Using ${eng.gpu}.` : ''}</div>}
        {s && !s.voicebox.reachable && <div className="px-2 text-[11.5px] text-[#D8DEFF]/45">{s.voicebox.how}</div>}

        <div className="space-y-0.5" role="radiogroup" aria-label="Voices">
          {(eng?.voices ?? []).slice(0, 14).map((v) => (
            <div key={v.id} className={`flex items-center gap-2 rounded-xl px-3 py-1.5 ${voice === v.id ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]'}`}>
              <button type="button" role="radio" aria-checked={voice === v.id} onClick={() => setVoice(v.id)}
                className="min-w-0 flex-1 text-left text-[13px]">
                {v.name}
                {v.kind && v.kind !== 'preset' && <span className="ml-2 text-[11px] text-[#D8DEFF]/50">{v.kind}</span>}
              </button>
              <button type="button" disabled={busy} aria-label={`Hear ${v.name}`} onClick={() => void preview(v.id)}
                className="grid h-7 w-7 place-items-center rounded-full" style={btnStyle}>
                <Play size={12} />
              </button>
            </div>
          ))}
        </div>

        {range('Speed', speed, setSpeed, 0.5, 1.5, speed < 0.85 ? 'slower' : speed > 1.1 ? 'faster' : 'normal')}
        {range('Warmth', warmth, setWarmth, 0, 1, warmth === 0 ? 'as recorded' : warmth > 0.6 ? 'soft and warm' : 'a little warmer')}

        {eng?.delivery && (
          <label className="block px-3 py-1">
            <span className="block text-[12.5px] font-semibold">Delivery</span>
            <input value={delivery} onChange={(e) => setDelivery(e.target.value)} maxLength={300}
              placeholder="For example: calm and unhurried, with a slight smile"
              className="mt-1 w-full rounded-lg bg-black/25 px-2 py-1.5 text-[12.5px] text-white placeholder:text-white/35 focus:outline-none" />
          </label>
        )}

        {s?.voicebox.reachable && (
          <div className="rounded-xl px-3 py-2" style={{ background: 'rgba(216,222,255,0.05)' }}>
            <span className="flex items-center gap-1.5 text-[12.5px] font-semibold"><Sparkles size={13} /> Design a voice</span>
            <span className="block text-[11.5px] text-[#D8DEFF]/55">Describe it in your own words. No recording needed.</span>
            <div className="mt-1.5 flex gap-2">
              <input value={describe} onChange={(e) => setDescribe(e.target.value)} maxLength={500}
                aria-label="Describe the voice" placeholder="A warm, low, friendly voice that speaks clearly"
                className="min-w-0 flex-1 rounded-lg bg-black/25 px-2 py-1.5 text-[12.5px] text-white placeholder:text-white/35 focus:outline-none" />
              <button type="button" className={btn} style={btnStyle} disabled={busy || describe.trim().length < 8} onClick={() => void design()}>
                Design
              </button>
            </div>
          </div>
        )}

        <div className="px-3 py-1">
          <span className="block text-[12.5px] font-semibold">Try it</span>
          <div className="mt-1 flex gap-2">
            <input value={phrase} onChange={(e) => setPhrase(e.target.value)} maxLength={400} aria-label="A sentence for Orion to say"
              placeholder="Type a sentence for Orion to say"
              className="min-w-0 flex-1 rounded-lg bg-black/25 px-2 py-1.5 text-[12.5px] text-white placeholder:text-white/35 focus:outline-none" />
            <button type="button" className={btn} style={btnStyle} disabled={busy} onClick={() => void preview(voice, phrase)}>Say it</button>
            <button type="button" className={btn} style={btnStyle} disabled={busy || listening} onClick={() => void sayItBack()}
              aria-label="Say something and Orion says it back">
              <Mic size={12} className="inline" /> {listening ? 'Listening…' : 'Say it back'}
            </button>
          </div>
        </div>

        <label className="flex items-start gap-2 px-3 py-1 text-[11.5px] text-[#D8DEFF]/70">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5" />
          <span>{s?.consent ?? 'Only use your own voice or a voice you have permission to use.'}{cloned ? ' (needed for this cloned voice)' : ''}</span>
        </label>

        {(note || s?.note) && <div className="px-3 text-[12px] text-[#FFC66B]" role="status">{note || s?.note}</div>}

        <div className="flex gap-2 px-3 pb-1 pt-1">
          <button type="button" className={btn} style={{ ...btnStyle, background: 'rgba(124,92,255,0.55)' }}
            disabled={busy || !voice || (cloned && !consent)} onClick={() => void save()}>
            Use this voice
          </button>
          {!s?.chosen && (
            <button type="button" className={btn} style={btnStyle} disabled={busy} onClick={() => void save(true)}>
              Skip for now
            </button>
          )}
        </div>
      </div>
    </GlassWindow>
  );
}

/** The window plus a one-time prompt ("Choose Orion's voice?") until a voice is chosen or skipped. */
export default function VoiceWindow({ mobile }: { mobile?: boolean }) {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState(false);
  useEffect(() => onOrionOpen('voice', () => { setOpen(true); setPrompt(false); }), []);
  useEffect(() => {
    let seen = false;
    try { seen = localStorage.getItem(PROMPT_KEY) === '1'; } catch { /* private window: ask anyway */ }
    if (seen) return;
    const t = window.setTimeout(async () => {
      try {
        const r = await fetch(`${ORION_HTTP_URL}/voice/engines`);
        if (r.ok && !((await r.json()) as State).chosen) setPrompt(true);
      } catch { /* brain not running: ask another time */ }
    }, 8000);
    return () => window.clearTimeout(t);
  }, []);
  const dismiss = () => {
    setPrompt(false);
    try { localStorage.setItem(PROMPT_KEY, '1'); } catch { /* fine */ }
  };

  return (
    <>
      {prompt && !open && (
        <div role="dialog" aria-label="Choose Orion's voice"
          className="orion-glass fixed bottom-[88px] right-4 z-[55] w-[300px] rounded-2xl p-3 text-[#D8DEFF]">
          <div className="text-[13px] font-semibold">Choose Orion's voice?</div>
          <div className="mt-0.5 text-[11.5px] text-[#D8DEFF]/60">Hear a few voices and pick one. About a minute.</div>
          <div className="mt-2 flex gap-2">
            <button type="button" className={btn} style={{ ...btnStyle, background: 'rgba(124,92,255,0.55)' }}
              onClick={() => { dismiss(); openOrion('voice'); }}>Choose</button>
            <button type="button" className={btn} style={btnStyle} onClick={dismiss}>Later</button>
          </div>
        </div>
      )}
      <AnimatePresence>{open && <VoicePanel key="voice" mobile={mobile} onClose={() => setOpen(false)} />}</AnimatePresence>
    </>
  );
}
