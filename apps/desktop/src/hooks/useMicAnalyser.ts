import { useEffect, useState } from 'react';

/** Microphone -> Web Audio AnalyserNode, active only while `enabled`. */
export function useMicAnalyser(enabled: boolean) {
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setAnalyser(null);
      return;
    }
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    let cancelled = false;

    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        if (cancelled) return;
        ctx = new AudioContext();
        const node = ctx.createAnalyser();
        node.fftSize = 256;
        node.smoothingTimeConstant = 0.8;
        ctx.createMediaStreamSource(stream).connect(node);
        setError(null);
        setAnalyser(node);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Microphone unavailable');
      }
    })();

    return () => {
      cancelled = true;
      stream?.getTracks().forEach((t) => t.stop());
      void ctx?.close();
      setAnalyser(null);
    };
  }, [enabled]);

  return { analyser, error };
}

export function readLevel(analyser: AnalyserNode, buf: { [index: number]: number; length: number }): number {
  analyser.getByteFrequencyData(buf as any);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i];
  return sum / (buf.length * 255);
}
