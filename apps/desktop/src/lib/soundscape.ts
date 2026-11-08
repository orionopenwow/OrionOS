/**
 * Procedural soundscape (no audio files): a low cosmic drone, soft rain, or a crackling campfire, built from
 * Web Audio oscillators and filtered noise. Off by default. Ducks while Orion speaks and follows the core's mode
 * (focus = tighter and brighter, sanctuary / wind-down = darker and slower).
 */
export type SoundscapeKind = 'off' | 'drone' | 'rain' | 'campfire';

type Mode = 'idle' | 'voice' | 'podcast' | 'focus' | 'winddown' | 'sanctuary';

const MODE_TONE: Record<Mode, { cutoff: number; lfo: number }> = {
  idle: { cutoff: 900, lfo: 0.08 },
  voice: { cutoff: 900, lfo: 0.08 },
  podcast: { cutoff: 700, lfo: 0.06 },
  focus: { cutoff: 1400, lfo: 0.14 },
  winddown: { cutoff: 420, lfo: 0.04 },
  sanctuary: { cutoff: 520, lfo: 0.035 },
};

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let filter: BiquadFilterNode | null = null;
let lfo: OscillatorNode | null = null;
let nodes: AudioNode[] = [];
let timers: number[] = [];
let kind: SoundscapeKind = 'off';
let volume = 0.35;
let ducked = false;
let mode: Mode = 'idle';

function noiseBuffer(c: AudioContext, seconds = 2): AudioBuffer {
  const buf = c.createBuffer(1, c.sampleRate * seconds, c.sampleRate);
  const d = buf.getChannelData(0);
  let last = 0;
  for (let i = 0; i < d.length; i++) {
    // brown-ish noise: smoother and less hissy than white
    last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02;
    d[i] = last * 3.5;
  }
  return buf;
}

function targetGain() {
  return kind === 'off' ? 0 : volume * (ducked ? 0.25 : 1);
}

function stopAll() {
  timers.forEach((t) => window.clearInterval(t));
  timers = [];
  nodes.forEach((n) => {
    try {
      (n as AudioScheduledSourceNode).stop?.();
    } catch {
      /* already stopped */
    }
    try {
      n.disconnect();
    } catch {
      /* ignore */
    }
  });
  nodes = [];
}

function ensureGraph(): AudioContext | null {
  if (typeof window === 'undefined') return null;
  if (!ctx) {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = 0;
    filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = MODE_TONE[mode].cutoff;
    filter.connect(master);
    master.connect(ctx.destination);
  }
  if (ctx.state === 'suspended') void ctx.resume();
  return ctx;
}

function build(k: SoundscapeKind) {
  const c = ctx!;
  const out = filter!;
  if (k === 'drone') {
    [55, 82.4, 110.2].forEach((f, i) => {
      const o = c.createOscillator();
      o.type = i === 0 ? 'sine' : 'triangle';
      o.frequency.value = f;
      o.detune.value = (i - 1) * 6;
      const g = c.createGain();
      g.gain.value = i === 0 ? 0.5 : 0.18;
      o.connect(g).connect(out);
      o.start();
      nodes.push(o, g);
    });
    // slow breathing on the filter
    lfo = c.createOscillator();
    lfo.frequency.value = MODE_TONE[mode].lfo;
    const depth = c.createGain();
    depth.gain.value = 180;
    lfo.connect(depth).connect(out.frequency);
    lfo.start();
    nodes.push(lfo, depth);
  } else if (k === 'rain') {
    const src = c.createBufferSource();
    src.buffer = noiseBuffer(c);
    src.loop = true;
    const hp = c.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 400;
    const g = c.createGain();
    g.gain.value = 0.6;
    src.connect(hp).connect(g).connect(out);
    src.start();
    nodes.push(src, hp, g);
  } else if (k === 'campfire') {
    const src = c.createBufferSource();
    src.buffer = noiseBuffer(c);
    src.loop = true;
    const g = c.createGain();
    g.gain.value = 0.45;
    src.connect(g).connect(out);
    src.start();
    nodes.push(src, g);
    // random crackles
    timers.push(
      window.setInterval(() => {
        if (!ctx || Math.random() > 0.55) return;
        const o = ctx.createOscillator();
        const cg = ctx.createGain();
        o.type = 'square';
        o.frequency.value = 900 + Math.random() * 2400;
        cg.gain.setValueAtTime(0.12 * Math.random(), ctx.currentTime);
        cg.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.03 + Math.random() * 0.05);
        o.connect(cg).connect(master!);
        o.start();
        o.stop(ctx.currentTime + 0.1);
      }, 140),
    );
  }
}

function applyGain() {
  if (!ctx || !master) return;
  master.gain.setTargetAtTime(targetGain(), ctx.currentTime, 0.6);
}

export const soundscape = {
  get kind() {
    return kind;
  },
  get volume() {
    return volume;
  },
  play(k: SoundscapeKind) {
    if (k === kind) return;
    kind = k;
    if (k === 'off') {
      applyGain();
      window.setTimeout(() => kind === 'off' && stopAll(), 1500);
      return;
    }
    if (!ensureGraph()) return;
    stopAll();
    build(k);
    applyGain();
  },
  setVolume(v: number) {
    volume = Math.max(0, Math.min(1, v));
    applyGain();
  },
  duck(on: boolean) {
    ducked = on;
    applyGain();
  },
  setMode(m: string) {
    mode = (m in MODE_TONE ? m : 'idle') as Mode;
    if (!ctx || !filter) return;
    filter.frequency.setTargetAtTime(MODE_TONE[mode].cutoff, ctx.currentTime, 1.5);
    if (lfo) lfo.frequency.setTargetAtTime(MODE_TONE[mode].lfo, ctx.currentTime, 1.5);
  },
};
