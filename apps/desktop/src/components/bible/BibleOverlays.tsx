/** Floating Feature Bible surfaces: clipboard alchemy pill, voice-reply staging, macro save, snippets,
 *  asset pipeline pills, comms digest, producer fact cards, breathwork vortex, wind-down notice. */
import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Check,
  Clipboard,
  Copy,
  FolderOpen,
  Image as ImageIcon,
  Moon,
  Radio,
  Send,
  Sparkles,
  Wand2,
  X,
} from 'lucide-react';
import type { BibleSend, BibleState } from '../../hooks/bibleState';
import { PillButton } from './ui';
import '../../styles/glass.css';

interface Props {
  bible: BibleState;
  send: BibleSend;
  patch: (p: Partial<BibleState>) => void;
}

const card =
  'orion-glass orion-glass--dense pointer-events-auto p-3 w-[min(360px,calc(100vw-32px))] text-starlight';
const spring = { type: 'spring', stiffness: 380, damping: 30 } as const;

export default function BibleOverlays({ bible, send, patch }: Props) {
  // producer fact cards fade on their own
  useEffect(() => {
    if (!bible.producerCard) return;
    const t = setTimeout(() => patch({ producerCard: null }), 14_000);
    return () => clearTimeout(t);
  }, [bible.producerCard, patch]);
  useEffect(() => {
    if (!bible.replyResult) return;
    const t = setTimeout(() => patch({ replyResult: null }), 5000);
    return () => clearTimeout(t);
  }, [bible.replyResult, patch]);
  useEffect(() => {
    if (!bible.clipboard) return;
    const t = setTimeout(() => patch({ clipboard: null }), 25_000);
    return () => clearTimeout(t);
  }, [bible.clipboard, patch]);

  return (
    <>
      {/* recording indicator */}
      <AnimatePresence>
        {bible.recording && (
          <motion.button
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            onClick={(e) => {
              e.stopPropagation();
              send('MACRO_STOP');
            }}
            className="fixed top-12 left-1/2 -translate-x-1/2 z-[70] emboss-pill rounded-full px-3 py-1.5 text-[11.5px] font-semibold text-[#FF5C8A] flex items-center gap-2"
            title="Click or say “stop watching”"
          >
            <span className="w-2.5 h-2.5 rounded-full bg-[#FF5C8A] animate-pulse" />
            Watching & learning — click to stop
          </motion.button>
        )}
      </AnimatePresence>

      {/* producer silent fact card (top centre) */}
      <AnimatePresence>
        {bible.producerCard && (
          <motion.div
            key={bible.producerCard.at}
            initial={{ opacity: 0, y: -16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -16 }}
            transition={spring}
            className={`fixed top-24 left-1/2 -translate-x-1/2 z-[65] ${card}`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-1.5 text-[11px] font-mono uppercase text-[#FF2A85] mb-1">
              <Radio size={11} /> Fact card
            </div>
            <div className="text-xs font-semibold mb-1">{bible.producerCard.title}</div>
            {bible.producerCard.hits.slice(0, 2).map((h) => (
              <p key={h.id} className="text-[11.5px] text-white/85 line-clamp-3">
                {h.content}
              </p>
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {/* bottom-right stack */}
      <div className="fixed bottom-40 right-4 z-[60] flex flex-col items-end gap-2 pointer-events-none">
        <AnimatePresence>
          {bible.stagedReply && (
            <motion.div key="reply" initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 30 }} transition={spring} className={card} onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center gap-1.5 text-[11px] font-mono uppercase text-[#26A5E4] mb-1.5">
                <Send size={11} /> Reply to {bible.stagedReply.name} · {bible.stagedReply.platform}
              </div>
              <p className="text-sm leading-snug mb-2.5 select-text">“{bible.stagedReply.text}”</p>
              <div className="flex gap-1.5 justify-end">
                <PillButton color="#8F90A6" onClick={() => send('VOICE_REPLY_CANCEL')}>
                  <X size={11} /> Cancel
                </PillButton>
                <PillButton color="#3DFFA8" onClick={() => send('VOICE_REPLY_SEND')}>
                  <Check size={11} /> Send it
                </PillButton>
              </div>
              <div className="text-[11px] text-white/72 mt-1.5">Or just say “send it”.</div>
            </motion.div>
          )}

          {bible.replyResult && (
            <motion.div key="reply-result" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className={`${card} !w-auto text-[11.5px]`}>
              {bible.replyResult.sent ? (
                <span className="text-[#3DFFA8]">✓ Sent</span>
              ) : (
                <span className="text-[#FF5C8A]">Couldn't send: {bible.replyResult.detail}</span>
              )}
            </motion.div>
          )}

          {bible.clipboard && (
            <motion.div key={bible.clipboard.id} initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 30 }} transition={spring} className={card} onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-1.5">
                <span className="flex items-center gap-1.5 text-[11px] font-mono uppercase text-[#FFB300]">
                  <Clipboard size={11} /> Copied {bible.clipboard.kind.replace('_', ' ')}
                </span>
                <button type="button" className="text-white/72 hover:text-white" onClick={() => patch({ clipboard: null })}>
                  <X size={12} />
                </button>
              </div>
              <pre className="text-[11px] font-mono text-white/78 line-clamp-3 whitespace-pre-wrap break-all mb-2">{bible.clipboard.preview}</pre>
              <div className="flex flex-wrap gap-1.5">
                {bible.clipboard.actions.map((a) => (
                  <PillButton
                    key={a.id}
                    color="#FFB300"
                    onClick={() => {
                      send('CLIPBOARD_ACTION', { id: bible.clipboard!.id, action: a.id });
                      patch({ clipboard: null });
                    }}
                  >
                    <Wand2 size={11} /> {a.label}
                  </PillButton>
                ))}
              </div>
            </motion.div>
          )}

          {bible.pendingMacro && <MacroSave key="macro" bible={bible} send={send} patch={patch} />}

          {bible.snippet && (
            <motion.div key="snippet" initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 30 }} className={card} onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] font-mono uppercase text-[#FFB300]">Snippet · {bible.snippet.description}</span>
                <button type="button" className="text-white/72 hover:text-white" onClick={() => patch({ snippet: null })}>
                  <X size={12} />
                </button>
              </div>
              <pre className="max-h-40 overflow-auto text-[11px] font-mono text-white/85 bg-black/50 rounded-lg p-2 whitespace-pre-wrap select-text">
                {bible.snippet.snippet}
              </pre>
              <div className="flex justify-end mt-2">
                <PillButton
                  color="#FFB300"
                  onClick={() => {
                    void navigator.clipboard?.writeText(bible.snippet?.snippet ?? '');
                    patch({ snippet: null });
                  }}
                >
                  <Copy size={11} /> Copy
                </PillButton>
              </div>
            </motion.div>
          )}

          {bible.assets.slice(0, 2).map((a) => (
            <motion.div key={a.id} initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 30 }} className={card} onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-1.5">
                <span className="flex items-center gap-1.5 text-[11px] font-mono uppercase text-[#7FA6FF] truncate">
                  <ImageIcon size={11} /> {a.name}
                </span>
                <button
                  type="button"
                  className="text-white/72 hover:text-white"
                  onClick={() => patch({ assets: bible.assets.filter((x) => x.id !== a.id) })}
                >
                  <X size={12} />
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {a.actions.map((act) => (
                  <PillButton
                    key={act.id}
                    color="#7FA6FF"
                    disabled={!act.enabled}
                    title={act.reason ?? undefined}
                    onClick={() => send('ASSET_ACTION', { asset_id: a.id, action: act.id })}
                  >
                    <Sparkles size={11} /> {act.label}
                  </PillButton>
                ))}
                {!a.actions.length && <span className="text-[11.5px] text-white/74">No pipeline actions for this file type.</span>}
              </div>
              {a.error && <div className="text-[11px] text-[#FF5C8A] mt-1.5">{a.error}</div>}
              {a.outputs.length > 0 && (
                <div className="mt-2 space-y-0.5">
                  {a.outputs.map((o) => (
                    <div key={o.path} className="text-[11px] font-mono text-[#3DFFA8] truncate">
                      ✓ {o.name} · {Math.round(o.bytes / 1024)} KB
                    </div>
                  ))}
                  <PillButton color="#3DFFA8" onClick={() => send('ASSET_REVEAL', { asset_id: a.id })}>
                    <FolderOpen size={11} /> Show in folder
                  </PillButton>
                </div>
              )}
            </motion.div>
          ))}

          {bible.briefing && (
            <motion.div key={`brief-${bible.briefing.at}`} initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 30 }} className={card} onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] font-mono uppercase text-[#FDE68A] truncate">Briefing · {bible.briefing.name}</span>
                <button type="button" className="text-white/72 hover:text-white" onClick={() => patch({ briefing: null })}>
                  <X size={12} />
                </button>
              </div>
              <p className="text-[12px] text-white/85 leading-relaxed select-text">{bible.briefing.text}</p>
              <div className="text-[11px] text-white/72 mt-1.5">Saved to your knowledge vault.</div>
            </motion.div>
          )}

          {bible.digest && (
            <motion.div key={`digest-${bible.digest.at}`} initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 30 }} className={card} onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] font-mono uppercase text-[#26A5E4]">
                  Muted-chat digest · {bible.digest.count} messages
                </span>
                <button type="button" className="text-white/72 hover:text-white" onClick={() => patch({ digest: null })}>
                  <X size={12} />
                </button>
              </div>
              <p className="text-[11.5px] text-white/80 whitespace-pre-line">{bible.digest.summary}</p>
            </motion.div>
          )}

          {bible.windDownNotice && bible.circadian && (
            <motion.div key="wind" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className={card} onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-[11.5px] text-[#FFB300]">
                  <Moon size={12} /> Wind-down: bedtime {bible.circadian.bedtime}
                </span>
                <button type="button" className="text-white/72 hover:text-white" onClick={() => patch({ windDownNotice: false })}>
                  <X size={12} />
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <AnimatePresence>{bible.breathwork && <Breathwork bible={bible} patch={patch} />}</AnimatePresence>
      <AnimatePresence>{bible.sanctuary && <Sanctuary key={bible.sanctuary.id} bible={bible} send={send} patch={patch} />}</AnimatePresence>
      <XpToast bible={bible} patch={patch} />
    </>
  );
}

function MacroSave({ bible, send, patch }: Props) {
  const [name, setName] = useState('');
  const [trigger, setTrigger] = useState('');
  const m = bible.pendingMacro!;
  const save = () => {
    const n = name.trim() || 'Recorded macro';
    const triggers = (trigger.trim() ? trigger : n).split(',').map((t) => t.trim()).filter(Boolean);
    send('MACRO_SAVE', { name: n, triggers });
    patch({ pendingMacro: null });
  };
  return (
    <motion.div initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 30 }} className={card} onClick={(e) => e.stopPropagation()}>
      <div className="text-[11px] font-mono uppercase text-[#7FA6FF] mb-1">Save recorded macro</div>
      <p className="text-[11px] text-white/78 mb-2 line-clamp-3">{m.summary}</p>
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Name (e.g. Export WoW patch)"
        className="w-full mb-1.5 rounded-lg bg-black/40 border border-white/10 px-2 py-1 text-[11.5px] text-starlight outline-none select-text"
      />
      <input
        value={trigger}
        onChange={(e) => setTrigger(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && save()}
        placeholder="Voice triggers, comma separated (e.g. export the patch)"
        className="w-full mb-2 rounded-lg bg-black/40 border border-white/10 px-2 py-1 text-[11.5px] text-starlight outline-none select-text"
      />
      <div className="flex justify-end gap-1.5">
        <PillButton
          color="#8F90A6"
          onClick={() => {
            send('MACRO_DISCARD');
            patch({ pendingMacro: null });
          }}
        >
          Discard
        </PillButton>
        <PillButton color="#7FA6FF" onClick={save}>
          <Check size={11} /> Save to Grimoire
        </PillButton>
      </div>
    </motion.div>
  );
}

/** 4-4-4-4 box breathing paced by a growing / shrinking vortex. */
function Breathwork({ bible, patch }: Pick<Props, 'bible' | 'patch'>) {
  const routine = bible.breathwork!;
  const total = Math.round((routine.duration_min ?? 3) * 60);
  const [t, setT] = useState(0);
  useEffect(() => {
    const i = setInterval(() => setT((x) => x + 1), 1000);
    return () => clearInterval(i);
  }, []);
  useEffect(() => {
    if (t >= total) patch({ breathwork: null });
  }, [t, total, patch]);
  const phase = Math.floor(t / 4) % 4; // 0 in, 1 hold, 2 out, 3 hold
  const label = ['Breathe in', 'Hold', 'Breathe out', 'Hold'][phase];
  const scale = phase === 0 || phase === 1 ? 1.35 : 0.75;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[90] flex flex-col items-center justify-center bg-[#05070E]/92 backdrop-blur-md"
      onClick={(e) => e.stopPropagation()}
    >
      <motion.div
        animate={{ scale, rotate: t * 12 }}
        transition={{ duration: 4, ease: 'easeInOut' }}
        className="w-56 h-56 rounded-full"
        style={{
          background:
            'conic-gradient(from 0deg, #2E6BFF, #00E5FF, #7FA6FF, #2E6BFF)',
          filter: 'blur(2px)',
          boxShadow: '0 0 80px #2E6BFF, inset 0 0 60px #05070E',
          maskImage: 'radial-gradient(circle, transparent 28%, black 30%)',
          WebkitMaskImage: 'radial-gradient(circle, transparent 28%, black 30%)',
        }}
      />
      <div className="mt-10 font-display text-2xl tracking-wide text-starlight">{label}</div>
      <div className="mt-2 text-xs font-mono text-white/76">
        {routine.title} · {Math.max(0, total - t)}s left
      </div>
      <button
        type="button"
        onClick={() => patch({ breathwork: null })}
        className="mt-8 emboss-pill rounded-full px-4 py-1.5 text-xs text-white/85 hover:text-white"
      >
        I'm done
      </button>
    </motion.div>
  );
}


/** Sanctuary Mode: guided recovery after a full focus lap. A soft gate — "Skip this once" always works. */
function Sanctuary({ bible, send, patch }: Props) {
  const routine = bible.sanctuary!;
  const total = Math.round((routine.duration_min ?? 5) * 60);
  const [left, setLeft] = useState(total);
  useEffect(() => {
    const t = setInterval(() => setLeft((x) => Math.max(0, x - 1)), 1000);
    return () => clearInterval(t);
  }, []);
  const steps = (routine.detail || '')
    .split(/\n|,|\+|;| then /i)
    .map((x) => x.trim().replace(/\.$/, ''))
    .filter(Boolean)
    .slice(0, 5);
  const finish = (completed: boolean) => {
    send('SANCTUARY_DONE', { routine: routine.id, completed });
    patch({ sanctuary: null });
  };
  const mm = Math.floor(left / 60);
  const ss = String(left % 60).padStart(2, '0');

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[85] flex items-center justify-center bg-[radial-gradient(circle_at_50%_45%,rgba(120,53,15,0.55),rgba(5,7,14,0.92)_70%)] backdrop-blur-sm"
      onClick={(e) => e.stopPropagation()}
      role="dialog"
      aria-label="Sanctuary: recovery break"
    >
      <div className="w-[min(420px,calc(100vw-32px))] rounded-3xl border border-amber-200/15 bg-black/55 backdrop-blur-xl p-6 text-white text-center">
        <div className="text-[12px] text-amber-200/80 mb-1">Sanctuary</div>
        <h2 className="text-[22px] font-bold mb-1">{routine.title}</h2>
        <div className="text-[42px] font-bold tabular-nums text-amber-100 my-3">
          {mm}:{ss}
        </div>
        {steps.length > 0 && (
          <ol className="text-left text-[14px] text-white/80 space-y-1.5 mb-5 list-decimal pl-5">
            {steps.map((st) => (
              <li key={st}>{st}</li>
            ))}
          </ol>
        )}
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => finish(true)}
            className={`rounded-2xl px-4 py-3 text-[15px] font-bold transition-colors ${
              left === 0 ? 'bg-amber-200 text-black hover:bg-amber-100' : 'bg-white/10 text-white hover:bg-white/15'
            }`}
          >
            {left === 0 ? 'Unlock the next focus block' : "I'm done"}
          </button>
          <button type="button" onClick={() => finish(false)} className="text-[12px] text-white/74 hover:text-white/80 py-1">
            Skip this once
          </button>
        </div>
      </div>
    </motion.div>
  );
}

/** Small "+XP" toast; a level-up gets a bigger moment. */
function XpToast({ bible, patch }: Pick<Props, 'bible' | 'patch'>) {
  const award = bible.xpToast;
  useEffect(() => {
    if (!award) return;
    const t = setTimeout(() => patch({ xpToast: null }), award.level_up ? 6000 : 2600);
    return () => clearTimeout(t);
  }, [award, patch]);
  return (
    <AnimatePresence>
      {award && (
        <motion.div
          key={award.at}
          initial={{ opacity: 0, y: 12, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -8 }}
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[70] pointer-events-none rounded-full border border-white/10 bg-black/60 backdrop-blur-xl px-4 py-2 text-white text-[13px] font-bold shadow-[0_10px_30px_rgba(0,0,0,0.5)]"
        >
          {award.level_up ? `Level ${award.level_up}! New look unlocked in Life` : `+${award.xp} XP · ${award.note || award.kind.replace('_', ' ')}`}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
