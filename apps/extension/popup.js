const $ = (id) => document.getElementById(id);
const ask = (action, extra = {}) => chrome.runtime.sendMessage({ type: 'ORION_POPUP', action, ...extra });

let current = {};

async function refresh() {
  const s = (current = (await ask('status')) || {});
  const rec = $('record');
  rec.classList.toggle('rec', !!s.recording);
  rec.textContent = s.recording ? `Stop recording (${s.steps} events so far)` : 'Record a skill on this page';
  rec.disabled = !s.recording && !s.web;
  if (!s.recording && !s.web) $('status').textContent = 'Open a web page to record a skill there.';
  $('pin').hidden = !s.chat;
  $('pin').textContent = s.pinned ? 'Unpin this chat (stop mirroring)' : 'Pin this chat so Orion mirrors it';
  $('notice').checked = !!s.notice;
}

$('record').addEventListener('click', async () => {
  if (current.recording) {
    await ask('record_stop');
    $('status').textContent = 'Done — finish the skill card in Orion (name it and check the values).';
  } else {
    // Optional: lets Orion note downloads (file name only) as steps. Asked once, needs this click.
    try { await chrome.permissions.request({ permissions: ['downloads'] }); } catch (err) { /* declined */ }
    const res = await ask('record_start');
    if (res && res.ok === false) $('status').textContent = res.error || 'Could not start recording here.';
    else window.close();
  }
  // ------------------------------------------------------------------ pairing with Orion
async function refreshPairing() {
  const st = (await chrome.runtime.sendMessage({ type: 'ORION_PAIR_STATUS' })) || {};
  $('pairbox').hidden = !!st.paired;
  $('linked').hidden = !st.paired;
}

$('pairbtn').addEventListener('click', async () => {
  $('pairmsg').textContent = 'Connecting…';
  const res = await chrome.runtime.sendMessage({ type: 'ORION_PAIR', code: $('code').value });
  $('pairmsg').textContent = res && res.ok ? 'Connected.' : (res && res.error) || 'That did not work.';
  refreshPairing();
});
$('code').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('pairbtn').click(); });
$('unpair').addEventListener('click', async (e) => {
  e.preventDefault();
  await chrome.runtime.sendMessage({ type: 'ORION_UNPAIR' });
  refreshPairing();
});

refresh();
refreshPairing();
});

$('pin').addEventListener('click', async () => { await ask('pin'); refresh(); });
$('notice').addEventListener('change', (e) => ask('notice', { on: e.target.checked }));

// ------------------------------------------------------------------ pairing with Orion
async function refreshPairing() {
  const st = (await chrome.runtime.sendMessage({ type: 'ORION_PAIR_STATUS' })) || {};
  $('pairbox').hidden = !!st.paired;
  $('linked').hidden = !st.paired;
}

$('pairbtn').addEventListener('click', async () => {
  $('pairmsg').textContent = 'Connecting…';
  const res = await chrome.runtime.sendMessage({ type: 'ORION_PAIR', code: $('code').value });
  $('pairmsg').textContent = res && res.ok ? 'Connected.' : (res && res.error) || 'That did not work.';
  refreshPairing();
});
$('code').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('pairbtn').click(); });
$('unpair').addEventListener('click', async (e) => {
  e.preventDefault();
  await chrome.runtime.sendMessage({ type: 'ORION_UNPAIR' });
  refreshPairing();
});

refresh();
refreshPairing();
