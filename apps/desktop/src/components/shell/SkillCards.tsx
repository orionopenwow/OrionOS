import { useCallback, useEffect, useState } from 'react';
import { onOrionOpen, setMenuBadge } from '../../lib/menu';
import { ORION_HTTP_URL } from '../../lib/assets';

/**
 * Skills (Skill Recorder): record in Chrome or on the desktop, confirm the card (name, voice trigger, values that
 * change), run with a preview (targets highlighted), step by step or all at once, export as a Playwright or plain
 * Python script, import one back. Risky steps show up in the approval queue as usual.
 * While recording, a small "Recording" pill stays on screen (with the reason when it pauses for privacy).
 * Talks to the daemon over HTTP (/browser-skills and /desktop-skills), so it needs nothing from the socket hook.
 */
const API = `${ORION_HTTP_URL}/browser-skills`;
const DESK = `${ORION_HTTP_URL}/desktop-skills`;

interface Variable { name: string; label: string; kind: string; example: string }
interface Draft { draft_id: string; name: string; card: string[]; variables: Variable[]; surface?: string; skipped_private?: number; import_skipped?: string[] }
interface SkillSummary { id: string; name: string; version: number; trusted: boolean; steps: number; risky_steps: number; voice_triggers: string[]; card: string[]; variables: Variable[]; surface?: string; start_app?: string }
interface Highlight { index: number; found: boolean | null; how?: string; rect?: number[] | null }
interface RunStep { description: string; risky: boolean; risk_reason?: string }
interface RunView { run_id: string; name: string; status: string; message: string; index: number; total: number; surface?: string; steps?: RunStep[]; highlights?: Highlight[] }
interface Suggestion { id: string; title: string; body: string; steps: string[] }
interface Recording { url?: string; app?: string; title?: string; paused?: boolean; reason?: string; events: number; surface?: string }
interface SkillsState { skills: SkillSummary[]; drafts: Draft[]; recording: Recording | null; runs: RunView[]; suggestions: Suggestion[] }
interface DeskSettings { notice_patterns: boolean; nightly_review: boolean; review_hour: number }
interface DeskStatus { backend: string; identity: boolean; input: string | null; can_record: boolean; ocr: boolean; settings: DeskSettings }

const post = (base: string, path: string, body?: unknown) =>
  fetch(`${base}${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) });

const box: React.CSSProperties = { background: 'rgba(255,255,255,0.04)', border: '1px solid #262D50', borderRadius: 12, padding: 10, marginTop: 8 };
const btn: React.CSSProperties = { background: '#1B2142', border: '1px solid #343C6E', color: '#D8DEFF', borderRadius: 8, padding: '4px 10px', cursor: 'pointer', font: 'inherit', marginRight: 6, marginTop: 6 };
const input: React.CSSProperties = { background: '#0B0F1E', border: '1px solid #262D50', color: '#D8DEFF', borderRadius: 6, padding: '4px 6px', font: 'inherit', width: '100%', boxSizing: 'border-box' };
const tag: React.CSSProperties = { fontSize: 10, border: '1px solid #343C6E', borderRadius: 6, padding: '0 5px', marginLeft: 6, opacity: 0.8, fontWeight: 400 };

function DraftCard({ draft, onDone }: { draft: Draft; onDone: () => void }) {
  const [name, setName] = useState(draft.name);
  const [trigger, setTrigger] = useState('');
  const [vars, setVars] = useState(() => draft.variables.map((v) => ({ name: v.name, new_name: v.name, keep_fixed: false, label: v.label, example: v.example })));
  const save = async () => {
    await post(API, `/drafts/${draft.draft_id}/save`, {
      name, voice_triggers: trigger ? [trigger] : [],
      variables: vars.map((v) => ({ name: v.name, new_name: v.new_name, keep_fixed: v.keep_fixed })),
    });
    onDone();
  };
  const discard = async () => { await post(API, `/drafts/${draft.draft_id}/discard`); onDone(); };
  return (
    <div style={box}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>New {draft.surface === 'desktop' ? 'desktop ' : ''}skill — check it and give it a name</div>
      <ol style={{ margin: '4px 0 6px 18px', padding: 0 }}>{draft.card.map((l, i) => <li key={i}>{l.replace(/^\d+\.\s*/, '')}</li>)}</ol>
      {!!draft.skipped_private && <div style={{ opacity: 0.7 }}>Left out {draft.skipped_private} private thing(s) (passwords, card numbers, codes, private windows).</div>}
      {!!draft.import_skipped?.length && <div style={{ opacity: 0.7 }}>Couldn't import {draft.import_skipped.length} line(s); record those parts instead.</div>}
      <label>Name<input style={input} value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label>Voice trigger (“Orion, …”)<input style={input} value={trigger} placeholder={name.toLowerCase()} onChange={(e) => setTrigger(e.target.value)} /></label>
      {vars.length > 0 && <div style={{ marginTop: 6 }}>Values that change each time:</div>}
      {vars.map((v, i) => (
        <div key={v.name} style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
          <input style={{ ...input, width: 120 }} value={v.new_name} disabled={v.keep_fixed}
            onChange={(e) => setVars(vars.map((x, j) => (j === i ? { ...x, new_name: e.target.value } : x)))} />
          <span style={{ opacity: 0.7, flex: 1 }}>{v.label}: “{v.example}”</span>
          <label style={{ whiteSpace: 'nowrap' }}><input type="checkbox" checked={v.keep_fixed}
            onChange={(e) => setVars(vars.map((x, j) => (j === i ? { ...x, keep_fixed: e.target.checked } : x)))} /> always this</label>
        </div>
      ))}
      <button style={btn} onClick={save}>Save skill</button>
      <button style={btn} onClick={discard}>Discard</button>
    </div>
  );
}

/** Before a run: fill in the values that change (prefilled with last time's), then preview. */
function RunForm({ skill, onDone }: { skill: SkillSummary; onDone: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(skill.variables.map((v) => [v.name, v.example])));
  const go = async () => { await post(API, `/${skill.id}/run`, { mode: 'step', preview: true, values }); onDone(); };
  return (
    <div style={{ marginTop: 6 }}>
      {skill.variables.map((v) => (
        <label key={v.name} style={{ display: 'block', marginTop: 4 }}>{v.label}
          <input style={input} value={values[v.name] ?? ''} onChange={(e) => setValues({ ...values, [v.name]: e.target.value })} />
        </label>
      ))}
      <button style={btn} onClick={go}>Preview</button>
      <button style={btn} onClick={onDone}>Cancel</button>
    </div>
  );
}

function RunCard({ r }: { r: RunView }) {
  const ctl = (action: string) => post(API, `/runs/${r.run_id}/control`, { action });
  const found = new Map((r.highlights ?? []).map((h) => [h.index, h]));
  return (
    <div style={box}>
      <div style={{ fontWeight: 600 }}>{r.name} · step {Math.min(r.index + 1, r.total)} of {r.total}{r.surface === 'desktop' && <span style={tag}>desktop</span>}</div>
      <div style={{ opacity: 0.8 }}>{r.message}</div>
      {!!r.steps?.length && (
        <ol style={{ margin: '4px 0 0 18px', padding: 0 }}>
          {r.steps.map((s, i) => {
            const h = found.get(i);
            const where = h && h.found === false ? ' (not on screen yet)' : '';
            return (
              <li key={i} style={{ opacity: i < r.index ? 0.5 : 1, fontWeight: i === r.index ? 600 : 400, color: s.risky ? '#F5A524' : undefined }}>
                {s.description}{s.risky ? ' — asks you first' : ''}{where}
              </li>
            );
          })}
        </ol>
      )}
      {r.status === 'preview' && <button style={btn} onClick={() => ctl('start')}>Step by step</button>}
      {(r.status === 'preview' || r.status === 'waiting_step') && <button style={btn} onClick={() => ctl('run_all')}>Run all</button>}
      {r.status === 'waiting_step' && <button style={btn} onClick={() => ctl('next')}>Next step</button>}
      <button style={btn} onClick={() => ctl('stop')}>Stop</button>
      {r.status === 'waiting_approval' && <div style={{ color: '#F5A524', marginTop: 4 }}>Waiting for your OK in the approval queue.</div>}
      {r.status === 'waiting_fix' && r.surface === 'desktop' && <div style={{ color: '#F5A524', marginTop: 4 }}>Click the right spot on screen; Orion remembers it.</div>}
    </div>
  );
}

/** Outlines where a desktop step's target is, when this window covers that part of the screen. */
function Highlights({ runs }: { runs: RunView[] }) {
  const boxes = runs.flatMap((r) => (r.surface === 'desktop' ? (r.highlights ?? []) : [])
    .filter((h) => h.found && h.rect && (r.status === 'preview' ? true : h.index === r.index))
    .map((h) => ({ key: `${r.run_id}-${h.index}`, n: h.index + 1, rect: h.rect as number[] })));
  if (!boxes.length) return null;
  return (
    <>
      {boxes.map((b) => {
        const left = b.rect[0] - window.screenX;
        const top = b.rect[1] - window.screenY;
        if (left < 0 || top < 0 || left > window.innerWidth || top > window.innerHeight) return null;
        return (
          <div key={b.key} style={{ position: 'fixed', left, top, width: b.rect[2], height: b.rect[3], border: '2px solid #7C8CFF', borderRadius: 6, pointerEvents: 'none', zIndex: 60 }}>
            <span style={{ position: 'absolute', top: -16, left: 0, fontSize: 10, background: '#7C8CFF', color: '#0B0F1E', borderRadius: 4, padding: '0 4px' }}>{b.n}</span>
          </div>
        );
      })}
    </>
  );
}

/** The small pill that stays on screen the whole time Orion is recording. */
function RecordingPill({ rec, onStop }: { rec: Recording; onStop: () => void }) {
  const where = rec.surface === 'desktop' ? (rec.app || 'the desktop') : 'Chrome';
  return (
    <div role="status" style={{ position: 'fixed', top: 10, left: '50%', transform: 'translateX(-50%)', zIndex: 70, background: 'rgba(11,15,30,0.92)', border: `1px solid ${rec.paused ? '#F5A524' : '#FF5C8A'}`, borderRadius: 999, padding: '4px 6px 4px 12px', display: 'flex', alignItems: 'center', gap: 8, font: '12px/1.4 system-ui, sans-serif', color: '#D8DEFF' }}>
      <span style={{ width: 8, height: 8, borderRadius: 999, background: rec.paused ? '#F5A524' : '#FF5C8A' }} />
      <span>{rec.paused ? (rec.reason || 'Paused') : `Recording in ${where} · ${rec.events} steps`}</span>
      <button style={{ ...btn, marginTop: 0, marginRight: 0, borderRadius: 999 }} onClick={onStop}>Stop</button>
    </div>
  );
}

export default function SkillCards() {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<SkillsState | null>(null);
  const [desk, setDesk] = useState<DeskStatus | null>(null);
  const [note, setNote] = useState('');
  const [runForm, setRunForm] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [code, setCode] = useState('');

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(API);
      if (r.ok) setState(await r.json());
      const d = await fetch(DESK);
      if (d.ok) setDesk(await d.json());
    } catch {
      /* daemon not up yet */
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2500);
    return () => clearInterval(t);
  }, [refresh]);

  // [simple-menu] opens from the menu ("skills") instead of its own floating button; Esc closes it
  useEffect(() => onOrionOpen('skills', () => setOpen(true)), []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);
  const attention = state ? state.drafts.length + state.runs.length + state.suggestions.length + (state.recording ? 1 : 0) : 0;
  useEffect(() => setMenuBadge('skills', attention), [attention]);

  if (!state) return open ? <div role="status" style={{ position: 'fixed', right: 16, bottom: 64, zIndex: 55, ...box, background: 'rgba(11,15,30,0.95)', color: '#D8DEFF' }}>Skills need Orion's brain running. <button style={btn} onClick={() => setOpen(false)}>Close</button></div> : null;
  const rec = state.recording;
  const stopRecording = () => (rec?.surface === 'desktop' ? post(DESK, '/record/stop') : post(API, '/record/stop')).then(refresh);
  const recordDesktop = async () => {
    const res = await (await post(DESK, '/record/start')).json().catch(() => ({}));
    if (res && res.message) setNote(res.message);
    refresh();
  };

  const exportSkill = async (id: string, lang: 'python' | 'js', desktop: boolean) => {
    const text = await (await fetch(`${API}/${id}/export?lang=${lang}`)).text();
    try {
      await navigator.clipboard.writeText(text);
      setNote(desktop ? 'Copied the Python script.' : `Copied the ${lang === 'python' ? 'Python' : 'JavaScript'} Playwright script.`);
    } catch {
      setNote('Could not copy — your system blocked the clipboard.');
    }
  };

  const importScript = async () => {
    const res = await (await post(API, '/import', { code, name: 'Imported skill' })).json().catch(() => null);
    setNote(res ? `Imported ${res.draft?.steps?.length ?? 0} steps — check the new card.` : 'That script could not be imported.');
    setImporting(false);
    setCode('');
    refresh();
  };

  const setSetting = async (key: keyof DeskSettings, value: boolean) => {
    await post(DESK, '/settings', { [key]: value });
    refresh();
  };

  return (
    <>
      {rec && <RecordingPill rec={rec} onStop={stopRecording} />}
      <Highlights runs={state.runs} />
      <div role={open ? 'dialog' : undefined} aria-label="Skills" style={{ position: 'fixed', right: 16, bottom: 64, zIndex: 55, font: '12px/1.4 system-ui, sans-serif', color: '#D8DEFF' }}>
        {open && (
          <div style={{ width: 380, maxHeight: '70vh', overflowY: 'auto', background: 'rgba(11,15,30,0.95)', border: '1px solid #262D50', borderRadius: 16, padding: 12, marginBottom: 8, backdropFilter: 'blur(8px)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap' }}>
              <strong>Skills</strong>
              <div>
                <button style={btn} onClick={() => setOpen(false)} aria-label="Close skills">Close</button>
                {rec
                  ? <button style={btn} onClick={stopRecording}>Stop recording</button>
                  : <>
                      <button style={btn} onClick={() => post(API, '/record/start').then(refresh)}>Record in Chrome</button>
                      <button style={btn} disabled={desk ? !desk.can_record : true} title={desk && !desk.can_record ? 'Keyboard and mouse watching is not available on this desktop' : ''} onClick={recordDesktop}>Record on desktop</button>
                    </>}
              </div>
            </div>
            {rec && (
              <div style={box}>{rec.paused ? `${rec.reason || 'Paused'}` : `Recording… ${rec.events} actions so far.`}</div>
            )}
            {note && <div style={{ ...box, opacity: 0.8 }}>{note}</div>}
            {state.drafts.map((d) => <DraftCard key={d.draft_id} draft={d} onDone={refresh} />)}
            {state.runs.map((r) => <RunCard key={r.run_id} r={r} />)}
            {state.suggestions.map((s) => (
              <div key={s.id} style={box}>
                <div style={{ fontWeight: 600 }}>{s.title}</div>
                <div style={{ opacity: 0.8 }}>{s.body}</div>
                <ol style={{ margin: '4px 0 0 18px', padding: 0 }}>{s.steps.map((l, i) => <li key={i}>{l}</li>)}</ol>
                <button style={btn} onClick={() => post(API, '/record/start').then(refresh)}>Record in Chrome</button>
                {desk?.can_record && <button style={btn} onClick={recordDesktop}>Record on desktop</button>}
                <button style={btn} onClick={() => post(API, `/suggestions/${s.id}/dismiss`).then(refresh)}>Not now</button>
              </div>
            ))}
            {state.skills.length === 0 && state.drafts.length === 0 && (
              <div style={{ ...box, opacity: 0.8 }}>Say “Orion, watch this” (Chrome) or “Orion, record a desktop skill”, do the task, then press Stop.</div>
            )}
            {state.skills.map((s) => {
              const desktop = s.surface === 'desktop';
              return (
                <div key={s.id} style={box}>
                  <div style={{ fontWeight: 600 }}>{s.name}<span style={tag}>{desktop ? (s.start_app || 'desktop') : 'browser'}</span> <span style={{ opacity: 0.6, fontWeight: 400 }}>v{s.version} · {s.steps} steps{s.risky_steps ? ` · ${s.risky_steps} need your OK` : ''}</span></div>
                  {!s.trusted && <div style={{ color: '#FF5C8A' }}>This skill was changed outside Orion, so it won't run. Record it again.</div>}
                  {s.voice_triggers.length > 0 && <div style={{ opacity: 0.7 }}>“Orion, {s.voice_triggers[0]}”</div>}
                  {runForm === s.id
                    ? <RunForm skill={s} onDone={() => { setRunForm(null); refresh(); }} />
                    : <>
                        <button style={btn} disabled={!s.trusted} onClick={() => (s.variables.length ? setRunForm(s.id) : post(API, `/${s.id}/run`, { mode: 'step', preview: true }).then(refresh))}>Run</button>
                        <button style={btn} onClick={() => exportSkill(s.id, 'python', desktop)}>Copy as Python</button>
                        {!desktop && <button style={btn} onClick={() => exportSkill(s.id, 'js', desktop)}>Copy as JS</button>}
                        <button style={btn} onClick={() => post(API, `/${s.id}/delete`).then(refresh)}>Delete</button>
                      </>}
                </div>
              );
            })}
            <div style={box}>
              {importing
                ? <>
                    <div>Paste a Playwright or plain Python script. Orion reads it without running it.</div>
                    <textarea style={{ ...input, height: 110, fontFamily: 'monospace', marginTop: 4 }} value={code} onChange={(e) => setCode(e.target.value)} />
                    <button style={btn} disabled={!code.trim()} onClick={importScript}>Import</button>
                    <button style={btn} onClick={() => setImporting(false)}>Cancel</button>
                  </>
                : <button style={{ ...btn, marginTop: 0 }} onClick={() => setImporting(true)}>Import a script</button>}
            </div>
            {desk && (
              <div style={box}>
                <div style={{ fontWeight: 600 }}>Learning on its own (off unless you turn it on)</div>
                <label style={{ display: 'block', marginTop: 4 }}><input type="checkbox" checked={desk.settings.notice_patterns} onChange={(e) => setSetting('notice_patterns', e.target.checked)} /> Notice tasks I repeat on the desktop (names of what I click only; no screen video, no typing)</label>
                <label style={{ display: 'block', marginTop: 4 }}><input type="checkbox" checked={desk.settings.nightly_review} onChange={(e) => setSetting('nightly_review', e.target.checked)} /> Nightly review: suggest new skills, rules and shortcuts in the Queue</label>
                <div style={{ opacity: 0.6, marginTop: 4 }}>Desktop: {desk.identity ? `reads what you click (${desk.backend})` : 'no accessibility, positions only'}{desk.ocr ? ' · screen text search on' : ''}</div>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
