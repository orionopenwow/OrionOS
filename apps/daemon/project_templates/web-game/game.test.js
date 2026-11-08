// Run: node --test   (Node's own test runner; nothing to install)
const test = require('node:test');
const assert = require('node:assert/strict');
const { newState, step, W, H } = require('./game.js');

test('the ship moves and stays on screen', () => {
  const s = newState();
  for (let i = 0; i < 200; i++) step(s, 0.05, -1, () => 0.9);
  assert.ok(s.x >= 0 && s.x < W / 2);
});

test('catching a star adds to the score', () => {
  const s = newState();
  s.spawnIn = 99;
  s.things.push({ x: s.x, y: H - 60, kind: 'star', speed: 0 });
  step(s, 0.01, 0);
  assert.equal(s.score, 1);
});

test('three rocks end the game', () => {
  const s = newState();
  s.spawnIn = 99;
  for (let i = 0; i < 3; i++) {
    s.things.push({ x: s.x, y: H - 60, kind: 'rock', speed: 0 });
    step(s, 0.01, 0);
  }
  assert.equal(s.over, true);
});
