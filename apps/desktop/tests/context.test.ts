// The console's "Focus and context" rules (lib/contextView.ts). Run: node --test apps/desktop/tests/context.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  agentName, clockAt, dayTimeline, inWords, nextBestAction, nowLineAt, projectsSummary, promiseObject, rowTime, shortTitle,
  waitingOnOthers, waitingOnYou, type CardLike,
} from '../src/lib/contextView.ts';

const NOW = new Date(2026, 10, 7, 15, 0, 0).getTime() / 1000; // 15:00 local
const at = (h: number, m = 0, day = 7) => new Date(2026, 10, day, h, m, 0).getTime() / 1000;

const nowCard: CardLike = {
  id: 'now:mission:star', kind: 'now', title: 'Star catcher: add a high-score table', detail: 'Claude: Checking the work (build, tests, review).',
  why: 'This is what Orion is working on right now.', open: { window: 'missions' },
  meta: { progress: 0.75, waiting_on: 'Your OK on the result, once the checks pass.' },
};
const maya: CardLike = {
  id: 'msg:tg:maya', kind: 'message', title: 'Maya on Telegram', detail: 'can you send the demo track?', why: 'You usually reply to Maya within minutes.',
  open: { window: 'messages', thread: 'tg:maya' }, meta: { sender: 'Maya', app_label: 'Telegram', unread: 1, thread: 'tg:maya' },
};
const approvals: CardLike = {
  id: 'approval:ap2', kind: 'approval', title: '2 things are waiting for your OK', detail: '', why: "Orion won't do these until you say yes.",
  open: { window: 'queue' }, meta: { count: 2 },
};
const nextUp: CardLike = {
  id: 'next:promise:demo', kind: 'next_up', title: 'Send Maya the demo track', detail: 'Due in 3 hours (you told Maya).', why: "You said you'd do this.",
  open: { window: 'life' }, meta: {},
};
const promises = [{ id: 'pr-demo', task: 'Send Maya the demo track', to: 'tg:maya', due: NOW + 3 * 3600 }];

test('the next best action reads like the mockup: plain sentences, one suggestion, two real actions', () => {
  const nba = nextBestAction([nowCard, maya, approvals, nextUp], { unread: 3, promises, now: NOW });
  assert.deepEqual(nba.sentences, [
    "Claude's work on Star catcher needs your OK once the checks pass.",
    'Maya is waiting for the demo track (Telegram), due in 3 hours.',
    '2 things are waiting for your OK.',
    'You have 3 unread messages.',
  ]);
  assert.equal(nba.suggestion, "Suggested: Check Claude's work, then reply to Maya.");
  assert.deepEqual(nba.actions.map((a) => [a.label, a.kind, a.window ?? a.thread]), [["Review Claude's work", 'window', 'missions'], ['Reply to Maya', 'draft', 'tg:maya']]);
  assert.ok(nba.why.length >= 2 && nba.why.every((w) => w.includes(':')));
  assert.equal(nba.clear, false);
});

test('a reply action only drafts; approvals only open the queue', () => {
  const nba = nextBestAction([approvals, maya], { unread: 0, promises: [], now: NOW });
  assert.equal(nba.actions[0].window, 'queue');
  assert.equal(nba.actions[1].kind, 'draft');
  assert.equal(nba.sentences[1], 'Maya is waiting for a reply (Telegram).');
  assert.ok(!nba.actions.some((a) => /approve|send/i.test(a.label)));
});

test('a promise already said with its message is not repeated as "Next"', () => {
  const nba = nextBestAction([maya, nextUp], { unread: 0, promises, now: NOW });
  assert.equal(nba.sentences.length, 1);
  const alone = nextBestAction([nextUp], { unread: 0, promises: [], now: NOW });
  assert.equal(alone.sentences[0], 'Next: send Maya the demo track, due in 3 hours (you told Maya).');
});

test('all clear, unread only, and a local-model summary replacing the sentences', () => {
  assert.deepEqual(nextBestAction([], { unread: 0, promises: [], now: NOW }).sentences, ['Nothing needs you right now.']);
  const unread = nextBestAction([], { unread: 1, promises: [], now: NOW });
  assert.deepEqual(unread.sentences, ['Nothing urgent needs you.', 'You have 1 unread message.']);
  assert.match(unread.suggestion, /messages/);
  const s = nextBestAction([maya], { unread: 0, promises: [], now: NOW, summary: 'Reply to Maya first.' });
  assert.deepEqual(s.sentences, ['Reply to Maya first.']);
  assert.equal(s.actions.length, 1);
});

test('never more than four sentences', () => {
  const many = [nowCard, maya, approvals, { ...maya, id: 'msg:x', meta: { sender: 'Leo', unread: 2 }, open: {} }];
  assert.equal(nextBestAction(many, { unread: 9, promises: [], now: NOW }).sentences.length, 4);
});

test('small word helpers', () => {
  assert.equal(shortTitle('Star catcher: add a table'), 'Star catcher');
  assert.equal(agentName('Claude: Checking'), 'Claude');
  assert.equal(agentName('Claude Code'), 'Claude');
  assert.equal(agentName(''), 'The agent');
  assert.equal(promiseObject('Send Maya the demo track', 'Maya'), 'the demo track');
  assert.equal(promiseObject('Look over the sketches', 'Maya'), null);
  assert.equal(inWords(NOW + 3 * 3600, NOW), 'in 3 hours');
  assert.equal(inWords(NOW - 20 * 60, NOW), '20 min ago');
  assert.equal(inWords(NOW + 30, NOW), 'now');
});

test("today's timeline merges promises, calendar, routines and missions in time order", () => {
  const items = dayTimeline(
    {
      promises: [
        { id: 'a', task: 'Send Maya the demo track', due: at(18) },
        { id: 'b', task: 'Old promise', due: at(9) },
        { id: 'c', task: 'Some day', due: null },
        { id: 'd', task: 'Next week', due: at(10, 0, 14) },
      ],
      life: { wind_down_starts: '21:30', bedtime: '23:00', break_due: true },
      missions: [{ id: 'm1', title: 'Star catcher: scores', state: 'running', agent_label: 'Claude Code', started: NOW - 600, time_limit_min: 30 }],
      agenda: [{ at: at(9, 30), title: 'Team standup' }, { at: at(16, 30), title: 'Call with Sam' }, { at: at(10, 0, 9), title: 'Not today' }],
    },
    NOW,
  );
  assert.deepEqual(items.map((i) => i.title), [
    'Old promise', 'Team standup', 'Take a short break', 'Star catcher should be done', 'Call with Sam', 'Send Maya the demo track', 'Wind down', 'Bedtime', 'Some day',
  ]);
  assert.equal(items[0].overdue, true);
  assert.equal(items[1].past, true);
  assert.equal(items.find((i) => i.kind === 'mission')!.sub, 'Claude Code, at the latest');
  assert.equal(nowLineAt(items, NOW), 2);
});

test('timeline times and a bedtime after midnight', () => {
  assert.deepEqual(rowTime(at(9, 5), NOW), { time: '09:05', day: '' });
  assert.deepEqual(rowTime(at(1, 30, 8), NOW), { time: '01:30', day: 'Tomorrow' });
  assert.deepEqual(rowTime(null, NOW), { time: 'Any time', day: '' });
  assert.equal(clockAt('00:30', NOW), at(0, 30, 8));
  assert.equal(clockAt('23:00', NOW), at(23));
  assert.equal(clockAt('nope', NOW), null);
  const late = dayTimeline({ life: { bedtime: '00:30' } }, NOW);
  assert.equal(late[0]?.title, 'Bedtime');
  assert.equal(nowLineAt(late, NOW), -1);
});

test('waiting on you: approvals, finished or stuck missions and needs-you cards, never twice', () => {
  const rows = waitingOnYou({
    approvals: [{ id: 'ap1', status: 'pending', kind: 'discord_prompt', author: 'newcomer42', channel: '#help' }, { id: 'ap2', status: 'pending', kind: 'devlog', title: 'Devlog: raid lockouts' }, { id: 'ap3', status: 'approved' }],
    missions: [
      { id: 'm1', title: 'Fix the raid reset timer', state: 'review', agent_label: 'Codex' },
      { id: 'm2', title: 'Website: contact form', state: 'needs_you', summary: 'Email or a file?' },
      { id: 'm3', title: 'Done one', state: 'done' },
    ],
    cards: [
      { id: 'needs:mission:m2:needs_you', kind: 'needs_you', title: 'Mission needs you', detail: '', why: '', open: { window: 'missions' }, meta: {} },
      { id: 'needs:health:warn', kind: 'needs_you', title: 'Something on this computer needs a look', detail: 'Disk is nearly full.', why: '', open: { window: 'analytics' }, meta: {} },
    ],
  });
  assert.deepEqual(rows.map((r) => r.title), ['Reply to newcomer42 in #help', 'Devlog: raid lockouts', 'Fix the raid reset timer', 'Website: contact form', 'Something on this computer needs a look']);
  assert.deepEqual(rows.map((r) => r.open.window), ['queue', 'queue', 'missions', 'missions', 'analytics']);
  assert.equal(rows[2].sub, 'Codex finished. Ready for your review');
});

test('waiting on others: running work and chats where you wrote last', () => {
  const rows = waitingOnOthers({
    now: NOW,
    missions: [{ id: 'q', title: 'Tidy notes', state: 'queued', agent_label: 'Gemini CLI' }, { id: 'r', title: 'Star catcher', state: 'running', agent_label: 'Claude Code' }],
    threads: [
      { key: 'tg:jules', thread_name: 'Jules', messages: [{ direction: 'in', text: 'hi', timestamp: NOW - 9000 }, { direction: 'out', text: 'send the files by friday?', timestamp: NOW - 7200 }] },
      { key: 'tg:maya', thread_name: 'Maya', messages: [{ direction: 'in', text: 'demo?', timestamp: NOW - 200 }] },
      { key: 'old', thread_name: 'Old', messages: [{ direction: 'out', text: 'x', timestamp: NOW - 9 * 86400 }] },
      { key: 'muted', thread_name: 'Muted', muted: true, messages: [{ direction: 'out', text: 'x', timestamp: NOW - 60 }] },
    ],
  });
  assert.deepEqual(rows.map((r) => r.title), ['Star catcher', 'Tidy notes', 'Jules']);
  assert.equal(rows[0].sub, 'Claude is on it');
  assert.equal(rows[2].sub, 'You wrote 2 hours ago: send the files by friday?');
  assert.equal(rows[2].open.thread, 'tg:jules');
});

test('the Mission Control summary line', () => {
  assert.equal(projectsSummary(['needs', 'running', 'idle', 'idle']), '4 projects, 1 running, 1 needs you');
  assert.equal(projectsSummary(['idle']), '1 project');
  assert.equal(projectsSummary([]), 'No projects yet');
});

test('the console keeps one timer, the Focus and context middle, and never sends from it', () => {
  const read = (p: string) => readFileSync(new URL(`../src/components/console/${p}`, import.meta.url), 'utf8');
  const widgets = read('CommandWidgets.tsx');
  const ctx = read('ContextPanel.tsx');
  const consoleSrc = read('CommandConsole.tsx');
  assert.equal((consoleSrc + widgets + ctx).match(/<FocusTimer \/>/g)?.length, 1, 'exactly one focus timer');
  assert.ok(ctx.includes('Focus and context') && ctx.includes("Today's timeline") && ctx.includes('Why Orion suggests this'));
  assert.ok(!/sendReply|\/comms\/send|onSendMessage/.test(ctx), 'the middle only drafts');
  assert.ok(consoleSrc.includes('Life and project manager'));
  for (const t of ['Mission Control', 'Waiting on you', 'Waiting on others', 'Computer', 'Messages', 'Quick tools']) assert.ok(widgets.includes(`"${t}"`), t);
});
