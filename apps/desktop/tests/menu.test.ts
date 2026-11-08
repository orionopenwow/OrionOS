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

// ---- look pass: the command console groups and the draggable Orion button --------------------------------
import { SECTIONS, sectionItems } from '../src/lib/menu.ts';
import { clampPos, defaultPos, inTaskbarCorner, readPos, BUTTON_SIZE } from '../src/lib/orionButton.ts';

test('every item except Chat sits in exactly one console section, main items first', () => {
  const ids = new Set(SECTIONS.map((s) => s.id));
  for (const i of MENU) {
    if (i.id === 'chat') assert.equal(i.section, undefined);
    else assert.ok(i.section && ids.has(i.section), i.id);
  }
  const placed = SECTIONS.flatMap((s) => sectionItems(s.id).map((i) => i.id));
  assert.equal(placed.length, MENU.length - 1);
  for (const s of SECTIONS) {
    const g = sectionItems(s.id).map((i) => i.group);
    assert.deepEqual(g, [...g].sort((a, b) => (a === b ? 0 : a === 'main' ? -1 : 1)), s.id);
  }
});

test('the Orion button stays on screen and remembers a sane spot', () => {
  assert.deepEqual(clampPos({ x: -50, y: 9999 }, 1600, 1000), { x: 12, y: 1000 - BUTTON_SIZE - 12 });
  assert.deepEqual(clampPos({ x: 700, y: 400 }, 1600, 1000), { x: 700, y: 400 });
  // a spot saved on a big screen is pulled back inside a small one
  assert.deepEqual(readPos(JSON.stringify({ x: 1500, y: 900 }), 390, 844), { x: 390 - BUTTON_SIZE - 12, y: 844 - BUTTON_SIZE - 12 });
  for (const bad of [null, '', 'nope', '{"x":"a"}', '{"x":1}']) assert.deepEqual(readPos(bad, 1600, 1000), defaultPos(1600, 1000));
  assert.equal(inTaskbarCorner(defaultPos(1600, 1000), 1000), true);
  assert.equal(inTaskbarCorner({ x: 800, y: 40 }, 1000), false);
});
