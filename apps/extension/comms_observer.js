/**
 * Orion Comms Observer (content script) — V2 Unified Comms Hub.
 *
 * Watches the DOM of your own logged-in Facebook Messenger and Discord DM tabs and forwards
 * new messages to the Orion daemon through the background worker. Nothing here talks to
 * Discord/Meta APIs — it only reads what is already rendered in your browser, which avoids
 * unofficial-API bans. Only pinned tabs are mirrored (click the Orion toolbar icon on a chat
 * tab to pin it).
 *
 * The background worker also asks this script to focus the message composer before it types
 * an approved reply through the Chrome DevTools Protocol (extension_cdp_inject route).
 */
(() => {
  if (window.__orionCommsObserver) return;
  window.__orionCommsObserver = true;

  const HOST = location.host;
  const PLATFORM = HOST.includes('discord.com') ? 'discord' : 'messenger';
  const BACKFILL = 15;

  let currentThread = null;
  let seen = new Set();
  let scanTimer = null;

  // ------------------------------------------------------------------ helpers
  const clean = (s) => (s || '').replace(/\s+\n/g, '\n').replace(/[ \t]+/g, ' ').trim();

  function hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(36);
  }

  function threadFromUrl() {
    const path = location.pathname;
    if (PLATFORM === 'discord') {
      const m = path.match(/^\/channels\/@me\/(\d+)/);
      return m ? m[1] : null;
    }
    const m = path.match(/\/(?:e2ee\/)?t\/([^/?#]+)/);
    return m ? m[1] : null;
  }

  function threadName() {
    let t = document.title || '';
    t = t.replace(/^\(\d+\+?\)\s*/, '').replace(/^(?:[•●]\s*)/, '');
    t = t.replace(/\s*[|\-–]\s*(Discord|Messenger|Facebook)\s*$/i, '').replace(/^(Discord|Messenger)\s*[|\-–]\s*/i, '');
    t = t.replace(/^@/, '').trim();
    if (PLATFORM === 'messenger') {
      const header = document.querySelector('[role="main"] h1, [role="main"] h2');
      if (header && clean(header.innerText)) return clean(header.innerText).split('\n')[0];
    }
    return t || threadFromUrl() || 'Unknown';
  }

  function ownDiscordName() {
    const area = document.querySelector('section[aria-label="User area"], [class*="panels"] [class*="nameTag"]');
    return area ? clean(area.innerText).split('\n')[0] : null;
  }

  // ------------------------------------------------------------- extraction
  function extractDiscord() {
    const out = [];
    let lastAuthor = null;
    const me = ownDiscordName();
    document.querySelectorAll('li[id^="chat-messages-"]').forEach((li) => {
      const authorEl = li.querySelector('[id^="message-username-"]');
      if (authorEl) lastAuthor = clean(authorEl.innerText).split('\n')[0];
      const contentEl = li.querySelector('[id^="message-content-"]');
      const text = contentEl ? clean(contentEl.innerText) : '';
      if (!text) return;
      const author = lastAuthor || 'Unknown';
      out.push({
        external_id: li.id.replace('chat-messages-', ''),
        author,
        text,
        direction: me && author === me ? 'out' : 'in',
      });
    });
    return out;
  }

  function extractMessenger() {
    const out = [];
    const main = document.querySelector('[role="main"]') || document.body;
    const runs = {};
    let prevKey = '';
    const name = threadName();
    main.querySelectorAll('div[role="row"]').forEach((row) => {
      const parts = Array.from(row.querySelectorAll('div[dir="auto"]'))
        .filter((el) => !el.querySelector('div[dir="auto"]'))
        .map((el) => clean(el.innerText))
        .filter(Boolean);
      const text = clean(parts.join('\n'));
      if (!text || text.length > 6000) return;
      const rowRect = row.getBoundingClientRect();
      const bubble = row.querySelector('div[dir="auto"]');
      const rect = bubble ? bubble.getBoundingClientRect() : rowRect;
      const outgoing = rect.left + rect.width / 2 > rowRect.left + rowRect.width * 0.55;
      const avatar = row.querySelector('img[alt]');
      const author = outgoing ? 'You' : clean(avatar?.getAttribute('alt')) || name;
      // Identity = message + the message right before it (+ position inside a run of identical
      // messages). Unlike a global occurrence count it stays stable when old rows unload.
      const key = `${author}|${text}`;
      const runKey = `${prevKey}>>${key}`;
      runs[runKey] = key === prevKey ? (runs[runKey] || 0) + 1 : 1;
      prevKey = key;
      out.push({
        external_id: hash(`${runKey}#${runs[runKey]}`),
        author,
        text,
        direction: outgoing ? 'out' : 'in',
      });
    });
    return out;
  }

  // ------------------------------------------------------------------- scan
  function scan() {
    const thread = threadFromUrl();
    if (!thread) return;
    const messages = PLATFORM === 'discord' ? extractDiscord() : extractMessenger();
    if (!messages.length) return;

    if (thread !== currentThread) {
      // New conversation opened: mirror the last few as backfill, never as unread.
      currentThread = thread;
      seen = new Set(messages.map((m) => m.external_id));
      messages.slice(-BACKFILL).forEach((m) => send(m, thread, true));
      return;
    }
    // Only messages rendered *below* the newest one we already know are new; anything above it
    // is older history that loaded while scrolling up.
    let lastKnown = -1;
    messages.forEach((m, i) => {
      if (seen.has(m.external_id)) lastKnown = i;
    });
    messages.forEach((m, i) => {
      if (seen.has(m.external_id)) return;
      seen.add(m.external_id);
      if (i > lastKnown) send(m, thread, false);
    });
  }

  function send(m, thread, backfill) {
    try {
      chrome.runtime.sendMessage({
        type: 'ORION_COMMS_INBOUND',
        payload: {
          platform: PLATFORM,
          thread_id: thread,
          thread_name: threadName(),
          thread_url: location.href.split('?')[0],
          author: m.author,
          text: m.text,
          direction: m.direction,
          external_id: `${PLATFORM}-${thread}-${m.external_id}`,
          backfill,
          timestamp: Date.now() / 1000,
        },
      });
    } catch (err) {
      // Extension reloaded: this content script is orphaned.
    }
  }

  function scheduleScan() {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, 450);
  }

  new MutationObserver(scheduleScan).observe(document.documentElement, { childList: true, subtree: true });
  setInterval(scan, 4000);
  scheduleScan();

  // -------------------------------------------------------------- reply focus
  function findComposer() {
    if (PLATFORM === 'discord') {
      return document.querySelector('[role="textbox"][data-slate-editor="true"]');
    }
    const boxes = Array.from(document.querySelectorAll('[role="textbox"][contenteditable="true"]'));
    return boxes.find((b) => /message|aa/i.test(b.getAttribute('aria-label') || '')) || boxes[boxes.length - 1];
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'ORION_FOCUS_COMPOSER') {
      if (msg.thread_id && threadFromUrl() !== msg.thread_id) {
        sendResponse({ ok: false, error: 'chat tab is showing a different conversation' });
        return;
      }
      const box = findComposer();
      if (!box) {
        sendResponse({ ok: false, error: 'message box not found' });
        return;
      }
      box.focus();
      sendResponse({ ok: true });
    } else if (msg?.type === 'ORION_DOM_TYPE_FALLBACK') {
      const box = findComposer();
      if (!box) {
        sendResponse({ ok: false, error: 'message box not found' });
        return;
      }
      box.focus();
      document.execCommand('insertText', false, msg.text);
      const enter = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
      box.dispatchEvent(new KeyboardEvent('keydown', enter));
      box.dispatchEvent(new KeyboardEvent('keyup', enter));
      sendResponse({ ok: true });
    }
  });
})();
