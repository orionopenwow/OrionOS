// The HUD's one menu (lib/menu.ts). Run: node --test apps/desktop/tests/menu.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { filterMenu, isMenuShortcut, mainItems, MAX_MAIN, MENU, moreItems, onOrionOpen, openOrion } from '../src/lib/menu.ts';

test('six main items at most, in the agreed order', () => {
  const main = mainItems().map((i) => i.id);
  assert.ok(main.length <= MAX_MAIN);
  assert.deepEqual(main, ['chat', 'projects', 'phone', 'skills', 'tests', 'settings']);
});

test('ids are unique, every item has a label, an icon and a one-line description', () => {
  const ids = MENU.map((i) => i.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const i of MENU) {
    assert.ok(i.label && i.icon, i.id);
    assert.ok(i.description.length > 5 && i.description.length < 90 && !i.description.includes('\n'), i.id);
  }
});

test('reserved ids and the things moved behind More are all there', () => {
  const ids = new Set(MENU.map((i) => i.id));
  for (const id of ['tests', 'phone', 'projects', 'privacy', 'missions']) assert.ok(ids.has(id), id);
  const more = new Set(moreItems().map((i) => i.id));
  for (const id of ['scout', 'agents', 'privacy', 'guide', 'selfchanges', 'tools', 'missions', 'camera', 'widgets']) assert.ok(more.has(id), id);
});

test('typing filters by name, description and extra words', () => {
  assert.deepEqual(filterMenu('').length, MENU.length);
  assert.ok(filterMenu('video').some((i) => i.id === 'scout'));
  assert.ok(filterMenu('GAME').some((i) => i.id === 'projects'));
  assert.ok(filterMenu('swarm').some((i) => i.id === 'agents'));
  assert.deepEqual(filterMenu('zzzz-nothing'), []);
});

test('Super+Space and Alt+Space open the menu; Ctrl+Space does not', () => {
  const k = (o: Partial<{ altKey: boolean; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean }>) =>
    ({ code: 'Space', altKey: false, metaKey: false, ctrlKey: false, shiftKey: false, ...o });
  assert.equal(isMenuShortcut(k({ metaKey: true })), true);
  assert.equal(isMenuShortcut(k({ altKey: true })), true);
  assert.equal(isMenuShortcut(k({ ctrlKey: true })), false);
  assert.equal(isMenuShortcut(k({})), false);
  assert.equal(isMenuShortcut({ ...k({ altKey: true }), code: 'KeyA' }), false);
});

test('orion:open reaches only the listeners for that id', () => {
  (globalThis as unknown as { window: EventTarget }).window = new EventTarget();
  const got: string[] = [];
  const off = onOrionOpen(['projects', 'missions'], (id) => got.push(id));
  openOrion('projects');
  openOrion('chat');
  openOrion('missions');
  off();
  openOrion('projects');
  assert.deepEqual(got, ['projects', 'missions']);
});
