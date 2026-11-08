// {{name}}: a tiny canvas game with no dependencies. Open index.html in a browser to play.
// The rules live in plain functions at the top (tested by game.test.js with `node --test`);
// drawing and input are at the bottom and only run in a browser.

const W = 480;
const H = 640;
const SHIP_SPEED = 320; // pixels per second

// The ship sprite: each letter is one pixel (. = see-through). Drawn once onto a small canvas, then scaled up.
const SHIP = [
  '....c....',
  '...ccc...',
  '...cwc...',
  '..ccccc..',
  '.cc.c.cc.',
  'cc.ccc.cc',
  'c..rrr..c',
  '....r....',
];
const COLORS = { c: '#7ff2ff', w: '#ffffff', r: '#ff5c8a' };

function newState() {
  return { x: W / 2, score: 0, lives: 3, things: [], spawnIn: 0, time: 0, over: false };
}

// One step of the game. `input` is -1 (left), 0 or 1 (right); `rand` is Math.random (swappable in tests).
function step(s, dt, input, rand = Math.random) {
  if (s.over) return s;
  s.time += dt;
  s.x = Math.max(24, Math.min(W - 24, s.x + input * SHIP_SPEED * dt));
  s.spawnIn -= dt;
  if (s.spawnIn <= 0) {
    const rock = rand() < 0.3;
    s.things.push({ x: 20 + rand() * (W - 40), y: -20, kind: rock ? 'rock' : 'star', speed: 140 + rand() * 120 + s.time * 3 });
    s.spawnIn = Math.max(0.25, 0.8 - s.time * 0.01);
  }
  for (const t of s.things) t.y += t.speed * dt;
  const shipY = H - 60;
  s.things = s.things.filter((t) => {
    if (Math.abs(t.x - s.x) < 26 && Math.abs(t.y - shipY) < 22) {
      if (t.kind === 'star') s.score += 1;
      else s.lives -= 1;
      return false;
    }
    return t.y < H + 30;
  });
  if (s.lives <= 0) s.over = true;
  return s;
}

if (typeof module !== 'undefined') module.exports = { newState, step, W, H, SHIP };

// ---------- browser only: drawing and input ----------
if (typeof document !== 'undefined') {
  const canvas = document.getElementById('game');
  const ctx = canvas.getContext('2d');
  const sprite = document.createElement('canvas');
  sprite.width = SHIP[0].length;
  sprite.height = SHIP.length;
  const sctx = sprite.getContext('2d');
  SHIP.forEach((row, y) => [...row].forEach((ch, x) => {
    if (COLORS[ch]) { sctx.fillStyle = COLORS[ch]; sctx.fillRect(x, y, 1, 1); }
  }));

  let state = newState();
  const keys = new Set();
  let dragX = null;
  window.addEventListener('keydown', (e) => {
    keys.add(e.key.toLowerCase());
    if (e.key === ' ' && state.over) state = newState();
  });
  window.addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));
  canvas.addEventListener('pointerdown', (e) => { if (state.over) state = newState(); dragX = e.offsetX; });
  canvas.addEventListener('pointermove', (e) => { if (dragX !== null) dragX = e.offsetX; });
  window.addEventListener('pointerup', () => { dragX = null; });

  function inputNow() {
    if (keys.has('arrowleft') || keys.has('a')) return -1;
    if (keys.has('arrowright') || keys.has('d')) return 1;
    if (dragX !== null) {
      const target = dragX * (W / canvas.clientWidth);
      if (Math.abs(target - state.x) > 6) return target < state.x ? -1 : 1;
    }
    return 0;
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (const t of state.things) {
      ctx.fillStyle = t.kind === 'star' ? '#ffc66b' : '#8a90b8';
      ctx.beginPath();
      ctx.arc(t.x, t.y, t.kind === 'star' ? 8 : 14, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(sprite, state.x - 27, H - 84, 54, 48);
    ctx.fillStyle = '#d8deff';
    ctx.font = '600 18px system-ui, sans-serif';
    ctx.fillText(`Score ${state.score}`, 16, 30);
    ctx.fillText('♥'.repeat(Math.max(0, state.lives)), W - 70, 30);
    if (state.over) {
      ctx.textAlign = 'center';
      ctx.font = '700 32px system-ui, sans-serif';
      ctx.fillText('Game over', W / 2, H / 2 - 10);
      ctx.font = '16px system-ui, sans-serif';
      ctx.fillText(`You caught ${state.score} stars. Space or tap to play again.`, W / 2, H / 2 + 24);
      ctx.textAlign = 'start';
    }
  }

  // The game loop: update by the real time that passed, then draw.
  let last = performance.now();
  function loop(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    step(state, dt, inputNow());
    draw();
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);
}
