/**
 * Cosmic Sound Effects Synthesizer using Web Audio API.
 * Synthesizes pure harmonic, ethereal celestial chimes and audio cues
 * with zero external audio assets.
 */

class CosmicAudioSynthesizer {
  private ctx: AudioContext | null = null;
  public enabled: boolean = true;
  public volume: number = 0.35;

  constructor() {
    // AudioContext will be initialized on first user interaction
    const savedMuted = localStorage.getItem('orion_audio_muted');
    if (savedMuted !== null) {
      this.enabled = savedMuted !== 'true';
    }
    const savedVol = localStorage.getItem('orion_audio_vol');
    if (savedVol !== null) {
      this.volume = parseFloat(savedVol);
    }
  }

  private getContext(): AudioContext | null {
    if (typeof window === 'undefined') return null;
    if (!this.ctx) {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      if (AudioContextClass) {
        this.ctx = new AudioContextClass();
      }
    }
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
    return this.ctx;
  }

  public setMuted(muted: boolean) {
    this.enabled = !muted;
    localStorage.setItem('orion_audio_muted', String(muted));
  }

  public setVolume(vol: number) {
    this.volume = Math.max(0, Math.min(1, vol));
    localStorage.setItem('orion_audio_vol', String(this.volume));
  }

  /**
   * Warm analog-style synth voice: detuned saw stack + sub sine through a resonant low-pass whose cutoff
   * opens and closes (the classic pad "bwaah"), with a soft attack so it never clicks.
   */
  private synth(
    ctx: AudioContext,
    out: AudioNode,
    opts: { at: number; dur: number; notes: number[]; glideTo?: number; cutoff: [number, number, number]; level: number },
  ) {
    const { at, dur, notes, glideTo, cutoff, level } = opts;
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.Q.value = 6;
    filter.frequency.setValueAtTime(cutoff[0], at);
    filter.frequency.exponentialRampToValueAtTime(cutoff[1], at + dur * 0.35);
    filter.frequency.exponentialRampToValueAtTime(cutoff[2], at + dur);

    const amp = ctx.createGain();
    amp.gain.setValueAtTime(0.0001, at);
    amp.gain.exponentialRampToValueAtTime(level, at + 0.12);
    amp.gain.setTargetAtTime(level * 0.7, at + 0.15, 0.25);
    amp.gain.setTargetAtTime(0.0001, at + dur * 0.55, dur * 0.16);
    filter.connect(amp).connect(out);

    const ratio = glideTo ? glideTo / notes[0] : 1;
    notes.forEach((note, n) => {
      // three slightly detuned saws per note = analog chorus width
      [-7, 0, 7].forEach((cents) => {
        const osc = ctx.createOscillator();
        const g = ctx.createGain();
        osc.type = 'sawtooth';
        osc.detune.value = cents;
        osc.frequency.setValueAtTime(note, at);
        if (glideTo) osc.frequency.exponentialRampToValueAtTime(note * ratio, at + dur * 0.5);
        g.gain.value = (n === 0 ? 0.32 : 0.2) / 3;
        osc.connect(g).connect(filter);
        osc.start(at);
        osc.stop(at + dur + 0.8);
      });
    });
    // sub-octave sine for body
    const sub = ctx.createOscillator();
    const subGain = ctx.createGain();
    sub.type = 'sine';
    sub.frequency.setValueAtTime(notes[0] / 2, at);
    if (glideTo) sub.frequency.exponentialRampToValueAtTime((notes[0] / 2) * ratio, at + dur * 0.5);
    subGain.gain.value = 0.5;
    sub.connect(subGain).connect(amp);
    sub.start(at);
    sub.stop(at + dur + 0.8);
  }

  /**
   * Orion begins listening: a quick bassy synth rise — A1 sweeping up an octave to A2 as the filter opens.
   */
  public playListenStart(): void {
    if (!this.enabled) return;
    const ctx = this.getContext();
    if (!ctx) return;
    const now = ctx.currentTime + 0.01;
    const master = ctx.createGain();
    master.gain.setValueAtTime(this.volume * 1.4, now);
    master.connect(ctx.destination);
    this.synth(ctx, master, { at: now, dur: 0.42, notes: [55, 82.41], glideTo: 110, cutoff: [140, 1100, 700], level: 0.7 });
  }

  /**
   * Speech captured / sent: a short synth "bwow" gliding down from A2 to D2 as the filter closes.
   */
  public playCommandDetected(): void {
    if (!this.enabled) return;
    const ctx = this.getContext();
    if (!ctx) return;
    const now = ctx.currentTime + 0.01;
    const master = ctx.createGain();
    master.gain.setValueAtTime(this.volume * 1.9, now);
    master.connect(ctx.destination);
    this.synth(ctx, master, { at: now, dur: 0.9, notes: [110, 164.81], glideTo: 73.42, cutoff: [900, 700, 160], level: 0.5 });
  }

  /**
   * Gentle cosmic resolve pentatonic chime with celestial shimmer decay when a task finishes.
   * Freqs: E5 (659.25Hz), B5 (987.77Hz), E6 (1318.51Hz)
   */
  public playTaskFinished(): void {
    if (!this.enabled) return;
    const ctx = this.getContext();
    if (!ctx) return;

    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(this.volume * 0.4, now);
    master.connect(ctx.destination);

    const freqs = [659.25, 830.61, 987.77, 1318.51];
    freqs.forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + idx * 0.05);

      gain.gain.setValueAtTime(0.001, now + idx * 0.05);
      gain.gain.exponentialRampToValueAtTime(0.55, now + idx * 0.05 + 0.04);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.05 + 1.2);

      osc.connect(gain);
      gain.connect(master);

      osc.start(now + idx * 0.05);
      osc.stop(now + idx * 0.05 + 1.25);
    });
  }

  /**
   * Ethereal pulse chime when Orion discovers a proactive insight on screen.
   */
  public playProactiveInsight(): void {
    if (!this.enabled) return;
    const ctx = this.getContext();
    if (!ctx) return;

    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(this.volume * 0.35, now);
    master.connect(ctx.destination);

    const freqs = [880.0, 1174.66, 1760.0];
    freqs.forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + idx * 0.07);

      gain.gain.setValueAtTime(0.001, now + idx * 0.07);
      gain.gain.exponentialRampToValueAtTime(0.4, now + idx * 0.07 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.07 + 0.9);

      osc.connect(gain);
      gain.connect(master);

      osc.start(now + idx * 0.07);
      osc.stop(now + idx * 0.07 + 0.95);
    });
  }

  /**
   * V2 harmonic Celestial Chime — plays when the pacing arc reaches its limit (90 min).
   * Slow G-major arpeggio (G5 B5 D6 G6) with detuned partials and a long, soft shimmer tail.
   */
  public playCelestialChime(): void {
    if (!this.enabled) return;
    const ctx = this.getContext();
    if (!ctx) return;

    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(this.volume * 0.5, now);
    master.connect(ctx.destination);

    const notes = [783.99, 987.77, 1174.66, 1567.98];
    notes.forEach((freq, idx) => {
      const t = now + idx * 0.18;
      [1, 2.01, 3.0].forEach((partial, pIdx) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq * partial, t);
        osc.detune.setValueAtTime(pIdx === 1 ? 4 : pIdx === 2 ? -3 : 0, t);
        const peak = 0.5 / (pIdx + 1) ** 1.6;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(peak, t + 0.03);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 2.4 - pIdx * 0.5);
        osc.connect(gain);
        gain.connect(master);
        osc.start(t);
        osc.stop(t + 2.5);
      });
    });
  }

  /**
   * V2 soft two-note ping when a drafted reply or patch lands in the approval queue.
   */
  public playApprovalPing(): void {
    if (!this.enabled) return;
    const ctx = this.getContext();
    if (!ctx) return;

    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(this.volume * 0.3, now);
    master.connect(ctx.destination);

    [1318.51, 1567.98].forEach((freq, idx) => {
      const t = now + idx * 0.09;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, t);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.45, t + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.5);
      osc.connect(gain);
      gain.connect(master);
      osc.start(t);
      osc.stop(t + 0.55);
    });
  }

  /**
   * Subtle glassmorphic tactile tap for planetary dials and radial buttons.
   */
  public playTap(): void {
    if (!this.enabled) return;
    const ctx = this.getContext();
    if (!ctx) return;

    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(this.volume * 0.25, now);
    master.connect(ctx.destination);

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = 'sine';
    osc.frequency.setValueAtTime(1200, now);
    osc.frequency.exponentialRampToValueAtTime(800, now + 0.08);

    gain.gain.setValueAtTime(0.3, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);

    osc.connect(gain);
    gain.connect(master);

    osc.start(now);
    osc.stop(now + 0.09);
  }
}

export const cosmicAudio = new CosmicAudioSynthesizer();
