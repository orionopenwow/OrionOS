/**
 * Orion Manifest V3 Background Service Worker
 * Establishes persistent WebSocket connection to Orion Daemon (ws://localhost:9120)
 * Observes active tab updates, navigations, and DOM content mutations.
 *
 * V2 Unified Comms Hub:
 *  - forwards Messenger / Discord DM messages captured by comms_observer.js (pinned tabs only)
 *    to the daemon as COMMS_INBOUND
 *  - executes approved replies (COMMS_INJECT_REPLY) by focusing the chat composer and typing
 *    through the Chrome DevTools Protocol (route: extension_cdp_inject), with a DOM fallback
 *  - clicking the Orion toolbar icon on a chat tab pins it so it gets mirrored
 *
 * Service-worker lifecycle (MV3): Chrome suspends idle workers after ~30 s, which closes the
 * socket. A 20 s keepalive PING keeps the worker and socket alive while the daemon is up, a
 * chrome.alarms tick reconnects after a suspension, and anything sent while the socket is
 * still connecting is queued and flushed on open instead of being dropped.
 */

importScripts('skills_bg.js'); // Skill Recorder (record / replay browser skills)

const ORION_WS_URL = 'ws://localhost:9120';
const ORION_HTTP_URL = 'http://localhost:9120';
// Orion only lets this extension in after a one-time pairing: Orion shows a short code (Control > Pair, or
// `orion-ctl extension pair`), you type it in this extension's popup, and Orion hands back a key kept here.
// The key opens the WebSocket only, as a limited "extension" client (apps/daemon/extension_pairing.py).
const KEY_STORE = 'orion_pair_key';
let connecting = false;
const KEEPALIVE_MS = 20_000;
const MAX_QUEUE = 500;
let socket = null;
let reconnectTimer = null;
let keepaliveTimer = null;
const outbox = [];

async function pairedKey() {
  const got = await chrome.storage.local.get(KEY_STORE);
  return typeof got[KEY_STORE] === 'string' ? got[KEY_STORE] : '';
}

function connectWebSocket() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  if (connecting) return;
  connecting = true;
  pairedKey().then((key) => {
    connecting = false;
    if (key) openSocket(key); // not paired yet: wait for the popup (the alarm checks again)
  }, () => { connecting = false; });
}

function openSocket(key) {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  try {
    socket = new WebSocket(`${ORION_WS_URL}/?token=${encodeURIComponent(key)}`);

    socket.onopen = () => {
      console.log('[Orion Extension] Connected to Daemon at', ORION_WS_URL);
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      socket.send(JSON.stringify({ event: 'CLIENT_HELLO', client: 'extension', browser: 'chrome' }));
      while (outbox.length && socket.readyState === WebSocket.OPEN) {
        socket.send(outbox.shift());
      }
      clearInterval(keepaliveTimer);
      keepaliveTimer = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ event: 'PING' }));
      }, KEEPALIVE_MS);
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.event === 'PING') {
          socket.send(JSON.stringify({ event: 'PONG' }));
        } else if (msg.event === 'COMMS_INJECT_REPLY') {
          injectReply(msg);
        } else if (typeof msg.event === 'string' && msg.event.startsWith('SKILL_')) {
          handleSkillMessage(msg);
        }
      } catch (err) {
        console.error('[Orion Extension] Failed to parse message', err);
      }
    };

    socket.onclose = () => {
      clearInterval(keepaliveTimer);
      console.warn('[Orion Extension] Disconnected from daemon. Reconnecting in 3s...');
      scheduleReconnect();
    };

    socket.onerror = (err) => {
      console.error('[Orion Extension] WebSocket error:', err);
      socket.close();
    };
  } catch (err) {
    console.error('[Orion Extension] Failed to initialize WebSocket', err);
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (!reconnectTimer) {
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectWebSocket();
    }, 3000);
  }
}

/** Send now if connected, otherwise queue until the socket opens (never silently drop). */
function sendToDaemon(payload, { queue = true } = {}) {
  const data = JSON.stringify(payload);
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(data);
    return;
  }
  if (queue) {
    outbox.push(data);
    if (outbox.length > MAX_QUEUE) outbox.shift();
  }
  connectWebSocket();
}

// Survives worker suspension: setTimeout-based reconnects do not.
chrome.alarms.create('orion-reconnect', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'orion-reconnect') connectWebSocket();
});

/**
 * Extracts visible textual content and metadata from the specified tab
 */
async function extractTabContent(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab || !tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('edge://')) {
      return;
    }

    const injection = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        // Collect visible body text, truncated to avoid IPC overload
        const bodyText = document.body ? document.body.innerText : '';
        return {
          title: document.title,
          textSample: bodyText.slice(0, 3000).trim(),
        };
      },
    });

    if (injection && injection[0] && injection[0].result) {
      const { title, textSample } = injection[0].result;
      sendTabMutation({
        url: tab.url,
        title: title || tab.title || '',
        text_sample: textSample || '',
        tab_id: tabId,
        browser: 'chrome',
      });
    }
  } catch (err) {
    // Ignore restricted pages
  }
}

function sendTabMutation(payload) {
  // Tab snapshots are only useful live; don't queue them.
  // Strip private URL parameters (passwords, card numbers, codes, tokens) before the snapshot leaves the browser.
  const clean = typeof scrubEvent === 'function' ? scrubEvent(payload) : payload;
  sendToDaemon({ event: 'BROWSER_TAB_MUTATED', timestamp: Date.now() / 1000, ...clean }, { queue: false });
}

// Tab navigation & change listeners
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.active) {
    extractTabContent(tabId);
  }
});

chrome.tabs.onActivated.addListener((activeInfo) => {
  extractTabContent(activeInfo.tabId);
});

// Initialize connection
connectWebSocket();

// =====================================================================
// V2 Unified Comms Hub
// =====================================================================
const CHAT_URLS = {
  discord: ['https://discord.com/channels/@me*'],
  messenger: ['https://www.messenger.com/*', 'https://www.facebook.com/messages/*'],
};
const CONTENT_SCRIPT_URLS = [
  'https://www.messenger.com/*',
  'https://www.facebook.com/messages/*',
  'https://discord.com/channels/*',
];

function isChatUrl(url) {
  return /^https:\/\/(discord\.com\/channels\/@me|www\.messenger\.com\/|www\.facebook\.com\/messages\/)/.test(url || '');
}

/** Exact conversation id from a chat URL (never a substring match: /t/john must not match /t/johnny). */
function threadIdFromUrl(platform, url) {
  try {
    const path = new URL(url).pathname;
    const m = platform === 'discord' ? path.match(/^\/channels\/@me\/(\d+)/) : path.match(/\/(?:e2ee\/)?t\/([^/?#]+)/);
    return m ? m[1] : null;
  } catch (err) {
    return null;
  }
}

async function pinnedOnly() {
  try {
    const { orion_pinned_only } = await chrome.storage.local.get('orion_pinned_only');
    return orion_pinned_only !== false;
  } catch (err) {
    return true;
  }
}

// Manifest content scripts are not injected into tabs that were already open when the
// extension was installed / reloaded, so inject them explicitly.
async function injectObserverIntoOpenTabs() {
  const tabs = await chrome.tabs.query({ url: CONTENT_SCRIPT_URLS }).catch(() => []);
  for (const tab of tabs) {
    await ensureObserver(tab.id);
  }
}

async function ensureObserver(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ['comms_observer.js'] });
    return true;
  } catch (err) {
    return false;
  }
}

// ------------------------------------------------------------------ pairing with Orion (popup)
async function pairWithOrion(code) {
  let res;
  try {
    res = await fetch(`${ORION_HTTP_URL}/extension/pair`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: String(code || '') }),
    });
  } catch (err) {
    return { ok: false, error: "Orion isn't answering on this computer. Is it running?" };
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || typeof data.key !== 'string') return { ok: false, error: data.error || `Pairing failed (${res.status}).` };
  await chrome.storage.local.set({ [KEY_STORE]: data.key });
  if (socket) {
    try { socket.close(); } catch (err) { /* already closed */ }
  }
  connectWebSocket();
  return { ok: true };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'ORION_PAIR') {
    pairWithOrion(msg.code).then(sendResponse);
    return true;
  }
  if (msg?.type === 'ORION_PAIR_STATUS') {
    pairedKey().then((key) => sendResponse({ paired: !!key, connected: !!(socket && socket.readyState === WebSocket.OPEN) }));
    return true;
  }
  if (msg?.type === 'ORION_UNPAIR') {
    chrome.storage.local.remove(KEY_STORE).then(() => {
      if (socket) {
        try { socket.close(); } catch (err) { /* already closed */ }
      }
      sendResponse({ ok: true });
    });
    return true;
  }
  return undefined;
});

chrome.runtime.onInstalled.addListener(injectObserverIntoOpenTabs);
chrome.runtime.onStartup.addListener(injectObserverIntoOpenTabs);

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg?.type !== 'ORION_COMMS_INBOUND' || !sender.tab) return;
  (async () => {
    if ((await pinnedOnly()) && !sender.tab.pinned) return;
    sendToDaemon({ event: 'COMMS_INBOUND', ...msg.payload });
  })();
});

// Toolbar click on a chat tab pins it (pinned chat tabs are the ones Orion mirrors).
chrome.action.onClicked.addListener(async (tab) => {
  if (tab?.id && isChatUrl(tab.url)) {
    await chrome.tabs.update(tab.id, { pinned: !tab.pinned });
    await ensureObserver(tab.id);
  }
});

function injectResult(request_id, ok, error) {
  sendToDaemon({ event: 'COMMS_INJECT_RESULT', request_id, ok, error: error || null });
}

async function findThreadTab(platform, threadId) {
  const tabs = await chrome.tabs.query({ url: CHAT_URLS[platform] || [] });
  return tabs.find((t) => threadIdFromUrl(platform, t.url || '') === String(threadId)) || null;
}

async function sendToTab(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (err) {
    // "Receiving end does not exist": the observer isn't in this tab yet. Inject and retry once.
    if (await ensureObserver(tabId)) {
      await new Promise((r) => setTimeout(r, 300));
      return chrome.tabs.sendMessage(tabId, message).catch((e) => ({ ok: false, error: String(e) }));
    }
    return { ok: false, error: String(err) };
  }
}

async function cdpKey(target, key, code, modifiers = 0, withText = true) {
  for (const type of ['keyDown', 'keyUp']) {
    const params = { type, key, code: key, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, modifiers };
    if (type === 'keyDown' && withText) params.text = '\r';
    await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', params);
  }
}

async function cdpType(target, text) {
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]) await chrome.debugger.sendCommand(target, 'Input.insertText', { text: lines[i] });
    if (i < lines.length - 1) await cdpKey(target, 'Enter', 13, 8, false); // Shift+Enter = newline
  }
  await new Promise((r) => setTimeout(r, 120));
  await cdpKey(target, 'Enter', 13, 0, true); // send
}

async function injectReply(msg) {
  const { request_id, platform, thread_id, text } = msg;
  try {
    const tab = await findThreadTab(platform, thread_id);
    if (!tab) {
      injectResult(request_id, false, `no open ${platform} tab for this conversation`);
      return;
    }
    const focus = await sendToTab(tab.id, { type: 'ORION_FOCUS_COMPOSER', thread_id });
    if (!focus?.ok) {
      injectResult(request_id, false, focus?.error || 'could not focus message box');
      return;
    }

    const target = { tabId: tab.id };
    let attached = false;
    try {
      await chrome.debugger.attach(target, '1.3');
      attached = true;
    } catch (attachErr) {
      // DevTools protocol unavailable (e.g. DevTools already open on that tab). Nothing has been
      // typed yet, so the DOM fallback is safe to use.
      const res = await sendToTab(tab.id, { type: 'ORION_DOM_TYPE_FALLBACK', text });
      injectResult(request_id, !!res?.ok, res?.ok ? null : `CDP unavailable (${attachErr}); DOM fallback failed (${res?.error})`);
      return;
    }
    try {
      await cdpType(target, text);
      injectResult(request_id, true);
    } catch (typeErr) {
      // Part of the text may already be in the box or sent: never retype (that could double-send).
      injectResult(request_id, false, `typing interrupted — check the chat before resending (${typeErr})`);
    } finally {
      if (attached) await chrome.debugger.detach(target).catch(() => {});
    }
  } catch (err) {
    injectResult(request_id, false, String(err));
  }
}
