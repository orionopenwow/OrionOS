// The command console's side panels (lib/commandCentreView.ts). Run: node --test apps/desktop/tests/commandCentre.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  calc, clock, meterLevel, projectStatus, sortProjects, todayTimeline, unreadByApp, usedFromFree, whenWords,
} from '../src/lib/commandCentreView.ts';

test('the calculator does sums safely and ignores plain words', () => {
  assert.equal(calc('12*7+3'), '87');
  assert.equal(calc('(2+3)^2'), '25');
  assert.equal(calc('10 / 4'), '2.5');
  assert.equal(calc('20% * 50'), '10');
  assert.equal(calc('-3 + 1'), '-2');
  assert.equal(calc('1,200 * 2'), '2,400');
  assert.equal(calc('= 9 - 10'), '-1');
  assert.equal(calc('42'), null); // a lone number is not a sum
  assert.equal(calc('hello there'), null);
  assert.equal(calc('alert(1)'), null);
  assert.equal(calc('2 +'), null);
  assert.equal(calc('1/0'), null);
});

test('the calculator converts units of the same kind', () => {
  assert.equal(calc('5 km in mi'), '3.10686 mi');
  assert.equal(calc('100 c to f'), '212 f');
  assert.equal(calc('32 f in c'), '0 c');
  assert.equal(calc('2 lb in kg'), '0.907184 kg');
  assert.equal(calc('90 minutes in h'), '1.5 h');
  assert.equal(calc('12 in to cm'), '30.48 cm');
  assert.equal(calc('5 km in kg'), null); // different kinds
  assert.equal(calc('hello in there'), null);
});

test('project status: needs you first, then running, then the newest', () => {
  assert.equal(projectStatus('needs_you'), 'needs');
  assert.equal(projectStatus('review'), 'needs');
  assert.equal(projectStatus('running'), 'running');
  assert.equal(projectStatus('queued'), 'running');
  assert.equal(projectStatus('done'), 'idle');
  assert.equal(projectStatus(null), 'idle');
  const list = [
    { name: 'a', status: 'idle' as const, created: 5 },
    { name: 'b', status: 'running' as const, created: 1 },
    { name: 'c', status: 'needs' as const, created: 2 },
    { name: 'd', status: 'idle' as const, created: 9 },
  ];
  assert.deepEqual(sortProjects(list).map((p) => p.name), ['c', 'b', 'd', 'a']);
  assert.equal(sortProjects([...list, ...list, ...list]).length, 5);
});

test('the Today timeline: in time order, at most the limit, far-off promises left out', () => {
  const now = new Date(2026, 10, 7, 14, 0).getTime() / 1000;
  const items = todayTimeline(
    [
      { id: 'late', task: 'Overdue thing', due: now - 3600 },
      { id: 'soon', task: 'Soon', due: now + 1800 },
      { id: 'far', task: 'Next week', due: now + 7 * 86400 },
      { id: 'any', task: 'Some time', due: null },
    ],
    { wind_down_starts: '21:30', bedtime: '23:00', break_due: true },
    now,
    10,
  );
  assert.deepEqual(items.map((i) => i.id), ['p-late', 'break', 'p-soon', 'wind', 'bed', 'p-any']);
  assert.equal(todayTimeline([], { wind_down_starts: '09:00' }, now).length, 0); // already past
  assert.equal(todayTimeline([{ id: 'a', task: 'a', due: null }, { id: 'b', task: 'b', due: null }], null, now, 1).length, 1);
  assert.equal(whenWords(now - 3600, now), 'Overdue');
  assert.equal(whenWords(now + 1800, now), '14:30');
  assert.equal(whenWords(now + 86400, now), 'Tomorrow 14:00');
  assert.equal(whenWords(null, now), 'Any time');
});

test('unread by app, muted threads are the caller\'s to drop, most unread first', () => {
  const out = unreadByApp([
    { platform: 'discord', unread: 2 }, { platform: 'telegram', unread: 1 }, { platform: 'discord', unread: 3 },
    { platform: 'sms', unread: 0 }, { platform: 'whatsapp', unread: 4 },
  ]);
  assert.deepEqual(out, [{ app: 'Discord', unread: 5 }, { app: 'WhatsApp', unread: 4 }, { app: 'Telegram', unread: 1 }]);
});

test('computer meters read the health lines', () => {
  assert.equal(usedFromFree('9.8 GB of 16.0 GB free (61%).'), 39);
  assert.equal(usedFromFree('212.4 GB free on / (46%); 30.0 GB free on /home (12%).'), 54);
  assert.equal(usedFromFree('Not checked.'), null);
  assert.equal(meterLevel(null), 'none');
  assert.equal(meterLevel(40), 'ok');
  assert.equal(meterLevel(80), 'warn');
  assert.equal(meterLevel(95), 'high');
  assert.equal(clock(1500), '25:00');
  assert.equal(clock(61.4), '01:01');
});

test('the console keeps every feature reachable and lays out four columns on wide screens', () => {
  const src = readFileSync(new URL('../src/components/console/CommandConsole.tsx', import.meta.url), 'utf8');
  assert.match(src, /w >= 1700 \? 'four'/);
  for (const s of ['work', 'life', 'connect', 'system']) assert.ok(src.includes(`'${s}'`), s);
  assert.match(src, /sections=\{\['work', 'life', 'connect', 'system'\]\}/);
});
