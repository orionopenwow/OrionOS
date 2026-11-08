// The console's Focus dashboard rules (lib/focusView.ts). Run: node --test apps/desktop/tests/focus.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { FOCUS_MAX, greeting, showDashboard, topCards, type FocusCard } from '../src/lib/focusView.ts';

const card = (id: string, score: number, pinned = false): FocusCard =>
  ({ id, kind: 'message', title: id, detail: '', why: 'because', score, pinned, open: {}, actions: ['open'], meta: {} });

test('never more than five cards, pinned first then by importance', () => {
  const cards = [card('a', 10), card('b', 90), card('c', 50), card('d', 40), card('e', 30), card('f', 20), card('g', 5, true)];
  const top = topCards(cards);
  assert.equal(top.length, FOCUS_MAX);
  assert.deepEqual(top.map((c) => c.id), ['g', 'b', 'c', 'd', 'e']);
});

test('the dashboard shows only before a conversation and while no chat message is typed', () => {
  const base = { messages: 0, processing: false, chatting: false, query: '', featureMatches: 0 };
  assert.equal(showDashboard(base), true);
  assert.equal(showDashboard({ ...base, messages: 1 }), false);
  assert.equal(showDashboard({ ...base, processing: true }), false);
  assert.equal(showDashboard({ ...base, chatting: true }), false);
  assert.equal(showDashboard({ ...base, query: 'phone', featureMatches: 2 }), true);        // a feature search keeps it
  assert.equal(showDashboard({ ...base, query: 'hello there', featureMatches: 0 }), false); // a chat message
  assert.equal(showDashboard({ ...base, query: 'can you check my phone', featureMatches: 1 }), false);
});

test('greetings never use a name', () => {
  assert.equal(greeting(new Date(2026, 0, 1, 8)), 'Morning');
  assert.equal(greeting(new Date(2026, 0, 1, 21)), 'Evening');
});

test('the dashboard can only open, draft, pin or hide: it never sends or approves', () => {
  const src = readFileSync(new URL('../src/components/console/FocusDashboard.tsx', import.meta.url), 'utf8');
  assert.ok(!/onReply|comms_reply|COMMS_REPLY|\/approvals\//.test(src));
});
