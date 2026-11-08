/**
 * Orion Skill Recorder — service-worker side (loaded by background.js with importScripts).
 *
 * Talks to the daemon through background.js's sendToDaemon() and drives recorder.js in the page:
 *  - recording: start/stop from the popup, the page pill or the daemon ("Orion, watch this"); forwards each
 *    DOM event plus navigations and downloads (file name only) to the daemon
 *  - replay: SKILL_PREVIEW / SKILL_EXEC_STEP / SKILL_PICK from the daemon; navigation and page-load waits are
 *    handled here, everything else by recorder.js in the tab (the user's real, signed-in session)
 *  - optional "notice repeated tasks": sends action outlines (never typed values) so Orion can suggest skills
 */
/* global sendToDaemon, isChatUrl, ensureObserver */

const SKILL_REC_KEY = 'orion_skill_rec';
const NOTICE_KEY = 'orion_notice_repeats';
let skillRec = null; // { rec_id, tabId, count, url }
const runTabs = {};
let lastDownloadAt = 0;

const skillSleep = (ms) => new Promise((r) => setTimeout(r, ms));
const isWebUrl = (url) => /^https?:\/\//.test(url || '') || /^file:\/\//.test(url || '');

async function loadSkillRec() {
  if (skillRec) return skillRec;
  try {
    const got = await chrome.storage.session.get(SKILL_REC_KEY);
    skillRec = got[SKILL_REC_KEY] || null;
  } catch (err) {
    skillRec = null;
  }
  return skillRec;
}

async function saveSkillRec() {
  try {
    await chrome.storage.session.set({ [SKILL_REC_KEY]: skillRec });
  } catch (err) { /* session storage unavailable */ }
}

async function activeWebTab() {
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  let tab = tabs.find((t) => isWebUrl(t.url));
  if (!tab) {
    const all = await chrome.tabs.query({ active: true });
    tab = all.find((t) => isWebUrl(t.url));
  }
  return tab || null;
}

async function ensureRecorder(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ['recorder.js'] });
    return true;
  } catch (err) {
    return false;
  }
}

async function tabMessage(tabId, message) {
  try {
    const res = await chrome.tabs.sendMessage(tabId, message, { frameId: 0 });
    if (res !== undefined) return res;
  } catch (err) { /* not injected yet, or the page is navigating */ }
  await waitTabComplete(tabId, 15000);
  if (!(await ensureRecorder(tabId))) return { ok: false, error: "Orion can't work on this page" };
  try {
    return await chrome.tabs.sendMessage(tabId, message, { frameId: 0 });
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

async function waitTabComplete(tabId, timeoutMs = 30000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.status === 'complete') return true;
    } catch (err) {
      return false;
    }
    await skillSleep(150);
  }
  return false;
}

// ------------------------------------------------------------------ recording
async function startSkillRecording(tab) {
  await loadSkillRec();
  if (skillRec) return { ok: true, already: true };
  tab = tab || (await activeWebTab());
  if (!tab || !isWebUrl(tab.url)) return { ok: false, error: 'Open the web page you want to record first.' };
  skillRec = { rec_id: `rec-${Math.random().toString(36).slice(2, 10)}`, tabId: tab.id, count: 0, url: tab.url };
  await saveSkillRec();
  sendToDaemon({ event: 'SKILL_REC_STATE', state: 'started', rec_id: skillRec.rec_id, url: tab.url, title: tab.title || '' });
  await ensureRecorder(tab.id);
  const res = await tabMessage(tab.id, { type: 'ORION_SKILL_RECORD', on: true, rec_id: skillRec.rec_id, count: 0 });
  return { ok: true, paused: res && res.paused };
}

async function stopSkillRecording() {
  await loadSkillRec();
  if (!skillRec) return { ok: true, already: true };
  const { rec_id, tabId } = skillRec;
  skillRec = null;
  await saveSkillRec();
  const notice = await noticeOn();
  await tabMessage(tabId, { type: 'ORION_SKILL_RECORD', on: false, notice }).catch(() => {});
  sendToDaemon({ event: 'SKILL_REC_STATE', state: 'stopped', rec_id });
  return { ok: true };
}

// URL parameters that are never forwarded (plus the names of private fields seen on recorded pages).
const PRIVATE_PARAM = /(^pw$|^pin$|pass|pwd|card|cc.?num|cvv|cvc|csc|otp|2fa|mfa|token|secret|one.?time|auth|session|code)/i;

/** Remove private query/fragment parameters from every URL inside an event before it leaves the browser. */
function scrubEvent(event) {
  const names = new Set((skillRec && skillRec.privateNames) || []);
  const text = JSON.stringify(event).replace(/([?&#])([^=&#"\s]+)=([^&#"\s]*)/g, (m, sep, key, _val) => {
    let k = key;
    try { k = decodeURIComponent(key); } catch (err) { /* keep raw */ }
    return names.has(k) || PRIVATE_PARAM.test(k) ? `${sep}${key}=` : m;
  });
  return JSON.parse(text);
}

function recEvent(event) {
  if (!skillRec) return;
  skillRec.count += 1;
  saveSkillRec();
  sendToDaemon({ event: 'SKILL_REC_EVENT', rec_id: skillRec.rec_id, ev: scrubEvent({ t: Date.now(), ...event }) });
}

async function noticeOn() {
  try {
    const got = await chrome.storage.local.get(NOTICE_KEY);
    return got[NOTICE_KEY] === true;
  } catch (err) {
    return false;
  }
}

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  await loadSkillRec();
  if (skillRec && tabId === skillRec.tabId) {
    if (changeInfo.url) recEvent({ action: 'navigate', url: changeInfo.url, title: tab.title || '' });
    if (changeInfo.status === 'complete') {
      await ensureRecorder(tabId);
      await tabMessage(tabId, { type: 'ORION_SKILL_RECORD', on: true, rec_id: skillRec.rec_id, count: skillRec.count });
    }
  } else if (changeInfo.status === 'complete' && isWebUrl(tab.url) && (await noticeOn())) {
    if (await ensureRecorder(tabId)) chrome.tabs.sendMessage(tabId, { type: 'ORION_SKILL_NOTICE' }, { frameId: 0 }).catch(() => {});
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  await loadSkillRec();
  if (skillRec && tabId === skillRec.tabId) stopSkillRecording();
});

function watchDownloads() {
  if (!chrome.downloads || watchDownloads.done) return;
  watchDownloads.done = true;
  chrome.downloads.onCreated.addListener(() => { lastDownloadAt = Date.now(); });
  chrome.downloads.onChanged.addListener(async (delta) => {
    if (!delta.filename || !delta.filename.current) return;
    await loadSkillRec();
    const name = delta.filename.current.split(/[\\/]/).pop(); // file name only, never the folder
    if (skillRec) recEvent({ action: 'download', filename: name });
  });
}
watchDownloads();
if (chrome.permissions && chrome.permissions.onAdded) chrome.permissions.onAdded.addListener(watchDownloads);

// ------------------------------------------------------------------ messages from pages and the popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.type !== 'string') return;
  if (msg.type === 'ORION_POPUP') {
    handlePopup(msg).then(sendResponse, (err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  if (!msg.type.startsWith('ORION_SKILL_') || !sender.tab) return;
  (async () => {
    await loadSkillRec();
    switch (msg.type) {
      case 'ORION_SKILL_EVENT':
        if (skillRec && sender.tab.id === skillRec.tabId && msg.rec_id === skillRec.rec_id) {
          skillRec.count += 1;
          saveSkillRec();
          sendToDaemon({ event: 'SKILL_REC_EVENT', rec_id: skillRec.rec_id, ev: scrubEvent(msg.event) });
        }
        break;
      case 'ORION_SKILL_PAUSED':
        if (skillRec && sender.tab.id === skillRec.tabId) {
          sendToDaemon({ event: 'SKILL_REC_STATE', state: msg.paused ? 'paused' : 'resumed', rec_id: skillRec.rec_id, reason: msg.reason || '' });
        }
        break;
      case 'ORION_SKILL_PRIVATE_NAMES':
        if (skillRec && sender.tab.id === skillRec.tabId) {
          skillRec.privateNames = [...new Set([...(skillRec.privateNames || []), ...(msg.names || []).map(String)])].slice(0, 200);
          saveSkillRec();
        }
        break;
      case 'ORION_SKILL_STOP':
        await stopSkillRecording();
        break;
      case 'ORION_SKILL_CONTROL':
        sendToDaemon({ event: 'SKILL_RUN_CONTROL', run_id: msg.run_id, action: msg.action });
        break;
      case 'ORION_SKILL_ACTIVITY':
        if (await noticeOn()) sendToDaemon({ event: 'SKILL_ACTIVITY', url: sender.tab.url, steps: [msg.event] }, { queue: false });
        break;
      default:
        break;
    }
  })();
});

async function handlePopup(msg) {
  await loadSkillRec();
  const tab = await activeWebTab();
  switch (msg.action) {
    case 'status':
      return { recording: !!skillRec, steps: skillRec ? skillRec.count : 0, notice: await noticeOn(),
        chat: !!(tab && typeof isChatUrl === 'function' && isChatUrl(tab.url)), pinned: !!(tab && tab.pinned), web: !!tab };
    case 'record_start':
      return startSkillRecording(tab);
    case 'record_stop':
      return stopSkillRecording();
    case 'notice':
      await chrome.storage.local.set({ [NOTICE_KEY]: !!msg.on });
      return { ok: true, notice: !!msg.on };
    case 'pin':
      if (tab && isChatUrl(tab.url)) {
        await chrome.tabs.update(tab.id, { pinned: !tab.pinned });
        await ensureObserver(tab.id);
        return { ok: true, pinned: !tab.pinned };
      }
      return { ok: false };
    default:
      return { ok: false, error: 'unknown action' };
  }
}

// ------------------------------------------------------------------ replay (messages from the daemon)
async function runTab(msg) {
  let tabId = msg.tab_id || runTabs[msg.run_id];
  if (tabId) {
    try { await chrome.tabs.get(tabId); } catch (err) { tabId = null; }
  }
  if (!tabId) {
    const tab = await activeWebTab();
    tabId = tab ? tab.id : null;
  }
  if (tabId && msg.run_id) runTabs[msg.run_id] = tabId;
  return tabId;
}

async function execSkillStep(msg) {
  const step = msg.step || {};
  const tabId = await runTab(msg);
  if (!tabId) return { ok: false, error: 'no web tab to run in' };
  const sentAt = Date.now();
  switch (step.action) {
    case 'open':
      await chrome.tabs.update(tabId, { url: step.url });
      await skillSleep(200);
      return (await waitTabComplete(tabId, 30000)) ? { ok: true } : { ok: false, error: 'the page took too long to load' };
    case 'wait_load':
      await skillSleep(400);
      return (await waitTabComplete(tabId, 30000)) ? { ok: true } : { ok: false, error: 'the page took too long to load' };
    case 'download': {
      const end = sentAt + 30000;
      while (Date.now() < end) {
        if (lastDownloadAt && lastDownloadAt >= sentAt - 20000) return { ok: true };
        await skillSleep(300);
      }
      return { ok: false, error: 'no download started' };
    }
    default:
      await waitTabComplete(tabId, 15000);
      await tabMessage(tabId, { type: 'ORION_SKILL_RUN_STATE', run: { run_id: msg.run_id, name: '', status: 'running',
        message: `step ${(msg.index || 0) + 1} of ${msg.total || '?'}: ${step.description || ''}` } });
      return tabMessage(tabId, { type: 'ORION_SKILL_EXEC', step, approved: !!msg.approved });
  }
}

async function handleSkillMessage(msg) {
  switch (msg.event) {
    case 'SKILL_RECORD_START':
      await startSkillRecording();
      break;
    case 'SKILL_RECORD_STOP':
      await stopSkillRecording();
      break;
    case 'SKILL_PREVIEW': {
      const tabId = await runTab(msg);
      if (tabId) await tabMessage(tabId, { type: 'ORION_SKILL_PREVIEW', run_id: msg.run_id, name: msg.name, steps: msg.steps || [] });
      break;
    }
    case 'SKILL_RUN_STATE': {
      const tabId = await runTab({ run_id: msg.run && msg.run.run_id, tab_id: msg.tab_id });
      if (tabId) await tabMessage(tabId, { type: 'ORION_SKILL_RUN_STATE', run: msg.run });
      break;
    }
    case 'SKILL_EXEC_STEP': {
      let res;
      try {
        res = await execSkillStep(msg);
      } catch (err) {
        res = { ok: false, error: String(err) };
      }
      sendToDaemon({ event: 'SKILL_STEP_RESULT', run_id: msg.run_id, nonce: msg.nonce, index: msg.index,
        tab_id: runTabs[msg.run_id] || null, ...(res || { ok: false, error: 'no answer from the page' }) });
      break;
    }
    case 'SKILL_PICK': {
      const tabId = await runTab(msg);
      const res = tabId ? await tabMessage(tabId, { type: 'ORION_SKILL_PICK', step: msg.step }) : null;
      sendToDaemon({ event: 'SKILL_PICKED', run_id: msg.run_id, nonce: msg.nonce, index: msg.index, target: (res && res.target) || null });
      break;
    }
    case 'SKILL_DRAFT_READY':
      chrome.action.setBadgeText({ text: '✓' }).catch(() => {});
      setTimeout(() => chrome.action.setBadgeText({ text: '' }).catch(() => {}), 5000);
      break;
    default:
      break;
  }
}
