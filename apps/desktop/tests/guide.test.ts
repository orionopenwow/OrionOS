// Guide Mode ring geometry. Run: node --test apps/desktop/tests/guide.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ringBox, stepWords } from '../src/lib/guideGeometry.ts';

test('ring is centred on the target and scaled to CSS pixels', () => {
  const r = ringBox({ x: 200, y: 100, w: 80, h: 20 }, 2);
  assert.equal(r.left + r.size / 2, 120);
  assert.equal(r.top + r.size / 2, 55);
  assert.ok(r.size >= 44);
});

test('tiny targets still get a ring you can see; bad scale counts as 1', () => {
  assert.equal(ringBox({ x: 0, y: 0, w: 2, h: 2 }, 0).size, 44);
});

test('step words', () => {
  assert.equal(stepWords(1, 4), 'Step 2 of 4');
  assert.equal(stepWords(undefined, 4), '');
});
