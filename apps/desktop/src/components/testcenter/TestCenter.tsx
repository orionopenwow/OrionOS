/**
 * Orion Test Center: one calm window that walks you through everything worth checking on the PC and the phone.
 *   1. Opening it runs the automatic checks that haven't passed yet ("Run all automatic checks" runs them all;
 *      this window also checks that the black hole can draw and which quality it picked).
 *   2. Then one "Next" card at a time with one big "Do it for me" button: it does the harmless half of the test
 *      (speaks, sends the do-nothing test card, opens the practice page, builds a practice change...) and the card
 *      ticks itself off when Orion sees it work, with a soft chime, and moves on. A few ask a Yes / No question.
 *      Done / It didn't work / Skip stay as smaller buttons. Approving, merging and sending always stay yours.
 *   3. Group pills at the top, the groups underneath (collapsed), and "All done" with "Copy report" (a short
 *      report with no keys or names).
 *
 * Opens on:  window.dispatchEvent(new CustomEvent('orion:open', { detail: 'tests' }))   (the main menu)
 *            the daemon's HUD_OPEN {what: "tests"} event ("Orion, test everything", Test Orion.cmd, orion-ctl test)
 *            a pending open request when the window connects (GET /tests/open-request)
 * Data: apps/daemon/test_center.py (GET /tests, POST /tests/run-auto, /tests/{id}/run, /tests/{id}/result,
 * /tests/reset, GET /tests/report). Requests carry Orion's key through lib/daemonAuth.ts.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { cosmicAudio } from '../../lib/cosmicAudio';
import { AnimatePresence } from 'framer-motion';
import { CheckCircle2, ChevronDown, ChevronRight, ClipboardCopy, Eye, Loader2, Play, RotateCcw, Smartphone, Monitor, Wand2, XCircle } from 'lucide-react';
import GlassWindow from '../glass/GlassWindow';
import { ORION_HTTP_URL } from '../../lib/assets';
import { onDaemonEvent } from '../../lib/daemonEvents';
import { openOrion } from '../../lib/menu';
import { probeGpu, qualityName, resolveQuality } from '../../lib/blackhole/quality';
import { groupPills, newlyPassed, nextButtons, pollMs, statusMap, type TestStatus } from '../../lib/testFlow';
import PracticeBanner from './PracticeBanner';

const PREFILL_EVENT = 'orion:projects-prefill'; // ProjectsWindow.tsx

export const OPEN_EVENT = 'orion:open';
export const TESTS_DETAIL = 'tests';

type Status = TestStatus;
interface TestItem {
  id: string;
  group: string;
  title: string;
  where: 'pc' | 'phone' | 'both';
  kind: 'auto' | 'manual';
  status: Status;
  detail: string;
  next: string;
  action: string | null;
  open: string | null;
  note?: string;
  /** "Do it for me" label, or null when the PC can't do this one for you */
  do?: string | null;
  /** a Yes / No question waiting for your answer */
  ask?: string | null;
  /** how it ticks itself off */
  watch?: string;
  /** its action ran and Orion is watching for the result */
  armed?: boolean;
  /** the phone's own Test screen fills it in */
  by_phone?: boolean;
}
interface TestGroup {
  id: string;
  title: string;
  items: TestItem[];
}
interface TestView {
  groups: TestGroup[];
  next: TestItem | null;
  summary: { pass: number; fail: number; todo: number; skip: number };
  running?: boolean;
  all_done?: boolean;
}

const C = {
  text: '#DDE5F0',
  good: '#5CFFB0',
  bad: '#FF5C8A',
  warn: '#FFC66B',
  violet: '#2E6BFF',
  dim: '#5A6088',
};
const statusColor: Record<Status, string> = { todo: C.dim, running: C.warn, pass: C.good, fail: C.bad, skip: '#8A90B8' };
const statusWord: Record<Status, string> = { todo: 'To do', running: 'Checking…', pass: 'Works', fail: "Didn't work", skip: 'Skipped' };
const QUALITY_LABEL = ['Still', 'Battery', 'Balanced', 'High', 'Ultra'];

async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${ORION_HTTP_URL}${path}`, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { error?: string }).error ?? "Orion's brain didn't answer.");
  return (await r.json()) as T;
}

/** The window's own check: can the black hole draw here, and which quality did it pick? */
async function reportCoreLook() {
  let status: 'pass' | 'fail' = 'pass';
  let note: string;
  try {
    const gpu = probeGpu();
    const q = resolveQuality();
    if (!gpu) {
      status = 'fail';
      note = 'This window has no WebGL 2, so the black hole falls back to a still picture.';
    } else {
      const kind = gpu.software ? 'software drawing' : gpu.virtual ? 'a virtual machine' : gpu.renderer.slice(0, 60);
      note = `Quality: ${QUALITY_LABEL[q] ?? qualityName(q)}. Graphics: ${kind}.`;
    }
  } catch {
    status = 'fail';
    note = "The black hole couldn't start its graphics.";
  }
  await api('/tests/core_renders/result', { status, note, by: 'orion-window' }).catch(() => undefined);
}

function WhereBadge({ where }: { where: TestItem['where'] }) {
  const label = where === 'pc' ? 'On this PC' : where === 'phone' ? 'On your phone' : 'PC and phone';
  return (
    <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] text-[#DDE5F0]/85" style={{ background: 'rgba(221,229,240,0.07)' }}>
      {where === 'pc' ? <Monitor size={11} /> : where === 'phone' ? <Smartphone size={11} /> : (<><Monitor size={11} /><Smartphone size={11} /></>)}
      {label}
    </span>
  );
}

function Dot({ s }: { s: Status }) {
  if (s === 'running') return <Loader2 size={12} className="animate-spin shrink-0" style={{ color: C.warn }} />;
  if (s === 'pass') return <CheckCircle2 size={12} className="shrink-0" style={{ color: C.good }} />;
  if (s === 'fail') return <XCircle size={12} className="shrink-0" style={{ color: C.bad }} />;
  return <span aria-hidden className="mt-[3px] h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: statusColor[s] }} />;
}

/** A soft low hum when an item ticks itself off (quiet, short, never on the first load; follows the sound setting). */
function chime() {
  cosmicAudio.playSoftTick();
}

function Btn({ children, onClick, tone = 'plain', big, disabled }: { children: ReactNode; onClick: () => void; tone?: 'main' | 'good' | 'bad' | 'plain'; big?: boolean; disabled?: boolean }) {
  const bg = tone === 'main' ? 'rgba(46,107,255,0.35)' : tone === 'good' ? 'rgba(92,255,176,0.18)' : tone === 'bad' ? 'rgba(255,92,138,0.18)' : 'rgba(221,229,240,0.06)';
  const border = tone === 'main' ? C.violet : tone === 'good' ? 'rgba(92,255,176,0.55)' : tone === 'bad' ? 'rgba(255,92,138,0.55)' : '#3A4352';
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-full font-semibold text-white transition-opacity disabled:opacity-40 ${big ? 'px-5 py-2.5 text-[14px]' : 'px-3 py-1.5 text-[12px]'}`}
      style={{ background: bg, border: `1px solid ${border}` }}
    >
      {children}
    </button>
  );
}

export default function TestCenter({ mobile }: { mobile?: boolean }) {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState<TestView | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [flash, setFlash] = useState('');
  const prev = useRef<Record<string, Status>>({});
  const copyRef = useRef<HTMLButtonElement | null>(null);

  const take = useCallback((next: TestView) => {
    const passed = newlyPassed(prev.current, next.groups);
    prev.current = statusMap(next.groups);
    if (passed.length) {
      chime();
      setFlash(passed.length === 1 ? `That worked: ${passed[0].title}` : `${passed.length} more things work.`);
    }
    setV(next);
  }, []);

  const load = useCallback(async () => {
    try {
      take(await api<TestView>('/tests'));
    } catch (e) {
      setNote((e as Error).message || "Orion's brain isn't running.");
    }
  }, [take]);

  const runAll = useCallback(async (fresh = true) => {
    setBusy(true);
    setNote('');
    try {
      await reportCoreLook();
      take(await api<TestView>('/tests/run-auto', { fresh }));
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [take]);

  // ---- ways to open it
  useEffect(() => {
    const onOpen = (e: Event) => {
      if ((e as CustomEvent).detail === TESTS_DETAIL) setOpen(true);
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    const askPending = () => {
      api<{ open: boolean; run?: boolean }>('/tests/open-request')
        .then((r) => {
          if (r.open) setOpen(true);
        })
        .catch(() => undefined);
    };
    const off = onDaemonEvent((ev) => {
      if (ev.event === 'HUD_OPEN' && ev.what === TESTS_DETAIL) {
        setOpen(true);
      } else if (ev.event === 'HUD_OPEN' && typeof ev.what === 'string' && ev.what) {
        openOrion(String(ev.what)); // any other window the daemon asks for (projects, privacy, ...) opens through the menu
        if (ev.what === 'projects' && ev.prefill && typeof ev.prefill === 'object') {
          window.dispatchEvent(new CustomEvent(PREFILL_EVENT, { detail: ev.prefill })); // the Test Center's sample idea
        }
      } else if (ev.event === 'HELLO') {
        askPending();
      }
    });
    askPending();
    return () => {
      window.removeEventListener(OPEN_EVENT, onOpen);
      off();
    };
  }, []);

  // ---- opening it runs the automatic checks that haven't passed yet (and the window's own check)
  useEffect(() => {
    if (!open) return;
    prev.current = {};
    void runAll(false);
  }, [open, runAll]);

  // ---- keep it fresh while open (faster while something is checked or watched)
  const nx = v?.next ?? null;
  const every = pollMs(!!v?.running, nx);
  useEffect(() => {
    if (!open) return;
    const t = setInterval(load, every);
    return () => clearInterval(t);
  }, [open, load, every]);

  useEffect(() => {
    if (!flash) return;
    const t = window.setTimeout(() => setFlash(''), 3500);
    return () => window.clearTimeout(t);
  }, [flash]);

  const allDone = !!v?.all_done;
  useEffect(() => {
    if (open && allDone) window.setTimeout(() => copyRef.current?.focus(), 50);
  }, [open, allDone]);

  const act = async (item: TestItem, what: 'run' | 'pass' | 'fail' | 'skip' | 'todo') => {
    setBusy(true);
    setNote('');
    try {
      if (what === 'run') {
        if (item.id === 'core_renders') await reportCoreLook();
        else await api(`/tests/${item.id}/run`, {});
      } else {
        await api(`/tests/${item.id}/result`, { status: what });
      }
      await load();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const copyReport = async () => {
    try {
      const r = await api<{ markdown: string }>('/tests/report');
      await navigator.clipboard.writeText(r.markdown);
      setNote('Report copied. Paste it wherever you like; it has no keys or names in it.');
    } catch {
      setNote("Couldn't copy the report.");
    }
  };

  const reset = async () => {
    setBusy(true);
    try {
      prev.current = {};
      take(await api<TestView>('/tests/reset', {}));
      setNote('Started over.');
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const s = v?.summary;
  const total = s ? s.pass + s.fail + s.todo + s.skip : 0;
  const done = s ? s.pass + s.fail + s.skip : 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const pills = groupPills(v?.groups ?? []);
  const buttons = nx ? nextButtons(nx) : null;

  return (
    <AnimatePresence>
      {open && (
        <GlassWindow
          key="tests"
          id="tests"
          title="Test Center"
          subtitle="Orion checks itself; you press one button at a time"
          icon={<CheckCircle2 size={14} className="text-[#5CFFB0]" />}
          onClose={() => setOpen(false)}
          maximisable={false}
          mobile={mobile}
          className="right-4 top-14 w-[480px] max-w-[calc(100vw-32px)]"
        >
          <div className="max-h-[74vh] space-y-3 overflow-y-auto p-4" style={{ color: C.text }}>
            {/* progress + group pills */}
            <div className="space-y-2">
              <div className="h-1.5 rounded-full bg-white/10" aria-label={`${pct}% checked`}>
                <div className="h-1.5 rounded-full transition-all" style={{ width: `${pct}%`, background: s && s.fail ? C.warn : C.good }} />
              </div>
              <div className="flex flex-wrap gap-1.5" aria-label="Progress by group">
                {pills.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setExpanded((x) => ({ ...x, [p.id]: !x[p.id] }))}
                    title={`${p.title}: ${p.pass} of ${p.total} work${p.fail ? `, ${p.fail} didn't` : ''}`}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px]"
                    style={{
                      background: p.done ? 'rgba(92,255,176,0.14)' : p.fail ? 'rgba(255,92,138,0.14)' : 'rgba(221,229,240,0.06)',
                      border: `1px solid ${p.done ? 'rgba(92,255,176,0.45)' : p.fail ? 'rgba(255,92,138,0.45)' : 'rgba(221,229,240,0.12)'}`,
                    }}
                  >
                    {p.busy ? <Loader2 size={10} className="animate-spin" /> : p.done ? <CheckCircle2 size={10} style={{ color: C.good }} /> : null}
                    {p.title} <span className="text-[#DDE5F0]/78">{p.pass}/{p.total}</span>
                  </button>
                ))}
              </div>
              {s && (
                <div className="flex justify-between text-[12px] text-[#DDE5F0]/80">
                  <span>{s.pass} work · {s.fail} didn't · {s.skip} skipped</span>
                  <span>{v?.running ? 'Checking…' : `${s.todo} to go`}</span>
                </div>
              )}
            </div>

            {flash && (
              <div role="status" className="flex items-center gap-2 rounded-xl px-3 py-2 text-[13px]" style={{ background: 'rgba(92,255,176,0.12)', border: '1px solid rgba(92,255,176,0.4)', color: C.good }}>
                <CheckCircle2 size={14} /> {flash}
              </div>
            )}
            {note && <div className="text-[12px]" style={{ color: C.warn }}>{note}</div>}

            {/* the one Next card */}
            {v && (
              <div className="rounded-2xl p-4" style={{ background: 'rgba(221,229,240,0.06)', border: '1px solid rgba(221,229,240,0.10)' }}>
                {nx && buttons ? (
                  <>
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="text-[11.5px] font-semibold uppercase tracking-wider text-[#DDE5F0]/76">Next</span>
                      <WhereBadge where={nx.where} />
                    </div>
                    <div className="text-[16px] font-semibold">{nx.title}</div>
                    <div className="mt-1 text-[14px] leading-snug">{nx.next}</div>
                    {nx.detail && nx.detail !== nx.next && !nx.ask && <div className="mt-1 text-[12px] leading-snug text-[#DDE5F0]/78">{nx.detail}</div>}
                    {nx.id === 'ghost_preview' && <div className="mt-2"><PracticeBanner /></div>}
                    {(nx.armed || nx.status === 'running') && !nx.ask && (
                      <div className="mt-2 flex items-center gap-1.5 text-[12px]" style={{ color: C.warn }}>
                        <Eye size={13} /> {nx.status === 'running' ? 'Checking…' : 'Watching for it. This card moves on by itself.'}
                      </div>
                    )}
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {buttons.primary === 'ask' && (
                        <>
                          <Btn big tone="good" disabled={busy} onClick={() => void act(nx, 'pass')}>Yes</Btn>
                          <Btn big tone="bad" disabled={busy} onClick={() => void act(nx, 'fail')}>No</Btn>
                        </>
                      )}
                      {buttons.primary === 'do' && (
                        <Btn big tone="main" disabled={busy || nx.status === 'running'} onClick={() => void act(nx, 'run')}>
                          <span className="inline-flex items-center gap-1.5"><Wand2 size={15} /> Do it for me{buttons.doLabel && buttons.doLabel !== 'Do it for me' ? `: ${buttons.doLabel}` : ''}</span>
                        </Btn>
                      )}
                      {buttons.primary === 'wait' && (
                        <span className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px]" style={{ background: 'rgba(221,229,240,0.06)' }}>
                          <Smartphone size={13} /> Waiting for your phone
                        </span>
                      )}
                      {buttons.showDoneFail && (
                        <>
                          <Btn tone="good" disabled={busy} onClick={() => void act(nx, 'pass')}>Done</Btn>
                          <Btn tone="bad" disabled={busy} onClick={() => void act(nx, 'fail')}>It didn't work</Btn>
                        </>
                      )}
                      <Btn disabled={busy} onClick={() => void act(nx, 'skip')}>Skip</Btn>
                    </div>
                  </>
                ) : allDone ? (
                  <div className="space-y-3">
                    <div className="flex items-center gap-2 text-[18px] font-semibold" style={{ color: C.good }}>
                      <CheckCircle2 size={20} /> All done
                    </div>
                    <div className="text-[13px] text-[#DDE5F0]/85">Everything worked or was skipped. Orion is ready. Copy the report if you want to keep or share it.</div>
                    <button
                      ref={copyRef}
                      type="button"
                      onClick={() => void copyReport()}
                      className="inline-flex items-center gap-2 rounded-full px-5 py-2.5 text-[14px] font-semibold text-white outline-none focus-visible:ring-2 focus-visible:ring-[#5CFFB0]"
                      style={{ background: 'rgba(92,255,176,0.2)', border: '1px solid rgba(92,255,176,0.6)' }}
                    >
                      <ClipboardCopy size={15} /> Copy report
                    </button>
                  </div>
                ) : (
                  <div className="text-[14px]">
                    {s && s.fail ? `All checked. ${s.fail} didn't work: open the groups below to see why, then copy the report.` : 'All checked.'}
                  </div>
                )}
              </div>
            )}

            {/* groups, collapsed */}
            <div className="space-y-1">
              {(v?.groups ?? []).map((g) => {
                const pass = g.items.filter((i) => i.status === 'pass').length;
                const fail = g.items.filter((i) => i.status === 'fail').length;
                const isOpen = !!expanded[g.id];
                return (
                  <div key={g.id} className="rounded-xl" style={{ background: 'rgba(221,229,240,0.03)' }}>
                    <button
                      type="button"
                      onClick={() => setExpanded((x) => ({ ...x, [g.id]: !isOpen }))}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] font-semibold"
                    >
                      {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <span className="flex-1">{g.title}</span>
                      {fail > 0 && <span className="text-[11.5px]" style={{ color: C.bad }}>{fail} didn't work</span>}
                      <span className="text-[11.5px] font-medium text-[#DDE5F0]/76">{pass}/{g.items.length}</span>
                    </button>
                    {isOpen && (
                      <div className="space-y-1 px-3 pb-2">
                        {g.items.map((i) => (
                          <div key={i.id} className="flex items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-white/[0.04]">
                            <Dot s={i.status} />
                            <span className="min-w-0 flex-1">
                              <span className="block text-[12.5px]">
                                {i.title}
                                <span className="ml-2 text-[11.5px]" style={{ color: statusColor[i.status] }}>{statusWord[i.status]}</span>
                              </span>
                              <span className="block text-[11.5px] leading-snug text-[#DDE5F0]/76">{i.status === 'todo' || i.status === 'fail' ? i.next : i.detail}</span>
                            </span>
                            {(i.status === 'fail' || i.status === 'todo') && (i.do || (i.kind === 'manual' && !i.by_phone)) && (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void act(i, i.do ? 'run' : 'pass')}
                                className="shrink-0 rounded-full px-2 py-0.5 text-[11.5px] text-white"
                                style={{ background: 'rgba(46,107,255,0.22)', border: '1px solid #3A4352' }}
                              >
                                {i.do ? 'Do it' : 'Done'}
                              </button>
                            )}
                            {i.status !== 'todo' && i.status !== 'running' && (
                              <button
                                type="button"
                                aria-label={`Check ${i.title} again`}
                                disabled={busy}
                                onClick={() => void act(i, 'todo')}
                                className="shrink-0 text-[#DDE5F0]/72 hover:text-[#DDE5F0]"
                              >
                                <RotateCcw size={12} />
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <div className="flex gap-2">
                <Btn disabled={busy || v?.running} onClick={() => void runAll(true)}>
                  <span className="inline-flex items-center gap-1.5">{v?.running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run all automatic checks</span>
                </Btn>
                {!allDone && (
                  <Btn onClick={() => void copyReport()}>
                    <span className="inline-flex items-center gap-1.5"><ClipboardCopy size={13} /> Copy report</span>
                  </Btn>
                )}
              </div>
              <button type="button" disabled={busy} onClick={() => void reset()} className="text-[12px] text-[#DDE5F0]/74 hover:text-[#DDE5F0]">
                Start over
              </button>
            </div>
          </div>
        </GlassWindow>
      )}
    </AnimatePresence>
  );
}
