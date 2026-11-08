import { test } from 'node:test';
import assert from 'node:assert/strict';
import { clampRect, dragRect, MIN_H, MIN_W } from '../src/lib/windowRect.ts';

const start = { x: 100, y: 100, w: 600, h: 500 };

test('corners and edges resize from the side you drag', () => {
  assert.deepEqual(dragRect(start, 'se', 50, 40), { x: 100, y: 100, w: 650, h: 540 });
  assert.deepEqual(dragRect(start, 'nw', 50, 40), { x: 150, y: 140, w: 550, h: 460 });
  assert.deepEqual(dragRect(start, 'e', 30, 99), { x: 100, y: 100, w: 630, h: 500 });
  assert.deepEqual(dragRect(start, 'move', 10, -20), { x: 110, y: 80, w: 600, h: 500 });
});

test('never smaller than the minimum, and the far edge stays put', () => {
  const r = dragRect(start, 'w', 1000, 0);
  assert.equal(r.w, MIN_W);
  assert.equal(r.x + r.w, start.x + start.w);
  assert.equal(dragRect(start, 'n', 0, 1000).h, MIN_H);
});

test('kept on screen and no bigger than it', () => {
  assert.deepEqual(clampRect({ x: 1500, y: -50, w: 600, h: 500 }, 1600, 1000), { x: 992, y: 8, w: 600, h: 500 });
  assert.deepEqual(clampRect({ x: 0, y: 0, w: 4000, h: 3000 }, 1600, 1000), { x: 8, y: 8, w: 1584, h: 984 });
});
