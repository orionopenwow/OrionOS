import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Check,
  Pencil,
  X,
  GitPullRequestArrow,
  RotateCcw,
  Sparkles,
  Loader2,
  AlertTriangle,
  ExternalLink,
  ChevronDown,
  Brain,
  Wand2,
  FileCode2,
} from 'lucide-react';
import type { ApprovalItem, SelfEvolveStatusEvent, TrainingExportedEvent } from '@orion/shared-types';

interface Props {
  approvals: ApprovalItem[];
  selfEvolveStatus: SelfEvolveStatusEvent | null;
  trainingExport: TrainingExportedEvent['result'] | null;
  alignmentExamples?: number;
  isDeepMode: boolean;
  onDecide: (id: string, decision: 'approve' | 'edit' | 'reject', text?: string) => void;
  onPatch: (id: string, action: 'apply' | 'reject' | 'revert') => void;
  onRequestEvolve: (request: string) => void;
}

const CATEGORY_STYLE: Record<string, { label: string; color: string }> = {
  prompt: { label: 'Prompt', color: '#7FA6FF' },
  realm_pitch: { label: 'Realm Pitch', color: '#3DFFA8' },
  bug: { label: 'Bug Report', color: '#FF5C8A' },
  question: { label: 'Question', color: '#00E5FF' },
  self_evolve: { label: 'Self-Evolve Patch', color: '#FFB300' },
  grimoire: { label: 'Grimoire Command', color: '#00E5FF' },
  devlog: { label: 'Devlog Draft', color: '#7FA6FF' },
  boardroom: { label: 'Boardroom', color: '#FFB300' },
};

function timeAgo(ts: number) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** OrionOS boardroom decisions: shown here, but only the boardroom VM's signature can decide them. */
function BoardroomCard({ item }: { item: ApprovalItem }) {
  const pending = item.status === 'pending';
  const outcome = item.decision ?? (item.status === 'rejected' ? 'rejected' : 'approved');
  return (
    <div className="emboss rounded-2xl p-3.5 space-y-2.5">
      <div className="flex items-center justify-between gap-2 text-[11.5px]">
        <span
          className="px-2 py-0.5 rounded-full font-bold shrink-0"
          style={{ color: '#FFB300', background: '#FFB30022', border: '1px solid #FFB30055' }}
        >
          Boardroom
        </span>
        <span className="text-white/74 shrink-0">{timeAgo(item.created_at)}</span>
      </div>
      <div className="emboss-well rounded-xl p-2.5 text-[12px] leading-relaxed">
        <div className="text-[11.5px] font-bold text-[#A5B4FC] mb-0.5">{item.author}</div>
        <p className="selectable text-white/85 whitespace-pre-wrap">{item.original}</p>
      </div>
      {item.error && (
        <div className="flex items-center gap-1.5 text-[11.5px] text-[#FF8FAB]">
          <AlertTriangle size={12} /> {item.error}
        </div>
      )}
      <div className="text-[11.5px] text-white/78">
        {pending ? 'Waiting for a signature on the boardroom VM. It cannot be approved from here.' : `Signed: ${outcome}`}
      </div>
    </div>
  );
}

function DiscordApprovalCard({ item, onDecide }: { item: ApprovalItem; onDecide: Props['onDecide'] }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.draft ?? '');
  const cat = CATEGORY_STYLE[item.category ?? 'question'] ?? CATEGORY_STYLE.question;
  const isCommand = item.kind === 'grimoire_run';
  const isDevlog = item.kind === 'devlog';
  const [showChangelog, setShowChangelog] = useState(false);
  const pending = item.status === 'pending' || item.status === 'sending';
  // One decision per click: buttons lock until the daemon reports back (prevents double posts).
  const [submitting, setSubmitting] = useState(false);
  const locked = submitting || item.status === 'sending';

  useEffect(() => {
    if (!editing) setText(item.draft ?? '');
  }, [item.draft, editing]);

  useEffect(() => {
    setSubmitting(false);
  }, [item.status, item.error]);

  useEffect(() => {
    if (!submitting) return;
    const t = window.setTimeout(() => setSubmitting(false), 30_000);
    return () => window.clearTimeout(t);
  }, [submitting]);

  const decide = (decision: 'approve' | 'edit' | 'reject', value?: string) => {
    if (locked) return;
    setSubmitting(true);
    onDecide(item.id, decision, value);
  };

  return (
    <div className="emboss rounded-2xl p-3.5 space-y-2.5">
      <div className="flex items-center justify-between gap-2 text-[11.5px]">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="px-2 py-0.5 rounded-full font-bold shrink-0"
            style={{ color: cat.color, background: `${cat.color}22`, border: `1px solid ${cat.color}55` }}
          >
            {cat.label}
          </span>
          <span className="text-white/80 font-semibold truncate">
            {item.guild ? `${item.guild} · ` : ''}
            {item.channel}
          </span>
        </div>
        <div className="flex items-center gap-2 text-white/74 shrink-0">
          {item.jump_url && (
            <a href={item.jump_url} target="_blank" rel="noreferrer" className="hover:text-white" title="Open in Discord">
              <ExternalLink size={12} />
            </a>
          )}
          <span>{timeAgo(item.created_at)}</span>
        </div>
      </div>

      <div className="emboss-well rounded-xl p-2.5 text-[12px] leading-relaxed">
        <div className="text-[11.5px] font-bold text-[#A5B4FC] mb-0.5">{isDevlog ? item.title ?? 'Devlog' : item.author}</div>
        <p className="selectable text-white/85 whitespace-pre-wrap">{item.original}</p>
        {isDevlog && item.changelog && (
          <>
            <button type="button" onClick={() => setShowChangelog(!showChangelog)} className="mt-1 text-[11px] text-[#7FA6FF]">
              {showChangelog ? 'Hide' : 'Show'} technical changelog
            </button>
            {showChangelog && (
              <pre className="selectable mt-1 max-h-40 overflow-y-auto text-[11px] text-white/82 whitespace-pre-wrap">{item.changelog}</pre>
            )}
          </>
        )}
      </div>
      {isCommand && item.destructive && (
        <div className="flex items-center gap-1.5 text-[11.5px] text-[#FF8FAB]">
          <AlertTriangle size={12} /> This command can delete or overwrite things. Read it before running.
        </div>
      )}

      <div className="space-y-1">
        <div className="flex items-center justify-between text-[11px] text-white/74 px-0.5">
          <span className="flex items-center gap-1">
            <Sparkles size={11} className="text-[#7FA6FF]" />
            {isCommand ? 'Command to run' : isDevlog ? 'Community post (Discord)' : 'Draft in your voice'}
            {item.examples_used ? ` · ${item.examples_used} past edits used` : ''}
          </span>
          {!isCommand && !isDevlog && (
            <span className="truncate max-w-[220px]">
              {item.models?.tier1 ?? 'heuristic'} → {item.models?.tier2 ?? 'no model'}
            </span>
          )}
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          readOnly={!editing || !pending}
          rows={Math.min(8, Math.max(3, Math.ceil(text.length / 70)))}
          placeholder={item.draft ? '' : 'No draft generated — write the reply yourself with Edit & Send.'}
          className={`selectable w-full resize-none rounded-xl px-3 py-2 text-[12.5px] leading-relaxed text-starlight focus:outline-none ${
            editing ? 'emboss-well border border-[#7FA6FF]/60' : 'bg-white/[0.04] border border-white/10'
          }`}
        />
      </div>

      {item.error && (
        <div className="flex items-center gap-1.5 text-[11.5px] text-[#FF8FAB]">
          <AlertTriangle size={12} /> {item.error}
        </div>
      )}

      {item.status === 'sending' && (
        <div className="flex items-center gap-1.5 text-[11.5px] text-white/80">
          <Loader2 size={12} className="animate-spin" /> {isCommand ? 'Running…' : `Sending to ${item.channel}…`}
        </div>
      )}
      {pending ? (
        <div className="flex items-center gap-2">
          <button
            onClick={() => decide('approve')}
            disabled={!item.draft || locked}
            className="emboss-pill flex-1 flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[12px] text-[#3DFFA8]"
          >
            <Check size={14} /> {isCommand ? 'Run' : 'Approve'}
          </button>
          <button
            onClick={() => {
              if (!editing) {
                setEditing(true);
                return;
              }
              if (text.trim()) decide('edit', text.trim());
            }}
            disabled={locked}
            data-active={editing}
            className="emboss-pill flex-1 flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[12px] text-[#DCE7FF]"
          >
            <Pencil size={13} /> {editing ? (isCommand ? 'Run Edited' : 'Send Edit') : isCommand ? 'Edit & Run' : 'Edit & Send'}
          </button>
          <button
            onClick={() => decide('reject')}
            disabled={locked}
            className="emboss-pill flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[12px] text-[#FF8FAB]"
          >
            <X size={14} /> Ignore
          </button>
        </div>
      ) : (
        <div className="text-[11.5px] text-white/78">
          {item.status === 'sent'
            ? isCommand
              ? '✓ Ran — see the Tools tab'
              : isDevlog
              ? item.posted
                ? '✓ Posted to Discord'
                : '✓ Approved (copy it — no devlog channel set in comms_routing.json)'
              : `✓ Sent${item.decision === 'edited' ? ' (edited — learned)' : ''}`
            : item.status === 'rejected'
            ? '✕ Ignored'
            : `✕ ${item.status}`}
        </div>
      )}
    </div>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="selectable emboss-well max-h-72 overflow-auto rounded-xl p-2.5 text-[11.5px] leading-[1.45] font-mono">
      <div className="min-w-max">
      {diff.split('\n').map((line, i) => {
        const color = line.startsWith('+++') || line.startsWith('---')
          ? 'text-white/76'
          : line.startsWith('+')
          ? 'text-[#3DFFA8] bg-[#3DFFA8]/[0.06]'
          : line.startsWith('-')
          ? 'text-[#FF8FAB] bg-[#FF5C8A]/[0.07]'
          : line.startsWith('@@')
          ? 'text-[#00E5FF]'
          : 'text-white/85';
        return (
          <div key={i} className={`${color} whitespace-pre px-1`}>
            {line || ' '}
          </div>
        );
      })}
      </div>
    </pre>
  );
}

function PatchCard({ item, onPatch }: { item: ApprovalItem; onPatch: Props['onPatch'] }) {
  const patch = item.patch;
  const [showDiff, setShowDiff] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    setSubmitting(false);
  }, [item.status, item.error]);
  useEffect(() => {
    if (!submitting) return;
    const t = window.setTimeout(() => setSubmitting(false), 150_000); // apply includes an import check
    return () => window.clearTimeout(t);
  }, [submitting]);
  if (!patch) return null;
  const status = item.status;
  const act = (action: 'apply' | 'reject' | 'revert') => {
    if (submitting) return;
    setSubmitting(true);
    onPatch(patch.id, action);
  };

  return (
    <div className="emboss rounded-2xl p-3.5 space-y-2.5 border border-[#FFB300]/25">
      <div className="flex items-center justify-between text-[11.5px]">
        <span className="flex items-center gap-1.5 font-bold text-[#FFB300]">
          <GitPullRequestArrow size={13} /> Self-Evolve Patch
        </span>
        <span className="text-white/74">
          {patch.model ?? ''} · {timeAgo(patch.created_at)}
        </span>
      </div>
      <div className="text-[11.5px] text-white/78 italic">“{patch.request}”</div>
      <p className="selectable text-[12.5px] text-white/90 leading-relaxed">{patch.summary}</p>

      <div className="flex flex-wrap gap-1.5">
        {patch.files.map((f) => (
          <span key={f.path} className="emboss-well flex items-center gap-1 rounded-lg px-2 py-0.5 text-[11.5px] text-white/80">
            <FileCode2 size={11} className="text-[#00E5FF]" />
            {f.path.split('/').slice(-2).join('/')}
            <span className="text-[#3DFFA8]">+{f.additions}</span>
            <span className="text-[#FF8FAB]">−{f.deletions}</span>
            {f.action === 'create' && <span className="text-[#FFB300]">new</span>}
          </span>
        ))}
      </div>
      <div className="flex gap-1.5 text-[11px]">
        {patch.touches_daemon && <span className="px-1.5 py-0.5 rounded bg-[#7FA6FF]/15 text-[#DCE7FF]">daemon restarts</span>}
        {patch.touches_ui && <span className="px-1.5 py-0.5 rounded bg-[#00E5FF]/15 text-[#A5F3FC]">UI hot-reloads</span>}
        {patch.touches_extension && <span className="px-1.5 py-0.5 rounded bg-white/10 text-white/85">reload extension</span>}
      </div>

      <button onClick={() => setShowDiff(!showDiff)} className="flex items-center gap-1 text-[11.5px] text-white/80 hover:text-white">
        <ChevronDown size={12} className={`transition-transform ${showDiff ? 'rotate-180' : ''}`} />
        {showDiff ? 'Hide' : 'Show'} diff preview
      </button>
      {showDiff && <DiffView diff={patch.diff} />}

      {item.error && (
        <div className="flex items-center gap-1.5 text-[11.5px] text-[#FF8FAB]">
          <AlertTriangle size={12} /> {item.error}
        </div>
      )}

      {status === 'pending' && (
        <div className="flex gap-2">
          <button
            onClick={() => act('apply')}
            disabled={submitting}
            className="emboss-pill flex-1 flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[12px] text-[#3DFFA8]"
          >
            {submitting ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            {submitting ? 'Applying & checking…' : 'Approve & Hot-Reload'}
          </button>
          <button
            onClick={() => act('reject')}
            disabled={submitting}
            className="emboss-pill flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[12px] text-[#FF8FAB]"
          >
            <X size={14} /> Reject
          </button>
        </div>
      )}
      {status === 'applied' && (
        <div className="flex items-center justify-between">
          <span className="text-[11.5px] text-[#3DFFA8]">✓ Applied</span>
          <button
            onClick={() => act('revert')}
            disabled={submitting}
            className="emboss-pill flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-[12px] text-white/85"
          >
            <RotateCcw size={12} /> Revert
          </button>
        </div>
      )}
      {(status === 'rejected' || status === 'reverted') && (
        <span className="text-[11.5px] text-white/76">{status === 'reverted' ? '↺ Reverted' : '✕ Rejected'}</span>
      )}
    </div>
  );
}

export default function ApprovalQueuePanel({
  approvals,
  selfEvolveStatus,
  trainingExport,
  alignmentExamples,
  isDeepMode,
  onDecide,
  onPatch,
  onRequestEvolve,
}: Props) {
  const [request, setRequest] = useState('');
  const [showResolved, setShowResolved] = useState(false);
  const isActive = (a: ApprovalItem) => a.status === 'pending' || a.status === 'sending';
  const pending = approvals.filter(isActive);
  const resolved = approvals.filter((a) => !isActive(a));
  const planning = selfEvolveStatus?.stage === 'planning';

  const renderItem = (item: ApprovalItem) => (
    <motion.div key={item.id} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }}>
      {item.kind === 'self_evolve' ? (
        <PatchCard item={item} onPatch={onPatch} />
      ) : item.kind === 'boardroom' ? (
        <BoardroomCard item={item} />
      ) : (
        <DiscordApprovalCard item={item} onDecide={onDecide} />
      )}
    </motion.div>
  );

  return (
    <div className="relative z-10 flex-1 flex flex-col min-h-0 overflow-y-auto p-4 space-y-3">
      {/* Learning status */}
      <div className="emboss rounded-2xl px-3.5 py-2.5 flex items-center justify-between gap-3 text-[12px]">
        <span className="flex items-center gap-2 text-white/80">
          <Brain size={14} className="text-[#7FA6FF]" />
          <span>
            <b className="text-white">{alignmentExamples ?? 0}</b> decisions learned
          </span>
        </span>
        <span className="text-white/74 truncate" title={trainingExport?.path}>
          {trainingExport ? `Fine-tune set: ${trainingExport.examples} examples` : 'Export runs on Consolidate Memories'}
        </span>
      </div>

      {/* Talk-to-Develop */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!request.trim() || planning) return;
          onRequestEvolve(request.trim());
          setRequest('');
        }}
        className="emboss rounded-2xl p-3 space-y-2"
      >
        <div className="flex items-center justify-between text-[11.5px]">
          <span className="flex items-center gap-1.5 font-bold text-[#FFB300] text-emboss-header">
            <Wand2 size={13} /> Talk-to-Develop
          </span>
          <span className="text-white/74">
            {isDeepMode ? 'Voice requests active (Deep mode)' : 'Switch to 📜 Deep for voice requests'}
          </span>
        </div>
        <div className="flex gap-2">
          <input
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            placeholder="Describe a feature for Orion to build into itself…"
            className="emboss-well flex-1 rounded-xl px-3 py-2 text-[12px] text-starlight placeholder:text-white/55 focus:outline-none"
          />
          <button type="submit" disabled={!request.trim() || planning} className="emboss-pill rounded-xl px-3 py-2 text-[12px] text-[#FFB300]">
            {planning ? <Loader2 size={14} className="animate-spin" /> : 'Generate'}
          </button>
        </div>
        {selfEvolveStatus && selfEvolveStatus.stage !== 'ready' && (
          <div className={`text-[11.5px] flex items-center gap-1.5 ${selfEvolveStatus.stage === 'failed' ? 'text-[#FF8FAB]' : 'text-white/80'}`}>
            {selfEvolveStatus.stage === 'planning' ? <Loader2 size={11} className="animate-spin" /> : <AlertTriangle size={11} />}
            {selfEvolveStatus.stage === 'planning'
              ? `Planning patch: “${selfEvolveStatus.request.slice(0, 80)}”`
              : selfEvolveStatus.error}
          </div>
        )}
      </form>

      <div className="flex items-center justify-between px-1 text-[11.5px] text-white/78 uppercase tracking-wider">
        <span>Awaiting approval ({pending.length})</span>
      </div>

      <AnimatePresence initial={false}>{pending.map(renderItem)}</AnimatePresence>
      {pending.length === 0 && (
        <div className="py-8 text-center text-[12px] text-white/76 leading-relaxed px-6">
          Nothing to review. Orion triages your community channels and drops drafted replies here — nothing is sent
          without your click.
        </div>
      )}

      {resolved.length > 0 && (
        <div className="space-y-2">
          <button onClick={() => setShowResolved(!showResolved)} className="flex items-center gap-1 px-1 text-[11.5px] text-white/76 hover:text-white">
            <ChevronDown size={12} className={`transition-transform ${showResolved ? 'rotate-180' : ''}`} />
            Recently resolved ({resolved.length})
          </button>
          {showResolved && resolved.slice(0, 15).map(renderItem)}
        </div>
      )}
    </div>
  );
}
