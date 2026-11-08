import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Loader2, AlertTriangle, RefreshCw, FolderOpen, Download } from 'lucide-react';
import { desktop, onDesktopEvent, type DaemonStatus } from '../../lib/desktop';
import { isTauri } from '../../lib/tauri';

const READY = new Set(['running', 'external']);

/**
 * First-run / startup screen of the desktop app while the app prepares Orion's Python brain
 * (find Python, create the private environment, install packages, start the daemon).
 * Hidden as soon as the brain is online, or when the HUD socket is already connected.
 */
export default function BootOverlay({ connected }: { connected: boolean }) {
  const [status, setStatus] = useState<DaemonStatus | null>(null);
  const [installingPython, setInstallingPython] = useState(false);

  useEffect(() => {
    if (!isTauri()) return;
    desktop.daemonStatus().then((s) => s && setStatus(s));
    let off = () => {};
    onDesktopEvent<DaemonStatus>('orion://daemon', (s) => {
      setStatus(s);
      if (s.stage !== 'python_missing') setInstallingPython(false);
    }).then((u) => (off = u));
    return () => off();
  }, []);

  if (!isTauri() || !status) return null;
  const visible = !connected && !READY.has(status.stage);
  const failed = status.stage === 'error' || status.stage === 'python_missing';

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="boot"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-[80] flex items-center justify-center bg-[#05070E]/80 backdrop-blur-md"
        >
          <div className="emboss w-[440px] rounded-3xl p-6 text-center space-y-4">
            <motion.img
              src={`${import.meta.env.BASE_URL}black-hole-core.png`}
              alt=""
              className="mx-auto h-20 w-20"
              animate={{ rotate: 360 }}
              transition={{ duration: failed ? 60 : 6, repeat: Infinity, ease: 'linear' }}
            />
            <h2 className="text-[15px] text-white">{status.message}</h2>
            {status.detail && <p className="selectable text-[12px] text-white/60 break-words">{status.detail}</p>}

            {status.progress != null && !failed && (
              <div className="emboss-well h-2.5 overflow-hidden rounded-full">
                <motion.div
                  className="h-full rounded-full bg-gradient-to-r from-[#7B5CFF] to-[#00E5FF]"
                  animate={{ width: `${Math.round(status.progress * 100)}%` }}
                />
              </div>
            )}
            {!failed && status.progress == null && (
              <Loader2 size={18} className="mx-auto animate-spin text-[#00E5FF]" />
            )}

            {failed && (
              <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
                {status.stage === 'python_missing' && (
                  <button
                    onClick={() => {
                      setInstallingPython(true);
                      desktop.installPython();
                    }}
                    disabled={installingPython}
                    className="emboss-pill flex items-center gap-1.5 rounded-xl px-3 py-2 text-[12px] text-[#3DFFA8]"
                  >
                    {installingPython ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                    {installingPython ? 'Installing Python…' : 'Install Python'}
                  </button>
                )}
                <button
                  onClick={() => desktop.restartDaemon()}
                  className="emboss-pill flex items-center gap-1.5 rounded-xl px-3 py-2 text-[12px] text-white/90"
                >
                  <RefreshCw size={13} /> Retry
                </button>
                <button
                  onClick={() => desktop.openLogs()}
                  className="emboss-pill flex items-center gap-1.5 rounded-xl px-3 py-2 text-[12px] text-white/70"
                >
                  <FolderOpen size={13} /> Open logs
                </button>
              </div>
            )}
            {failed && status.stage === 'error' && (
              <p className="flex items-center justify-center gap-1 text-[11px] text-[#FF8FAB]">
                <AlertTriangle size={11} /> The HUD keeps working; voice and agents need the brain.
              </p>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
