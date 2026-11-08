// Pure tests for Black hole 2.0 (after-1.0 plan §3): the depth layer's geometry and the quality tiers.
// Run: node --test apps/desktop/tests/look.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { curve, lineAlpha, pull, wellFor } from '../src/lib/spaceCurve.ts';
import { autoQuality, qualityName, resolveQuality, type GpuInfo } from '../src/lib/blackhole/quality.ts';

const well = wellFor(1920, 1080, { x: 960, y: 540, r: 160 });

test('screen edges stay flat, space curves toward the core', () => {
  assert.equal(pull(well.rim, well), 0);
  assert.equal(pull(well.rim * 1.2, well), 0);
  // a corner doesn't move at all, a point near the disk moves a lot
  assert.deepEqual(curve(0, 0, well), [0, 0]);
  const near = curve(960 + 240, 540, well);
  assert.ok(near[0] < 1200 - 30, `near point pulled in (${near[0]})`);
  assert.equal(near[1], 540);
  // the pull grows toward the centre
  assert.ok(pull(250, well) > pull(400, well) && pull(400, well) > pull(800, well));
});

test('the well never folds over itself (lines bend, they never cross)', () => {
  let prev = -1;
  for (let d = 0; d <= well.rim * 1.1; d += 2) {
    const landed = d - pull(d, well);
    assert.ok(landed > prev, `monotonic at ${d}`);
    prev = landed;
  }
});

test('grid lines vanish under the disk and stay faint everywhere', () => {
  assert.equal(lineAlpha(100, well), 0);
  for (const d of [200, 400, 800, well.rim]) {
    const a = lineAlpha(d, well);
    assert.ok(a > 0 && a <= 0.1, `alpha ${a} at ${d}`);
  }
});

const gpu = (over: Partial<GpuInfo>): GpuInfo => ({
  renderer: 'x', software: false, virtual: false, discrete: false, strongIntegrated: false, ...over,
});

test('auto quality: ray-marched Ultra only on a discrete card, Still on software GL', () => {
  assert.equal(autoQuality(gpu({ discrete: true })), 4);
  assert.equal(autoQuality(gpu({ strongIntegrated: true })), 3);
  assert.equal(autoQuality(gpu({})), 2);
  assert.equal(autoQuality(gpu({ virtual: true })), 2);
  assert.equal(autoQuality(gpu({ software: true })), 0);
  assert.equal(autoQuality(null), 0);
});

test('tier names round-trip and manual choices win', () => {
  assert.deepEqual([0, 1, 2, 3, 4].map((q) => qualityName(q as 0 | 1 | 2 | 3 | 4)), ['still', 'battery', 'balanced', 'high', 'ultra']);
  assert.equal(resolveQuality('ultra'), 4);
  assert.equal(resolveQuality('high'), 3);
  assert.equal(resolveQuality('still'), 0);
});
