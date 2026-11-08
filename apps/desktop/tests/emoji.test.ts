import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { EMOJI, pickEmoji } from '../src/lib/emoji.ts';

test('tasks get a picture from their own words, then from their kind', () => {
  assert.equal(pickEmoji('now', 'Star catcher: add a high-score table'), 'star');
  assert.equal(pickEmoji('now', 'Fix the raid reset timer'), 'video-game');
  assert.equal(pickEmoji('next_up', 'Send Maya the demo track'), 'musical-note');
  assert.equal(pickEmoji('needs_you', 'Something odd'), 'warning');
  assert.equal(pickEmoji('now', 'Star catcher: add a high-score table', 'Checking the work (build, tests, review)'), 'star');
  assert.equal(pickEmoji('message', 'Leo on Discord', 'the raid reset timer'), 'speech-balloon');
  assert.equal(pickEmoji('approval', 'Devlog: raid lockouts'), 'shield');
});

test('every picture the picker can return is bundled', () => {
  for (const n of EMOJI) assert.ok(existsSync(new URL(`../public/emoji/${n}.svg`, import.meta.url)), n);
  assert.ok(existsSync(new URL('../public/emoji/LICENSE.txt', import.meta.url)));
});
