/**
 * Guided tour for the guest demo: Orion introduces herself out loud (browser speech, with captions) and opens
 * each part of the HUD as she describes it. Works with sound off, on phones, and can be paused or skipped.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Pause, Play, SkipForward, Volume2, VolumeX, X } from 'lucide-react';
import { tourCommand, type TourCommand } from './tourBus';
import { DemoSocket } from './demoDaemon';
import { detectMobile } from '../lib/device';
import { openOrion } from '../lib/menu';

export const TOUR_START_EVENT = 'orion:tour-start';
const SEEN_KEY = 'orion-demo-tour-seen';

interface Step {
  title: string;
  text: string;
  mobileText?: string;
  ui: TourCommand;
  /** [pages] a menu item to open while this step plays (lib/menu.ts ids, or 'menu') */
  orion?: string;
  daemon?: Parameters<DemoSocket['tour']>[0];
}

const STEPS: Step[] = [
  {
    title: 'Hi, I’m Orion',
    text: 'Hi, I’m Orion. On OrionOS I am the desktop: your work, your messages, your agents and your day in one place. Let me show you around.',
    ui: { open: false },
  },
  {
    title: 'The black hole',
    text: 'This black hole is my core. It is calm when all is well, brighter while we talk and warmer when it is time to rest. Things waiting for your OK orbit it.',
    ui: { open: false },
  },
  {
    title: 'One Orion button',
    text: 'Everything starts from the round Orion button. It opens a console: talk to me in the middle, with every feature laid out around it. Type a feature’s name to find it, and drag the button anywhere you like.',
    ui: { open: false },
    orion: 'menu',
  },
  {
    title: 'Projects',
    text: 'Projects is where you start something with Claude. Name it, pick a starter, say what you want, and watch it being built.',
    ui: { open: false },
    orion: 'projects',
  },
  {
    title: 'Mission Control',
    text: 'Mission Control shows every agent job: its live log, what it changed and what it cost. A second agent reviews the work, and nothing is merged until you approve it with your fingerprint.',
    ui: { open: false },
    orion: 'missions',
  },
  {
    title: 'Your phone',
    text: 'Pair your phone by pointing its camera at this code. Then your phone shows what needs you, and big decisions are approved with your fingerprint, never by voice.',
    ui: { open: false },
    orion: 'phone',
  },
  {
    title: 'Privacy',
    text: 'The privacy dashboard lists everything I can see or hear, with one tap to turn each off. The camera and the hot mic are off until you turn them on.',
    ui: { open: false },
    orion: 'privacy',
  },
  {
    title: 'Test Center',
    text: 'The Test Center checks that every part works, one card at a time. Press Do it for me and I do the harmless half of each test.',
    ui: { open: false },
    orion: 'tests',
  },
  {
    title: 'Your turn',
    text: 'That’s me. Open the Orion button and try anything: this preview uses sample data and nothing leaves your browser. When you’re ready, try OrionOS in a virtual machine or from a USB stick.',
    ui: { tab: 'chat' },
    daemon: 'wake',
  },
];

// ------------------------------------------------------------------ speech
const PREFERRED = [
  'Samantha', 'Ava', 'Allison', 'Serena', 'Google UK English Female', 'Microsoft Aria', 'Microsoft Jenny',
  'Microsoft Sonia', 'Microsoft Zira', 'Karen', 'Moira', 'Tessa', 'Victoria', 'Female',
];
function pickVoice(): SpeechSynthesisVoice | null {
  if (!('speechSynthesis' in window)) return null;
  const voices = window.speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith('en'));
  for (const name of PREFERRED) {
    const v = voices.find((x) => x.name.includes(name));
    if (v) return v;
  }
  return voices[0] ?? null;
}
const sentences = (t: string) => t.match(/[^.!?]+[.!?]*/g)?.map((x) => x.trim()).filter(Boolean) ?? [t];

function readSeen() {
  try {
    return window.localStorage.getItem(SEEN_KEY) === '1';
  } catch {
    return false;
  }
}
function markSeen() {
  try {
    window.localStorage.setItem(SEEN_KEY, '1');
  } catch {
    /* ignore */
  }
}

export default function Tour() {
  const [offer, setOffer] = useState(false);
  const [step, setStep] = useState(-1);
  const [paused, setPaused] = useState(false);
  const [muted, setMuted] = useState(false);
  const mobile = detectMobile();
  const run = useRef(0); // bumps on every (re)start of a step so stale callbacks are ignored
  const timer = useRef<number | undefined>(undefined);

  // offer the tour on first visit (or ?tour=1)
  useEffect(() => {
    let want = !readSeen();
    try {
      const qs = new URLSearchParams(window.location.search);
      if (qs.get('tour') === '1') want = true;
      if (qs.get('shot') === '1' || qs.get('tour') === '0') want = false; // [pages] screenshots and the smoke test
    } catch {
      /* ignore */
    }
    const t = window.setTimeout(() => want && setOffer(true), 1400);
    const onStart = () => {
      setOffer(false);
      setPaused(false);
      setStep(0);
    };
    window.addEventListener(TOUR_START_EVENT, onStart);
    // voices load asynchronously in Chrome
    if ('speechSynthesis' in window) window.speechSynthesis.onvoiceschanged = () => undefined;
    return () => {
      window.clearTimeout(t);
      window.removeEventListener(TOUR_START_EVENT, onStart);
    };
  }, []);

  const stopSpeech = useCallback(() => {
    window.clearTimeout(timer.current);
    try {
      window.speechSynthesis?.cancel();
    } catch {
      /* ignore */
    }
    tourCommand({ speaking: false });
  }, []);

  const end = useCallback(() => {
    run.current++;
    stopSpeech();
    setStep(-1);
    markSeen();
    DemoSocket.instance?.tour('wake');
  }, [stopSpeech]);

  const next = useCallback(() => {
    stopSpeech();
    setStep((s) => s + 1);
  }, [stopSpeech]);

  // run the current step: drive the UI, then speak (or wait) and advance
  useEffect(() => {
    if (step < 0) return;
    if (step >= STEPS.length) return end();
    const s = STEPS[step];
    const id = ++run.current;
    tourCommand(s.ui);
    if (s.orion) openOrion(s.orion);
    if (s.daemon) window.setTimeout(() => DemoSocket.instance?.tour(s.daemon!), 500);
    if (paused) return;

    const text = (mobile && s.mobileText) || s.text;
    const estimate = Math.max(3500, text.length * 62);
    const started = Date.now();
    const advance = () => {
      if (run.current !== id) return;
      tourCommand({ speaking: false });
      // if speech failed or finished implausibly fast (no voices installed), still give people time to read
      const rest = Math.max(700, estimate * 0.75 - (Date.now() - started));
      timer.current = window.setTimeout(() => run.current === id && setStep((x) => x + 1), rest);
    };
    const synth = 'speechSynthesis' in window ? window.speechSynthesis : null;

    if (muted || !synth) {
      timer.current = window.setTimeout(advance, estimate);
      return;
    }
    synth.cancel();
    const voice = pickVoice();
    const parts = sentences(text);
    parts.forEach((part, i) => {
      const u = new SpeechSynthesisUtterance(part);
      if (voice) u.voice = voice;
      u.rate = 0.98;
      u.pitch = 1.02;
      u.onstart = () => run.current === id && tourCommand({ speaking: true });
      if (i === parts.length - 1) {
        u.onend = advance;
        u.onerror = advance;
      }
      synth.speak(u);
    });
    // safety net: some browsers never fire onend
    timer.current = window.setTimeout(advance, estimate * 1.6 + 2000);
    return () => window.clearTimeout(timer.current);
  }, [step, paused, muted, mobile, end]);

  useEffect(() => () => stopSpeech(), [stopSpeech]);
  useEffect(() => {
    if (step === -1) tourCommand({ speaking: false });
  }, [step]);

  const s = step >= 0 && step < STEPS.length ? STEPS[step] : null;
  // phones: sit at the top while Orion talks about the space view, at the bottom while a panel tab is open
  const pos = mobile
    ? s && s.ui.open === false
      ? 'fixed inset-x-3 top-[max(60px,calc(env(safe-area-inset-top)+52px))]'
      : 'fixed inset-x-3 bottom-[max(12px,env(safe-area-inset-bottom))]'
    : s && s.ui.open !== false
    ? 'fixed bottom-28 right-6 w-[min(480px,calc(100vw-660px))] min-w-[340px]'
    : 'fixed top-16 left-1/2 -translate-x-1/2 w-[min(540px,calc(100vw-32px))]';

  return (
    <>
      <AnimatePresence>
        {offer && step < 0 && (
          <motion.div
            key="tour-offer"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            className={`${pos} z-[200] rounded-3xl border border-white/12 bg-black/80 backdrop-blur-2xl p-5 text-white shadow-[0_20px_60px_rgba(0,0,0,0.7)]`}
            role="dialog"
            aria-label="Take the tour"
          >
            <div className="text-[11.5px] uppercase tracking-[0.2em] text-[#8DB0FF]">Guided tour · 90 seconds</div>
            <div className="mt-1 text-[18px] font-extrabold">Let Orion show you around</div>
            <p className="mt-1 text-[13px] text-white/82">
              She’ll talk you through what she does and open each menu as she goes. Turn your sound on.
            </p>
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                onClick={() => {
                  setOffer(false);
                  setMuted(false);
                  setPaused(false);
                  setStep(0);
                }}
                className="flex-1 inline-flex items-center justify-center gap-2 rounded-full bg-white text-black font-bold py-2.5 text-[14px] active:scale-95 transition-transform"
              >
                <Volume2 size={16} /> Start the tour
              </button>
              <button
                type="button"
                onClick={() => {
                  setOffer(false);
                  markSeen();
                }}
                className="rounded-full border border-white/15 px-4 py-2.5 text-[13.5px] text-white/88 active:scale-95 transition-transform"
              >
                Explore myself
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {s && (
          <motion.div
            key="tour-card"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            className={`${pos} z-[200] rounded-3xl border border-white/12 bg-black/80 backdrop-blur-2xl p-4 text-white shadow-[0_20px_60px_rgba(0,0,0,0.7)]`}
            role="region"
            aria-label="Orion tour"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-[#8DB0FF] animate-pulse" aria-hidden="true" />
              <span className="text-[13px] font-bold">{s.title}</span>
              <span className="ml-auto text-[11.5px] font-mono text-white/72">
                {step + 1}/{STEPS.length}
              </span>
            </div>
            <motion.p
              key={step}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="mt-2 text-[14px] leading-relaxed text-white/85"
              aria-live="polite"
            >
              {(mobile && s.mobileText) || s.text}
            </motion.p>
            <div className="mt-3 flex items-center gap-1.5">
              <div className="flex gap-1 mr-auto" aria-hidden="true">
                {STEPS.map((_, i) => (
                  <span key={i} className={`h-1 rounded-full transition-all ${i === step ? 'w-5 bg-[#8DB0FF]' : i < step ? 'w-2 bg-white/50' : 'w-2 bg-white/15'}`} />
                ))}
              </div>
              <TourBtn label={muted ? 'Turn voice on' : 'Mute voice'} onClick={() => { stopSpeech(); setMuted((m) => !m); }}>
                {muted ? <VolumeX size={15} /> : <Volume2 size={15} />}
              </TourBtn>
              <TourBtn label={paused ? 'Resume' : 'Pause'} onClick={() => { if (!paused) stopSpeech(); setPaused((p) => !p); }}>
                {paused ? <Play size={15} /> : <Pause size={15} />}
              </TourBtn>
              <TourBtn label="Next" onClick={next}>
                <SkipForward size={15} />
              </TourBtn>
              <TourBtn label="End tour" onClick={end}>
                <X size={15} />
              </TourBtn>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

function TourBtn({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="w-9 h-9 inline-flex items-center justify-center rounded-full border border-white/10 bg-white/5 text-white/80 hover:text-white hover:bg-white/10 active:scale-95 transition-all"
    >
      {children}
    </button>
  );
}
