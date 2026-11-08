// The Test Center's flow logic (lib/testFlow.ts). Run: node --test apps/desktop/tests/testflow.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupPills, newlyPassed, nextButtons, pollMs, statusMap, type FlowGroup } from '../src/lib/testFlow.ts';

const groups = (s1: string, s2: string, armed = false): FlowGroup[] => [
  { id: 'voice', title: 'Voice', items: [
    { id: 'mic', title: 'Mic', kind: 'auto', status: s1 as never },
    { id: 'tts', title: 'Speaks', kind: 'manual', status: s2 as never, do: 'Say something', armed },
  ] },
  { id: 'safety', title: 'Safety', items: [{ id: 'freeze', title: 'Freeze', kind: 'manual', status: 'skip', by_phone: true }] },
];

test('the first load never chimes; a later pass does', () => {
  assert.deepEqual(newlyPassed({}, groups('pass', 'pass')), []);
  const before = statusMap(groups('pass', 'todo'));
  assert.deepEqual(newlyPassed(before, groups('pass', 'pass')).map((i) => i.id), ['tts']);
  assert.deepEqual(newlyPassed(before, groups('pass', 'todo')), []);
});

test('group pills count passes, failures and skips', () => {
  const [voice, safety] = groupPills(groups('fail', 'todo', true));
  assert.equal(voice.total, 2);
  assert.equal(voice.fail, 1);
  assert.equal(voice.done, false);
  assert.equal(voice.busy, true);
  assert.equal(safety.done, true);
  assert.equal(groupPills(groups('pass', 'skip'))[0].done, true);
});

test('Next card buttons: a question wins, then Do it for me, then waiting for the phone', () => {
  assert.equal(nextButtons({ id: 'a', title: '', kind: 'manual', status: 'todo', ask: 'Did you hear it?', do: 'Say something' }).primary, 'ask');
  const d = nextButtons({ id: 'a', title: '', kind: 'manual', status: 'todo', do: 'Say something' });
  assert.equal(d.primary, 'do');
  assert.equal(d.doLabel, 'Say something');
  assert.equal(d.showDoneFail, true);
  assert.equal(nextButtons({ id: 'a', title: '', kind: 'auto', status: 'todo', do: 'Run the check' }).showDoneFail, false);
  assert.equal(nextButtons({ id: 'a', title: '', kind: 'manual', status: 'todo', by_phone: true }).primary, 'wait');
});

test('polls faster while something is watched', () => {
  assert.equal(pollMs(true, null), 1200);
  assert.equal(pollMs(false, { id: 'a', title: '', kind: 'manual', status: 'todo', armed: true }), 1500);
  assert.equal(pollMs(false, null), 4000);
});
