/** Phone layout: the widget column folds into one row of glanceable chips under the core. */
import { Bell, MessageCircle, Pause, Play } from 'lucide-react';
import type { ApprovalItem, CommsThread } from '@orion/shared-types';
import type { BibleSend, BibleState } from '../../hooks/bibleState';
import '../../styles/glass.css';

interface Props {
  bible: BibleState;
  send: BibleSend;
  threads: CommsThread[];
  approvals: ApprovalItem[];
  onOpen: (tab: 'comms' | 'queue') => void;
}

// Orion Glass chips (styles/glass.css); the pill radius is set inline because .orion-glass sets 16px corners.
const chip = 'orion-glass flex items-center gap-1.5 min-w-0 px-3 py-2 text-[12.5px] active:scale-95 transition-transform';
const pill = { borderRadius: 999 };

export default function MobileGlance({ bible, send, threads, approvals, onOpen }: Props) {
  const unread = threads.filter((t) => !t.muted).reduce((a, t) => a + t.unread, 0);
  const waiting = approvals.filter((a) => a.status === 'pending').length;
  const m = bible.media;
  return (
    <div className="flex items-center justify-center gap-2 px-4 w-full max-w-[420px]" onClick={(e) => e.stopPropagation()}>
      {m && (
        <button
          type="button"
          className={`${chip} flex-1`}
          style={pill}
          onClick={() => send('MEDIA_CONTROL', { action: m.playing ? 'pause' : 'play' })}
          aria-label={m.playing ? `Pause ${m.title}` : `Play ${m.title}`}
        >
          {m.playing ? <Pause size={13} className="shrink-0" /> : <Play size={13} className="shrink-0" />}
          <span className="truncate">{m.title}</span>
        </button>
      )}
      <button type="button" className={chip} style={pill} onClick={() => onOpen('comms')} aria-label={`${unread} unread messages`}>
        <MessageCircle size={13} />
        <span>{unread}</span>
      </button>
      <button type="button" className={chip} style={pill} onClick={() => onOpen('queue')} aria-label={`${waiting} waiting for your OK`}>
        <Bell size={13} />
        <span>{waiting}</span>
      </button>
    </div>
  );
}
