/**
 * Orion's UI sounds, synthesised with the Web Audio API (no audio files).
 *
 * Look pass (owner feedback: "less chimes, more bass hums"): every cue is a soft low hum or swell, a sine or
 * triangle between about 45 and 110 Hz with a slow attack and release, a quiet octave above it so small
 * speakers can still hear it, and a whisper of low-passed noise for texture. Nothing bright, nothing sharp.
 * The on/off switch and the volume (Settings > Personality and voice) work as before.
 */

type HumOpts = {
  /** Start delay in seconds. */
  at?: number;
  /** Base pitch in Hz (45-110). */
  freq: number;
  /** Pitch it glides to by the end, if any. */
  glideTo?: number;
  /** Seconds of rise and of fade. */
  attack: number;
  release: number;
  /** Seconds it holds between rise and fade. */
  hold?: number;
  /** Peak level before the master volume (0-1). */
  level: number;
  wave?: OscillatorType;
  /** How much filtered noise rides on top (0-1). */
  air?: number;
  /** Slow tremble in Hz (0 = none). */
  wobble?: number;
};

class CosmicAudioSynthesizer {
  private ctx: AudioContext | null = null;
  private noise: AudioBuffer | null = null;
  public enabled: boolean = true;
  public volume: number = 0.35;

  constructor() {
    // AudioContext is created on first use (after a user gesture)
    try {
      const savedMuted = localStorage.getItem('orion_audio_muted');
      if (savedMuted !== null) this.enabled = savedMuted !== 'true';
      const savedVol = localStorage.getItem('orion_audio_vol');
      if (savedVol !== null && !Number.isNaN(parseFloat(savedVol))) this.volume = parseFloat(savedVol);
    } catch {
      /* storage blocked: defaults */
    }
  }

  private getContext(): AudioContext | null {
    if (typeof window === 'undefined') return null;
    try {
      if (!this.ctx) {
        const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (AudioContextClass) this.ctx = new AudioContextClass();
      }
      if (this.ctx && this.ctx.state === 'suspended') void this.ctx.resume();
    } catch {
      return null;
    }
    return this.ctx;
  }

  public setMuted(muted: boolean) {
    this.enabled = !muted;
    try {
      localStorage.setItem('orion_audio_muted', String(muted));
    } catch {
      /* remembered for now only */
    }
  }

  public setVolume(vol: number) {
    this.volume = Math.max(0, Math.min(1, vol));
    try {
      localStorage.setItem('orion_audio_vol', String(this.volume));
    } catch {
      /* remembered for now only */
    }
  }

  private noiseBuffer(ctx: AudioContext): AudioBuffer {
    if (this.noise && this.noise.sampleRate === ctx.sampleRate) return this.noise;
    const buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 1.5), ctx.sampleRate);
    const d = buf.getChannelData(0);
    let last = 0;
    for (let i = 0; i < d.length; i++) {
      // brown noise: deep and soft, never hissy
      last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02;
      d[i] = last * 3.2;
    }
    this.noise = buf;
    return buf;
  }

  /** The one building block: a low hum that swells in and fades out. Overall level is kept quiet. */
  private hum(o: HumOpts) {
    if (!this.enabled || this.volume <= 0) return;
    const ctx = this.getContext();
    if (!ctx) return;
    try {
      const t0 = ctx.currentTime + 0.02 + (o.at ?? 0);
      const hold = o.hold ?? 0;
      const end = t0 + o.attack + hold + o.release;
      const peak = Math.max(0.0002, o.level * this.volume * 0.55);

      const amp = ctx.createGain();
      amp.gain.setValueAtTime(0.0001, t0);
      amp.gain.exponentialRampToValueAtTime(peak, t0 + o.attack);
      amp.gain.setValueAtTime(peak, t0 + o.attack + hold);
      amp.gain.exponentialRampToValueAtTime(0.0001, end);
      // a gentle low-pass keeps every partial round
      const lp = ctx.createBiquadFilter();
      lp.type = 'lowpass';
      lp.frequency.value = 420;
      lp.Q.value = 0.7;
      lp.connect(amp).connect(ctx.destination);

      const voice = (freq: number, gain: number, wave: OscillatorType, detune = 0) => {
        const osc = ctx.createOscillator();
        const g = ctx.createGain();
        osc.type = wave;
        osc.detune.value = detune;
        osc.frequency.setValueAtTime(freq, t0);
        if (o.glideTo) osc.frequency.exponentialRampToValueAtTime(o.glideTo * (freq / o.freq), end);
        g.gain.value = gain;
        osc.connect(g).connect(lp);
        osc.start(t0);
        osc.stop(end + 0.05);
        return g;
      };
      voice(o.freq, 0.8, o.wave ?? 'sine');
      voice(o.freq, 0.25, 'sine', 6); // slow beating for warmth
      voice(o.freq * 2, 0.22, 'triangle'); // an octave up so laptop speakers still hear it

      if (o.wobble) {
        const lfo = ctx.createOscillator();
        const depth = ctx.createGain();
        lfo.frequency.value = o.wobble;
        depth.gain.value = peak * 0.25;
        lfo.connect(depth).connect(amp.gain);
        lfo.start(t0);
        lfo.stop(end + 0.05);
      }

      if (o.air) {
        const src = ctx.createBufferSource();
        src.buffer = this.noiseBuffer(ctx);
        const band = ctx.createBiquadFilter();
        band.type = 'lowpass';
        band.frequency.value = 260;
        const ng = ctx.createGain();
        ng.gain.value = o.air * 0.5;
        src.connect(band).connect(ng).connect(amp);
        src.start(t0);
        src.stop(end + 0.05);
      }
    } catch {
      /* no sound here; nothing else depends on it */
    }
  }

  /** Orion begins listening: a short rising swell. */
  public playListenStart(): void {
    this.hum({ freq: 55, glideTo: 82.4, attack: 0.18, hold: 0.05, release: 0.45, level: 0.9, air: 0.4 });
  }

  /** Speech captured / a command sent: a low swell settling downward. */
  public playCommandDetected(): void {
    this.hum({ freq: 98, glideTo: 65.4, attack: 0.08, hold: 0.06, release: 0.6, level: 0.8, air: 0.3 });
  }

  /** A task finished: two soft hums a fifth apart, the second lower and longer, like a sigh of relief. */
  public playTaskFinished(): void {
    this.hum({ freq: 82.4, attack: 0.12, release: 0.5, level: 0.6, wave: 'triangle' });
    this.hum({ at: 0.22, freq: 55, attack: 0.2, hold: 0.1, release: 0.9, level: 0.75, air: 0.25 });
  }

  /** Orion noticed something useful: a single warm pulse with a slow tremble. */
  public playProactiveInsight(): void {
    this.hum({ freq: 73.4, attack: 0.25, hold: 0.15, release: 0.8, level: 0.6, wobble: 5, air: 0.2 });
  }

  /** The focus arc reached its limit: a slow, deep breath in and out. */
  public playCelestialChime(): void {
    this.hum({ freq: 49, glideTo: 55, attack: 0.9, hold: 0.4, release: 1.8, level: 0.8, air: 0.35, wobble: 0.8 });
    this.hum({ at: 0.6, freq: 73.4, attack: 0.8, hold: 0.2, release: 1.6, level: 0.35, wave: 'triangle' });
  }

  /** Something landed in the approval queue: two low taps on a drum skin. */
  public playApprovalPing(): void {
    this.hum({ freq: 87.3, glideTo: 70, attack: 0.03, release: 0.32, level: 0.7 });
    this.hum({ at: 0.16, freq: 87.3, glideTo: 70, attack: 0.03, release: 0.4, level: 0.55 });
  }

  /** A tap on a button or tab: the softest low thump. */
  public playTap(): void {
    this.hum({ freq: 110, glideTo: 80, attack: 0.012, release: 0.12, level: 0.45 });
  }

  /** The command console opens: a swell rising from the deep. */
  public playConsoleOpen(): void {
    this.hum({ freq: 46.2, glideTo: 69.3, attack: 0.22, hold: 0.04, release: 0.5, level: 0.75, air: 0.45 });
  }

  /** The command console closes: the same swell sinking back. */
  public playConsoleClose(): void {
    this.hum({ freq: 69.3, glideTo: 46.2, attack: 0.05, release: 0.42, level: 0.55, air: 0.3 });
  }

  /** A check passed (Test Center): a short warm hum. */
  public playSoftTick(): void {
    this.hum({ freq: 65.4, glideTo: 73.4, attack: 0.04, release: 0.35, level: 0.55 });
  }
}

export const cosmicAudio = new CosmicAudioSynthesizer();
