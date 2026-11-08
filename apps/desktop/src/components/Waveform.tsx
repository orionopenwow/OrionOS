import { useEffect, useRef } from 'react';
import type { CoreState } from '@orion/shared-types';

interface Props {
  state: CoreState;
  analyser?: AnalyserNode | null;
  bars?: number;
  className?: string;
}

export default function Waveform({ state, analyser, bars = 76, className = '' }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d')!;
    const freq = analyser ? new Uint8Array(analyser.frequencyBinCount) : null;
    let raf = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = canvas.clientWidth * dpr;
      canvas.height = canvas.clientHeight * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const draw = (t: number) => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      if (analyser && freq) analyser.getByteFrequencyData(freq);

      const s = stateRef.current;
      const amp = s === 'processing' ? 0.65 : s === 'listening' ? 0.42 : 0.16;
      const speed = s === 'processing' ? 0.015 : 0.004;
      const gap = 3;
      const bw = Math.max(1.8, (w - gap * bars) / bars);

      // Symmetrical Purple/Violet Neon Gradient
      const grad = ctx.createLinearGradient(0, 0, w, 0);
      grad.addColorStop(0, '#2E6BFF');
      grad.addColorStop(0.35, '#8A9AB5');
      grad.addColorStop(0.5, '#DCE7FF');
      grad.addColorStop(0.65, '#8A9AB5');
      grad.addColorStop(1, '#2E6BFF');
      ctx.fillStyle = grad;
      ctx.shadowColor = '#8A9AB5';
      ctx.shadowBlur = 10;

      for (let i = 0; i < bars; i++) {
        const centre = 1 - Math.abs(i - bars / 2) / (bars / 2);
        let v: number;
        if (freq) {
          const bin = Math.floor((Math.abs(i - bars / 2) / (bars / 2)) * freq.length * 0.65);
          v = (freq[bin] / 255) * 0.95 + 0.05;
        } else {
          // Complex organic breathing harmonics
          const wave1 = Math.sin(t * speed + i * 0.38);
          const wave2 = Math.cos(t * (speed * 0.7) + i * 0.6);
          v = ((wave1 + wave2) * 0.25 + 0.5) * amp * (0.35 + centre * 0.65) + 0.035;
        }
        const bh = Math.max(4, v * h);
        const y = (h - bh) / 2;
        const x = i * (bw + gap);

        // Rounded bar caps
        ctx.beginPath();
        ctx.roundRect(x, y, bw, bh, 2);
        ctx.fill();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [analyser, bars]);

  return <canvas ref={canvasRef} className={`h-16 w-full ${className}`} />;
}
