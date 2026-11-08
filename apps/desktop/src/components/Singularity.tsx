/**
 * Orion's living singularity. The core is the WebGL2 black hole shader (os/theme/blackhole/blackhole.frag,
 * driven through lib/blackhole): the painted disk flows with Keplerian rotation around a true black horizon and
 * photon ring, and dissolves into the starfield. If WebGL2 is unavailable the original painting is shown.
 *
 * Earlier notes (still true for the overlays): no rings, no borders. The vortex bleeds into the starfield through a feathered
 * radial mask, and each mode shifts the astrophysics (palette, spin, particles) instead of swapping UI chrome:
 *
 *   idle      Dormant supermassive black hole — obsidian core, slow violet/indigo swirl, sparse drifting light
 *   focus     Quasar — electric cyan accretion, 1.5x spin, stars visibly pulled inward
 *   voice     Pulsar — teal/emerald, concentric gravitational waves that ripple with your voice
 *   podcast   Binary merger / flare — crimson, magenta & gold; "mark that" fires a relativistic jet
 *   winddown  Redshift — near-halted infrared crimson/amber at 40 % brightness (sleep-friendly nightlight)
 *
 * Focus progress is organic: a single captured star spirals from the rim toward the event horizon over the
 * focus window, and the horizon's pull (glow + size) grows with it. A small readout floats underneath.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion';
import type { CoreState, PacingState } from '@orion/shared-types';
import { readLevel } from '../hooks/useMicAnalyser';
import { feedTheHole, presetFor, setBlackHoleSignals, useBlackHole, useCoreReactions } from '../lib/blackhole';
import './singularity.css';

/** The shader canvas is larger than the core so the disk's glow and lensed stars can fade out softly. */
const CANVAS_SCALE = 1.7;
/** u_size so the painted disk spans about the same footprint as the old image did. */
const CORE_SIZE = 0.74;
/** Shader horizon radius (RS = 0.082 of the canvas) as a fraction of the core size. */
const HORIZON_FRACTION = 0.082 * CORE_SIZE * CANVAS_SCALE;

export type GalacticMode = 'idle' | 'focus' | 'voice' | 'podcast' | 'winddown' | 'sanctuary';

export interface SwallowItem {
  id: number;
  label: string;
  kind: 'asset' | 'code' | 'doc' | 'other';
}

interface Props {
  mode: GalacticMode;
  state: CoreState;
  size?: number;
  analyser?: AnalyserNode | null;
  broadcastWaveform?: { amplitude: number; fft: number[]; is_playing: boolean } | null;
  pacing?: PacingState | null;
  /** Increments on every podcast "mark that" — each bump fires a relativistic jet. */
  highlightCount?: number;
  /** Optional caption under the core (podcast timer, bedtime…). Focus text is derived from `pacing`. */
  caption?: string | null;
  onCoreClick?: () => void;
  /** Singularity skin unlocked through Architect XP (default, emerald, supernova, bloodmoon, runic). */
  skin?: string;
  /** Momentum: number of Dyson-swarm structures (0–12) and entropy debris to clear. */
  swarm?: number;
  debris?: Array<{ id: string; label: string; tab: string; count: number }>;
  onDebrisClick?: (tab: string) => void;
  /** A file being swallowed by the event horizon (drag-and-drop). */
  swallow?: SwallowItem | null;
}

const INFALL_STARS = 10;

export default function Singularity({
  mode,
  state,
  size = 430,
  analyser,
  broadcastWaveform,
  pacing,
  highlightCount = 0,
  caption,
  onCoreClick,
  skin = 'default',
  swarm = 0,
  debris = [],
  onDebrisClick,
  swallow = null,
}: Props) {
  // ---------------------------------------------------------------- audio reactivity
  const level = useMotionValue(0);
  const smooth = useSpring(level, { stiffness: 220, damping: 22 });
  const breathe = useTransform(smooth, [0, 1], [1, 1.08]);
  const waveOpacity = useTransform(smooth, [0, 1], [0.35, 1]);
  const waveScale = useTransform(smooth, [0, 1], [0.55, 1.1]);
  const bufRef = useRef<Uint8Array | null>(null);
  const speaking = !!broadcastWaveform?.is_playing;

  // ---------------------------------------------------------------- the shader core
  const processingState = state === 'processing';
  const preset = useMemo(() => {
    const p = presetFor(mode, skin, processingState);
    p.size *= CORE_SIZE;
    return p;
  }, [mode, skin, processingState]);
  const { canvasRef, ok: glOk, quality } = useBlackHole({ preset, transparent: true });
  // smart reactions (Art stage 6): thinking, comets, approval stars, day and night, idle show, health
  const { orbitTotal } = useCoreReactions();
  // feeding the hole: the disk flares as it swallows a dropped file
  useEffect(() => {
    if (swallow) feedTheHole();
  }, [swallow]);

  // mic level -> ring brightness; Orion speaking (broadcast waveform) -> ripples through the disk
  useEffect(() => {
    const unsub = smooth.on('change', (v) => {
      const lv = Math.max(0, Math.min(1, v));
      setBlackHoleSignals(speaking ? { ripple: lv, voice: 0 } : { voice: lv, ripple: 0 });
    });
    return () => {
      unsub();
      setBlackHoleSignals({ voice: 0, ripple: 0 });
    };
  }, [smooth, speaking]);

  useEffect(() => {
    if (broadcastWaveform?.is_playing) {
      level.set(Math.min(1, broadcastWaveform.amplitude * 2.5));
      return;
    }
    if (!analyser) {
      level.set(0);
      return;
    }
    bufRef.current = new Uint8Array(analyser.frequencyBinCount);
    let raf = 0;
    const tick = () => {
      level.set(Math.min(1, readLevel(analyser, bufRef.current!) * 3));
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [analyser, broadcastWaveform, level]);

  // ---------------------------------------------------------------- relativistic jet on "mark that"
  const [jet, setJet] = useState(0);
  const lastHighlights = useRef(highlightCount);
  useEffect(() => {
    if (highlightCount > lastHighlights.current) setJet(Date.now());
    lastHighlights.current = highlightCount;
  }, [highlightCount]);

  // ---------------------------------------------------------------- focus progress → captured star
  const focusing = !!pacing && ['focus', 'warming', 'break_due'].includes(pacing.phase);
  const progress = focusing ? Math.max(0, Math.min(1, pacing!.progress)) : 0;
  const rim = size * 0.4;
  const horizon = size * 0.06;
  // ease-in so most of the fall happens late in the session, like a real inspiral
  const starRadius = rim - (rim - horizon) * Math.pow(progress, 1.6);
  const pull = 1 + progress * 0.14; // event horizon swells as the star nears

  const readout = useMemo(() => {
    if (caption) return caption;
    if (!pacing || pacing.phase === 'idle') return null;
    const minutes = Math.floor(pacing.session_minutes);
    if (pacing.phase === 'resting') return 'recharging';
    if (pacing.phase === 'break_due')
      return pacing.waiting_for_pause ? `${minutes} min · break at your next pause` : `${minutes} min · time to move`;
    return `${minutes} / ${pacing.limit_minutes} min focus`;
  }, [caption, pacing]);

  const processing = state === 'processing';
  const infall = useMemo(
    () =>
      Array.from({ length: INFALL_STARS }, (_, i) => ({
        angle: (360 / INFALL_STARS) * i + (i % 3) * 11,
        delay: (i * 0.77) % 6,
        dur: 4.5 + (i % 4) * 0.9,
      })),
    [],
  );

  return (
    <div className="singularity-wrap" style={{ width: size }}>
      <div
        className="singularity"
        data-gl={glOk || undefined}
        data-quality={glOk ? quality : undefined}
        data-mode={mode}
        data-skin={skin}
        data-processing={processing || undefined}
        style={{
          width: size,
          height: size,
          ['--pull' as string]: pull,
          // radius of the shader's horizon (infalling stars disappear there)
          ['--horizon-px' as string]: glOk ? `${Math.round(size * HORIZON_FRACTION)}px` : undefined,
        }}
        onClick={onCoreClick}
        title="Click Orion to speak"
      >
        {glOk ? (
          /* the living black hole: one WebGL2 pass, overlay mode (fades to transparent at its edge) */
          <canvas
            ref={canvasRef}
            className="sg-canvas"
            aria-hidden="true"
            style={{ width: size * CANVAS_SCALE, height: size * CANVAS_SCALE }}
          />
        ) : (
          /* fallback without WebGL2: soft nebula bloom behind the static painting */
          <div className="sg-bloom" />
        )}

        <motion.div className="sg-body" style={{ scale: glOk ? 1 : breathe }}>
          {!glOk && (
            <>
              {/* the original painting, two counter-rotating layers (no-WebGL2 fallback) */}
              <img className="sg-disk sg-disk-outer" src={`${import.meta.env.BASE_URL}black-hole-core.png`} alt="" draggable={false} />
              <img className="sg-disk sg-disk-inner" src={`${import.meta.env.BASE_URL}black-hole-core.png`} alt="" draggable={false} />
              <div className="sg-plasma" />
            </>
          )}

          {/* quasar infall: stars curving into the singularity (focus) */}
          <div className="sg-infall" aria-hidden="true">
            {infall.map((s, i) => (
              <span
                key={i}
                style={{
                  ['--a' as string]: `${s.angle}deg`,
                  ['--r' as string]: `${rim}px`,
                  animationDelay: `${s.delay}s`,
                  animationDuration: `${s.dur}s`,
                }}
              />
            ))}
          </div>


          {/* event horizon (fallback only; the shader draws a true horizon and photon ring) */}
          {!glOk && (
            <>
              <div className="sg-lens" />
              <div className="sg-horizon" />
            </>
          )}
        </motion.div>

        {/* pulsar: gravitational waves ripple outward into space with the voice (outside the feather mask) */}
        <motion.div className="sg-waves" style={{ opacity: waveOpacity, scale: waveScale }} aria-hidden="true">
          <span />
          <span />
          <span />
        </motion.div>

        {/* the captured star: orbits while its radius shrinks with the focus session */}
        {focusing && (
          <div className="sg-captured-orbit" aria-hidden="true">
            <div className="sg-captured-arm" style={{ width: starRadius }}>
              <span className="sg-captured-star" data-due={pacing?.phase === 'break_due' || undefined} />
            </div>
          </div>
        )}

        {/* living ecosystem: a micro Dyson swarm assembles as momentum builds */}
        {swarm > 0 && (
          <div className="sg-swarm" aria-label={`Momentum swarm: ${swarm} of 12 built`}>
            {Array.from({ length: 12 }, (_, i) => (
              <span
                key={i}
                className="sg-swarm-piece"
                data-built={i < swarm || undefined}
                style={{ ['--a' as string]: `${i * 30}deg`, ['--r' as string]: `${size * 0.47}px` }}
              />
            ))}
          </div>
        )}

        {/* entropy: red asteroids for things that are piling up — click one to go clear it */}
        {debris.length > 0 && (
          <div className="sg-debris">
            {debris.flatMap((d, di) =>
              Array.from({ length: Math.max(1, d.count) }, (_, i) => (
                <button
                  key={`${d.id}-${i}`}
                  type="button"
                  className="sg-asteroid"
                  title={`${d.label} — click to clear`}
                  aria-label={d.label}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDebrisClick?.(d.tab);
                  }}
                  style={{
                    ['--a' as string]: `${di * 97 + i * 41}deg`,
                    ['--r' as string]: `${size * (0.5 + ((i * 7 + di * 3) % 5) * 0.025)}px`,
                    ['--d' as string]: `${70 + ((i + di) % 4) * 18}s`,
                  }}
                />
              )),
            )}
          </div>
        )}

        {/* event horizon swallowing a dropped file (spaghettification) */}
        {swallow && (
          <div key={swallow.id} className="sg-swallow" data-kind={swallow.kind} aria-live="polite">
            <span className="sg-swallow-chip">{swallow.label}</span>
            <span className="sg-swallow-flare" />
          </div>
        )}

        {/* binary merger flare: relativistic jet + flash on each highlight */}
        {jet > 0 && (
          <div key={jet} className="sg-jet" aria-hidden="true">
            <span className="sg-jet-beam" />
            <span className="sg-jet-flash" />
          </div>
        )}
      </div>

      {readout && <div className="sg-readout" data-mode={mode}>{readout}</div>}
      {orbitTotal > 0 && (
        <div className="sg-orbit-count" title="Decisions waiting for you (the stars circling the core)">
          {orbitTotal} waiting
        </div>
      )}
    </div>
  );
}
