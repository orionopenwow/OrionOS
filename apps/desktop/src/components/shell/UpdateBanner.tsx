import { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Loader2, X, AlertTriangle } from 'lucide-react';
import { isTauri } from '../../lib/tauri';
import { desktop, onDesktopEvent } from '../../lib/desktop';

const CHECK_EVERY_MS = 6 * 60 * 60 * 1000;

type Phase = 'idle' | 'available' | 'downloading' | 'installing' | 'error' | 'uptodate';

/**
 * Signed auto-updates from GitHub Releases. Checks shortly after launch, every 6 hours and from
 * the tray's "Check for updates". One click downloads, stops the brain, installs and relaunches.
 */
export default function UpdateBanner() {
  const [phase, setPhase] = useState<Phase>('idle');
  const [version, setVersion] = useState('');
  const [notes, setNotes] = useState('');
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const updateRef = useRef<any>(null);
  const manualRef = useRef(false);

  const check = useCallback(async (manual = false) => {
    if (!isTauri()) return;
    manualRef.current = manual;
    try {
      const { check } = await import('@tauri-apps/plugin-updater');
      const update = await check();
      if (update) {
        updateRef.current = update;
        setVersion(update.version);
        setNotes(update.body ?? '');
        setPhase('available');
      } else if (manual) {
        setPhase('uptodate');
        setTimeout(() => setPhase((p) => (p === 'uptodate' ? 'idle' : p)), 4000);
      }
    } catch (err) {
      if (manual) {
        setError(String(err));
        setPhase('error');
      }
      console.warn('[orion] update check failed', err);
    }
  }, []);

  useEffect(() => {
    if (!isTauri()) return;
    const first = window.setTimeout(() => check(false), 15_000);
    const every = window.setInterval(() => check(false), CHECK_EVERY_MS);
    let off = () => {};
    onDesktopEvent('orion://check-update', () => check(true)).then((u) => (off = u));
    return () => {
      window.clearTimeout(first);
      window.clearInterval(every);
      off();
    };
  }, [check]);

  const install = async () => {
    const update = updateRef.current;
    if (!update) return;
    setPhase('downloading');
    setProgress(0);
    try {
      let total = 0;
      let done = 0;
      await update.download((event: any) => {
        if (event.event === 'Started') total = event.data.contentLength ?? 0;
        if (event.event === 'Progress') {
          done += event.data.chunkLength;
          if (total) setProgress(done / total);
        }
      });
      setPhase('installing');
      await desktop.stopDaemon(); // release port 9120 and files before the installer runs
      await update.install();
      const { relaunch } = await import('@tauri-apps/plugin-process');
      await relaunch();
    } catch (err) {
      setError(String(err));
      setPhase('error');
      desktop.restartDaemon();
    }
  };

  const show = phase !== 'idle';
  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -12 }}
          className="fixed right-4 top-10 z-[85] w-[340px]"
        >
          <div className="emboss rounded-2xl p-3.5 space-y-2 border border-[#00E5FF]/30">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-[12.5px] font-bold text-[#A5F3FC]">
                {phase === 'error' ? <AlertTriangle size={14} className="text-[#FF8FAB]" /> : <Sparkles size={14} />}
                {phase === 'available' && `Orion ${version} is ready`}
                {phase === 'downloading' && `Downloading Orion ${version}…`}
                {phase === 'installing' && 'Installing — Orion will restart'}
                {phase === 'uptodate' && 'Orion is up to date'}
                {phase === 'error' && 'Update failed'}
              </span>
              {(phase === 'available' || phase === 'error' || phase === 'uptodate') && (
                <button aria-label="Dismiss" onClick={() => setPhase('idle')} className="text-white/76 hover:text-white">
                  <X size={14} />
                </button>
              )}
            </div>
            {phase === 'available' && notes && (
              <p className="selectable max-h-28 overflow-y-auto whitespace-pre-wrap text-[12px] text-white/82">{notes}</p>
            )}
            {phase === 'error' && <p className="selectable text-[11.5px] text-[#FF8FAB] break-words">{error}</p>}
            {(phase === 'downloading' || phase === 'installing') && (
              <div className="emboss-well h-2 overflow-hidden rounded-full">
                <motion.div
                  className="h-full bg-gradient-to-r from-[#2E6BFF] to-[#00E5FF]"
                  animate={{ width: `${Math.round((phase === 'installing' ? 1 : progress) * 100)}%` }}
                />
              </div>
            )}
            {phase === 'available' && (
              <button
                onClick={install}
                className="emboss-pill w-full rounded-xl py-2 text-[12.5px] text-[#3DFFA8] flex items-center justify-center gap-1.5"
              >
                Restart to update
              </button>
            )}
            {phase === 'installing' && <Loader2 size={14} className="mx-auto animate-spin text-white/80" />}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
