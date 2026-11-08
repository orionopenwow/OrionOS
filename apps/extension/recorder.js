/**
 * Orion Skill Recorder — content script (injected on demand by skills_bg.js).
 *
 * Recording: every user action becomes one event with several selectors for the element (test id, ARIA role +
 * accessible name, label, visible text, placeholder, CSS path, and coordinates as a last resort). A pill on the
 * page shows that Orion is recording the whole time.
 *
 * Never recorded: password fields, card fields (autocomplete cc-*), one-time codes (autocomplete one-time-code
 * or short numeric boxes near "code"), and whole pages that look like sign-in, bank or password-manager pages —
 * recording pauses there and the pill says so.
 *
 * Replay: executes one step at a time for the daemon. Selectors are tried in order, then a fuzzy text match,
 * then it asks the user to click the right element. Anything that would submit/send/buy/delete/post is refused
 * unless the daemon says the user approved it, and unapproved form submits are blocked outright.
 *
 * The pure helpers are exposed as window.__orionSkillRecorder for tests (in the extension this lives in the
 * isolated content-script world, so web pages can't reach it).
 */
(() => {
  if (window.__orionSkillRecorder) return;

  // ------------------------------------------------------------------ pure helpers
  const RISKY_WORDS = /\b(send|buy|pay|purchase|checkout|check out|place order|order now|delete|remove|erase|post|publish|submit|confirm|transfer|tweet|unsubscribe)\b/i;
  const SENSITIVE_PAGE = /(log[ -_]?in|sign[ -_]?in|signin|password|passwd|2fa|two[- ]factor|mfa|otp|verification|1password|bitwarden|lastpass|keepass|dashlane|vault\b|bank|banking|credential|\/auth\b|oauth|sso)/i;
  const CARD_HINT = /(card.?num|cc.?num|credit.?card|cvv|cvc|csc|security.?code|expir|exp.?date)/i;
  const CODE_HINT = /(code|otp|2fa|mfa|totp|token|verification|one.?time|pin\b|passcode)/i;
  const PASSWORD_HINT = /(passw|pwd\b|passphrase|secret)/i;
  const TEST_ATTRS = ['data-testid', 'data-test-id', 'data-test', 'data-qa', 'data-cy'];
  const INTERACTIVE = 'a[href],button,input,select,textarea,summary,label,[role],[onclick],[contenteditable=""],[contenteditable="true"],[tabindex]';

  const clean = (s, n = 120) => String(s || '').replace(/\s+/g, ' ').trim().slice(0, n);

  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && Number(st.opacity) !== 0;
  }

  function implicitRole(el) {
    const explicit = el.getAttribute && el.getAttribute('role');
    if (explicit) return explicit.split(/\s+/)[0];
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const type = (el.getAttribute && (el.getAttribute('type') || '').toLowerCase()) || '';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'img' && el.getAttribute('alt')) return 'img';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'option') return 'option';
    if (tag === 'input') {
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'search') return 'searchbox';
      if (type === 'number') return 'spinbutton';
      if (['', 'text', 'email', 'tel', 'url'].includes(type)) return 'textbox';
      return '';
    }
    if (el.isContentEditable) return 'textbox';
    return '';
  }

  function labelText(el) {
    if (!el || !el.labels) return '';
    const parts = [];
    for (const l of el.labels) parts.push(l.innerText || l.textContent || '');
    return clean(parts.join(' '));
  }

  function accessibleName(el) {
    if (!el || !el.getAttribute) return '';
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.innerText || n.textContent).join(' ');
      if (clean(t)) return clean(t);
    }
    const aria = el.getAttribute('aria-label');
    if (clean(aria)) return clean(aria);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['input', 'select', 'textarea'].includes(tag)) {
      if (tag === 'input' && ['submit', 'button', 'reset'].includes(type)) return clean(el.value || (type === 'submit' ? 'Submit' : ''));
      if (tag === 'input' && type === 'image') return clean(el.getAttribute('alt'));
      return labelText(el) || clean(el.getAttribute('placeholder')) || clean(el.getAttribute('title'));
    }
    if (tag === 'img') return clean(el.getAttribute('alt'));
    const text = clean(el.innerText || el.textContent);
    return text || clean(el.getAttribute('title'));
  }

  function fieldHints(el) {
    return [el.getAttribute('name'), el.id, el.getAttribute('placeholder'), el.getAttribute('aria-label'), labelText(el)].filter(Boolean).join(' ');
  }

  function nearbyText(el) {
    const box = el.closest('form, fieldset, section, div');
    return clean(box ? box.innerText : '', 300);
  }

  /** Why this field must never be recorded ('password' | 'card' | 'code'), or ''. */
  function sensitiveFieldReason(el) {
    if (!el || !el.tagName) return '';
    const tag = el.tagName.toLowerCase();
    if (!['input', 'textarea', 'select'].includes(tag) && !el.isContentEditable) return '';
    const type = (el.getAttribute('type') || '').toLowerCase();
    const auto = (el.getAttribute('autocomplete') || '').toLowerCase();
    const hints = fieldHints(el);
    if (type === 'password' || auto.includes('password') || PASSWORD_HINT.test(hints)) return 'password';
    if (/(^|\s)cc-/.test(auto) || CARD_HINT.test(hints)) return 'card';
    if (auto.includes('one-time-code')) return 'code';
    const numeric = (el.getAttribute('inputmode') || '').toLowerCase() === 'numeric' || type === 'tel' || type === 'number';
    const maxlen = Number(el.getAttribute('maxlength') || 0);
    if (numeric && maxlen > 0 && maxlen <= 8 && CODE_HINT.test(hints + ' ' + nearbyText(el))) return 'code';
    if (/(otp|2fa|mfa|totp|one.?time)/i.test(hints)) return 'code';
    return '';
  }

  /** Whole page looks like a sign-in, bank or password-manager page: pause recording. */
  function pageSensitiveReason(doc = document, loc = location) {
    const pw = [...doc.querySelectorAll('input[type=password]')].some(visible);
    if (pw) {
      // A sign-in form is a password box plus one or two others (email / user name / code). A long form that
      // merely contains a password box is still recorded; the password box itself never is.
      const others = [...doc.querySelectorAll('input,select,textarea')].filter((el) => visible(el) &&
        !['password', 'hidden', 'submit', 'button', 'checkbox', 'radio', 'reset', 'image'].includes((el.getAttribute('type') || '').toLowerCase()));
      if (others.length <= 2) return 'this looks like a sign-in page';
    }
    if (SENSITIVE_PAGE.test(`${loc.host}${loc.pathname} ${doc.title}`)) return 'this looks like a sign-in, bank or password page';
    return '';
  }

  function inForm(el) {
    return !!(el.form || (el.closest && el.closest('form')));
  }

  /** Why acting on this element needs the user's OK ('' when it doesn't). */
  function riskReason(el) {
    if (!el || !el.tagName) return '';
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && (type === 'submit' || type === 'image')) return 'submits a form';
    if (tag === 'button' && inForm(el) && (type === '' || type === 'submit')) return 'submits a form';
    const m = RISKY_WORDS.exec(`${accessibleName(el)} ${clean(el.innerText || el.value || '', 80)}`);
    if (m && ['a', 'button', 'input', 'summary'].includes(tag) || (m && /button|link|menuitem/.test(implicitRole(el)))) {
      return `looks like it will ${m[1].toLowerCase()}`;
    }
    return '';
  }

  const UNSTABLE = /(\d{4,}|[a-f0-9]{8,}|^[a-z]{1,3}-[a-z0-9]{5,}$|^:r|ember\d|react-select)/i;

  function cssPath(el) {
    if (!(el instanceof Element)) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      if (node.id && !UNSTABLE.test(node.id) && document.querySelectorAll('#' + CSS.escape(node.id)).length === 1) {
        parts.unshift('#' + CSS.escape(node.id));
        break;
      }
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  }

  /** Several ways to find this element again, most robust first; coordinates last. */
  function selectorsFor(el) {
    const out = [];
    for (const a of TEST_ATTRS) {
      const v = el.getAttribute(a);
      if (v) { out.push({ kind: 'testid', attr: a, value: v }); break; }
    }
    const role = implicitRole(el);
    const name = accessibleName(el);
    if (role && name) out.push({ kind: 'role', role, name });
    const tag = el.tagName.toLowerCase();
    const isField = ['input', 'select', 'textarea'].includes(tag) || el.isContentEditable;
    if (isField) {
      const lab = labelText(el);
      if (lab) out.push({ kind: 'label', value: lab });
      const ph = el.getAttribute('placeholder');
      if (ph) out.push({ kind: 'placeholder', value: clean(ph) });
    } else {
      const text = clean(el.innerText || el.textContent, 80);
      if (text && text.length <= 80) out.push({ kind: 'text', value: text });
    }
    const css = cssPath(el);
    if (css) out.push({ kind: 'css', value: css });
    const r = el.getBoundingClientRect();
    out.push({ kind: 'coords', x: Math.round(r.left + r.width / 2 + scrollX), y: Math.round(r.top + r.height / 2 + scrollY),
      vw: innerWidth, vh: innerHeight });
    return out;
  }

  function describeTarget(el) {
    const tag = el.tagName.toLowerCase();
    const t = {
      selectors: selectorsFor(el),
      role: implicitRole(el),
      name: accessibleName(el),
      text: clean(el.innerText || el.textContent, 80),
      tag,
      type: (el.getAttribute('type') || '').toLowerCase(),
      label: labelText(el),
      placeholder: clean(el.getAttribute('placeholder')),
      in_form: inForm(el),
      editable: !!el.isContentEditable,
    };
    const tid = t.selectors.find((s) => s.kind === 'testid');
    if (tid) t.testid = tid.value;
    if (tag === 'a') t.href = el.href;
    if (tag === 'label' && el.control) t.label_for_control = el.control.type || el.control.tagName.toLowerCase();
    if (['input', 'textarea'].includes(tag)) {
      Object.assign(t, { autocomplete: el.getAttribute('autocomplete') || '', inputmode: el.getAttribute('inputmode') || '',
        maxlength: el.getAttribute('maxlength') || '', field_name: el.getAttribute('name') || '', field_id: el.id || '' });
    }
    return t;
  }

  // ---- finding elements again
  function bigrams(s) {
    const n = clean(s).toLowerCase();
    const out = new Map();
    for (let i = 0; i < n.length - 1; i++) out.set(n.slice(i, i + 2), (out.get(n.slice(i, i + 2)) || 0) + 1);
    return out;
  }

  /** Dice similarity of two strings, 0..1. */
  function similarity(a, b) {
    const A = bigrams(a), B = bigrams(b);
    if (!A.size || !B.size) return clean(a).toLowerCase() === clean(b).toLowerCase() && clean(a) ? 1 : 0;
    let inter = 0, total = 0;
    for (const [k, v] of A) { inter += Math.min(v, B.get(k) || 0); total += v; }
    for (const v of B.values()) total += v;
    return (2 * inter) / total;
  }

  function candidates(root = document) {
    return [...root.querySelectorAll(INTERACTIVE)].filter(visible);
  }

  function resolveSelector(sel, root = document) {
    try {
      switch (sel.kind) {
        case 'testid':
          return [...root.querySelectorAll(`[${sel.attr || 'data-testid'}="${CSS.escape(sel.value)}"]`)];
        case 'role':
          return candidates(root).filter((el) => implicitRole(el) === sel.role && accessibleName(el) === sel.name);
        case 'label':
          return [...root.querySelectorAll('input,select,textarea')].filter((el) => labelText(el) === sel.value);
        case 'placeholder':
          return [...root.querySelectorAll('[placeholder]')].filter((el) => clean(el.getAttribute('placeholder')) === sel.value);
        case 'text':
          return candidates(root).filter((el) => clean(el.innerText || el.textContent, 80) === sel.value)
            .filter((el, _, all) => !all.some((o) => o !== el && el.contains(o))); // innermost
        case 'css':
          return [...root.querySelectorAll(sel.value)];
        default:
          return [];
      }
    } catch (err) {
      return [];
    }
  }

  /**
   * Find a recorded target: selectors in order (first one with exactly one visible match wins), then a fuzzy
   * text match, then the recorded coordinates (only if the element there still looks like the right one).
   * Returns {el, matched, index, healed} or {el: null, ambiguous}.
   */
  function findTarget(target, root = document) {
    const sels = (target && target.selectors) || [];
    let ambiguous = false;
    for (let i = 0; i < sels.length; i++) {
      if (sels[i].kind === 'coords') continue;
      const found = resolveSelector(sels[i], root).filter(visible);
      if (found.length === 1) return { el: found[0], matched: sels[i].kind, index: i, healed: i > 0 };
      if (found.length > 1) ambiguous = true;
    }
    const want = target.name || target.label || target.text || target.placeholder || '';
    if (want) {
      const scored = candidates(root)
        .filter((el) => !target.role || implicitRole(el) === target.role || !implicitRole(el))
        .map((el) => ({ el, score: similarity(want, accessibleName(el) || el.getAttribute('placeholder') || '') }))
        .sort((a, b) => b.score - a.score);
      if (scored.length && scored[0].score >= 0.75 && (scored.length === 1 || scored[0].score - scored[1].score >= 0.1)) {
        return { el: scored[0].el, matched: 'fuzzy', index: -1, healed: true, score: scored[0].score };
      }
    }
    const c = sels.find((s) => s.kind === 'coords');
    if (c) {
      const el = document.elementFromPoint(c.x - scrollX, c.y - scrollY);
      const hit = el && el.closest(INTERACTIVE);
      if (hit && (!want || similarity(want, accessibleName(hit)) >= 0.6)) {
        return { el: hit, matched: 'coords', index: sels.indexOf(c), healed: true };
      }
    }
    return { el: null, ambiguous };
  }

  // ------------------------------------------------------------------ chrome glue
  const hasChrome = typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id;
  const state = { mode: 'off', recId: null, paused: '', count: 0, allowSubmitUntil: 0, guardUntil: 0, pendingFill: new Map(), run: null };

  function emit(event) {
    const ev = { t: Date.now(), page_url: location.href, page_title: document.title, ...event };
    if (state.mode === 'record') {
      state.count++;
      renderPill();
      if (hasChrome) chrome.runtime.sendMessage({ type: 'ORION_SKILL_EVENT', rec_id: state.recId, event: ev }).catch(() => {});
    } else if (state.mode === 'notice' && hasChrome) {
      const { value, files, ...noValue } = ev;
      chrome.runtime.sendMessage({ type: 'ORION_SKILL_ACTIVITY', event: noValue }).catch(() => {});
    }
    (window.__orionSkillEvents = window.__orionSkillEvents || []).push(ev); // test hook (isolated world in the extension)
  }

  // ---- pill (closed shadow root: the page can't restyle or read it)
  let pillHost = null, pillRoot = null;
  function ensurePill() {
    if (pillHost && pillHost.isConnected) return;
    pillHost = document.createElement('orion-skill-pill');
    pillHost.style.cssText = 'all:initial;position:fixed;z-index:2147483647;bottom:16px;left:50%;transform:translateX(-50%);';
    pillRoot = pillHost.attachShadow({ mode: 'closed' });
    document.documentElement.appendChild(pillHost);
  }
  function removePill() {
    if (pillHost) pillHost.remove();
    pillHost = null;
  }

  function pillHTML(color, text, buttons) {
    return `<style>
      .p{font:13px/1.3 system-ui,sans-serif;color:#fff;background:#14141c;border:1px solid ${color};border-radius:999px;
         padding:7px 8px 7px 12px;display:flex;align-items:center;gap:8px;box-shadow:0 6px 24px rgba(0,0,0,.35);max-width:min(92vw,640px)}
      .d{width:9px;height:9px;border-radius:50%;background:${color};flex:none;animation:b 1.2s infinite}
      @keyframes b{50%{opacity:.35}} .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      button{font:inherit;color:#fff;background:#2a2a38;border:1px solid #444;border-radius:999px;padding:3px 10px;cursor:pointer}
      button:hover{background:#3a3a4c}</style>
      <div class="p" role="status" aria-live="polite"><span class="d"></span><span class="t">${text}</span>${buttons}</div>`;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function renderPill(flash) {
    if (state.mode === 'record') {
      ensurePill();
      const paused = state.paused;
      pillRoot.innerHTML = paused
        ? pillHTML('#f5a524', esc(`Recording paused — ${paused}. Nothing here is saved.`), '<button data-a="stop">Stop</button>')
        : pillHTML('#ff4d5e', esc(flash || `Orion is recording · ${state.count} step${state.count === 1 ? '' : 's'}`),
          '<button data-a="wait">Wait for selected text</button><button data-a="stop">Stop</button>');
    } else if (state.mode === 'replay' && state.run) {
      ensurePill();
      const r = state.run;
      const btns = [];
      if (r.status === 'preview') btns.push('<button data-a="start">Start step by step</button><button data-a="run_all">Run all</button>');
      if (r.status === 'waiting_step') btns.push('<button data-a="next">Next step</button><button data-a="run_all">Run the rest</button>');
      if (!['done', 'failed', 'stopped'].includes(r.status)) btns.push('<button data-a="halt">Stop</button>');
      else btns.push('<button data-a="close">Close</button>');
      const color = r.status === 'waiting_approval' ? '#f5a524' : r.status === 'failed' ? '#ff4d5e' : '#7c5cff';
      pillRoot.innerHTML = pillHTML(color, esc(flash || `${r.name}: ${r.message || r.status}`), btns.join(''));
    } else {
      removePill();
      return;
    }
    // keep the page's text selection (for "Wait for selected text") and focus when a pill button is pressed
    pillRoot.querySelectorAll('button').forEach((b) => b.addEventListener('mousedown', (e) => e.preventDefault()));
    pillRoot.querySelectorAll('button').forEach((b) => b.addEventListener('click', (e) => {
      if (!e.isTrusted) return;
      const a = b.dataset.a;
      if (a === 'stop' && hasChrome) chrome.runtime.sendMessage({ type: 'ORION_SKILL_STOP' }).catch(() => {});
      else if (a === 'wait') recordWaitForSelection();
      else if (a === 'close') { clearHighlights(); state.mode = 'off'; removePill(); }
      else if (hasChrome && state.run) {
        chrome.runtime.sendMessage({ type: 'ORION_SKILL_CONTROL', run_id: state.run.run_id, action: a === 'halt' ? 'stop' : a }).catch(() => {});
      }
    }));
  }

  function fromPill(e) {
    return pillHost && e.composedPath && e.composedPath().includes(pillHost);
  }

  function recordWaitForSelection() {
    const text = clean(String(getSelection() || ''), 120);
    if (!text) { renderPill('Select some text on the page first, then press the button.'); setTimeout(() => renderPill(), 2500); return; }
    emit({ action: 'wait_text', text });
  }

  // ---- recording listeners
  function checkPage() {
    const reason = pageSensitiveReason();
    if (reason !== state.paused) {
      state.paused = reason;
      if (hasChrome) chrome.runtime.sendMessage({ type: 'ORION_SKILL_PAUSED', rec_id: state.recId, paused: !!reason, reason }).catch(() => {});
      renderPill();
    }
    return !reason;
  }

  function skipPrivate(el) {
    const why = sensitiveFieldReason(el);
    if (why) {
      renderPill(`Skipped a private ${why === 'card' ? 'card' : why === 'code' ? 'code' : 'password'} field — never recorded`);
      setTimeout(() => renderPill(), 2500);
    }
    return !!why;
  }

  function flushFill(el) {
    const v = state.pendingFill.get(el);
    if (v === undefined) return;
    state.pendingFill.delete(el);
    emit({ action: 'fill', target: describeTarget(el), value: v });
  }
  function flushAll() { for (const el of [...state.pendingFill.keys()]) flushFill(el); }

  function active() { return state.mode === 'record' || state.mode === 'notice'; }

  function onClick(e) {
    if (!active() || !e.isTrusted || fromPill(e)) return;
    if (!checkPage()) return;
    const el = e.target.closest ? e.target.closest(INTERACTIVE) || e.target : e.target;
    if (skipPrivate(el)) return;
    flushAll();
    emit({ action: 'click', target: describeTarget(el) });
  }

  function onInput(e) {
    if (!active() || !e.isTrusted || fromPill(e)) return;
    const el = e.target;
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    if (!(['input', 'textarea'].includes(tag) || el.isContentEditable)) return;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['checkbox', 'radio', 'file', 'submit', 'button'].includes(type)) return;
    if (!checkPage() || skipPrivate(el)) { state.pendingFill.delete(el); return; }
    state.pendingFill.set(el, el.isContentEditable ? clean(el.innerText, 5000) : el.value);
  }

  function onChange(e) {
    if (!active() || !e.isTrusted || fromPill(e)) return;
    const el = e.target;
    if (!checkPage() || skipPrivate(el)) { state.pendingFill.delete(el); return; }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'select') {
      const opt = el.selectedOptions && el.selectedOptions[0];
      emit({ action: 'select', target: describeTarget(el), value: el.value, option_text: opt ? clean(opt.textContent) : '' });
    } else if (type === 'checkbox' || type === 'radio') {
      emit({ action: 'check', target: describeTarget(el), checked: el.checked });
    } else if (type === 'file') {
      emit({ action: 'upload', target: describeTarget(el), files: [...(el.files || [])].map((f) => f.name) });
    } else {
      state.pendingFill.set(el, el.value);
      flushFill(el);
    }
  }

  function onKeydown(e) {
    if (!active() || !e.isTrusted || e.key !== 'Enter' || fromPill(e)) return;
    const el = e.target;
    if (!el.tagName || el.tagName.toLowerCase() !== 'input') return;
    if (!checkPage() || skipPrivate(el)) return;
    state.pendingFill.set(el, el.value);
    flushFill(el);
    emit({ action: 'press', key: 'Enter', target: describeTarget(el) });
  }

  /** Names of private fields on the page: a GET form would put their values into the next URL. */
  function reportPrivateNames(root = document) {
    if (!hasChrome || state.mode !== 'record') return;
    const names = [...root.querySelectorAll('input,textarea,select')].filter((el) => el.name && sensitiveFieldReason(el)).map((el) => el.name);
    if (names.length) chrome.runtime.sendMessage({ type: 'ORION_SKILL_PRIVATE_NAMES', names }).catch(() => {});
  }

  function onSubmit(e) {
    reportPrivateNames(e.target);
    const now = Date.now();
    if (state.mode === 'replay' && now < state.guardUntil && now > state.allowSubmitUntil) {
      // a replayed step tried to submit a form without the user's OK: block it
      e.preventDefault();
      e.stopImmediatePropagation();
      if (hasChrome) chrome.runtime.sendMessage({ type: 'ORION_SKILL_BLOCKED_SUBMIT' }).catch(() => {});
      return;
    }
    if (!active() || fromPill(e)) return;
    if (!checkPage()) return;
    flushAll();
    emit({ action: 'submit', target: describeTarget(e.target) });
  }

  document.addEventListener('click', onClick, true);
  document.addEventListener('input', onInput, true);
  document.addEventListener('change', onChange, true);
  document.addEventListener('keydown', onKeydown, true);
  document.addEventListener('submit', onSubmit, true);
  addEventListener('pagehide', () => { if (active()) flushAll(); });

  // ------------------------------------------------------------------ replay
  let highlights = [];
  function clearHighlights() { highlights.forEach((h) => h.remove()); highlights = []; }
  function highlight(el, label, color = '#7c5cff') {
    const r = el.getBoundingClientRect();
    const h = document.createElement('orion-skill-mark');
    h.style.cssText = `all:initial;position:absolute;z-index:2147483646;pointer-events:none;left:${r.left + scrollX - 3}px;top:${r.top + scrollY - 3}px;width:${r.width + 6}px;height:${r.height + 6}px;border:2px solid ${color};border-radius:6px;box-shadow:0 0 0 4px ${color}33;`;
    if (label) {
      const b = document.createElement('span');
      b.textContent = label;
      b.style.cssText = `all:initial;position:absolute;top:-11px;left:-11px;min-width:20px;height:20px;border-radius:10px;background:${color};color:#fff;font:bold 11px/20px system-ui;text-align:center;`;
      h.appendChild(b);
    }
    document.documentElement.appendChild(h);
    highlights.push(h);
    return h;
  }

  function preview(steps) {
    clearHighlights();
    const found = [];
    steps.forEach((s, i) => {
      if (!s.target) { found.push(null); return; }
      const hit = findTarget(s.target);
      found.push(hit.el ? hit.matched : false);
      if (hit.el) highlight(hit.el, String(i + 1), s.risky ? '#f5a524' : '#7c5cff');
    });
    return found;
  }

  function setNativeValue(el, value) {
    const proto = el.tagName.toLowerCase() === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function waitForText(text, timeoutMs = 20000) {
    const end = Date.now() + timeoutMs;
    while (Date.now() < end) {
      if (document.body && document.body.innerText.includes(text)) return true;
      await sleep(250);
    }
    return false;
  }

  /** The user does this one themselves (file pickers, private values): wait for a real change on the element. */
  function waitForUser(el, message) {
    highlight(el, '✋', '#f5a524');
    renderPill(message);
    el.scrollIntoView({ block: 'center' });
    return new Promise((resolve) => {
      const done = (e) => {
        if (!e.isTrusted || e.target !== el) return;
        document.removeEventListener('change', done, true);
        resolve(true);
      };
      document.addEventListener('change', done, true);
    });
  }

  async function execStep(step, approved) {
    const a = step.action;
    if (a === 'wait_text') {
      return (await waitForText(step.text, step.timeout_ms || 20000)) ? { ok: true } : { ok: false, error: `“${step.text}” didn't appear` };
    }
    if (a === 'wait_load') return { ok: true };
    const hit = findTarget(step.target || {});
    if (!hit.el) return { ok: false, need_pick: true, error: hit.ambiguous ? 'more than one match' : 'not found' };
    const el = hit.el;
    const risk = step.risky ? step.risk_reason || 'risky step' : (a === 'click' || a === 'press' || a === 'submit') ? riskReason(el) || (a === 'press' && inForm(el) ? 'submits a form' : '') : '';
    if (risk && !approved) return { ok: false, needs_approval: true, reason: risk };
    if (sensitiveFieldReason(el) && a === 'fill') return { ok: false, error: 'that is a private field — Orion never types there' };
    clearHighlights();
    el.scrollIntoView({ block: 'center', behavior: 'instant' });
    highlight(el, '', risk ? '#f5a524' : '#7c5cff');
    await sleep(180);
    if (approved) state.allowSubmitUntil = Date.now() + 3000;
    else state.guardUntil = Date.now() + 2500; // anything this step submits without an OK gets blocked
    const result = { ok: true, matched: hit.matched, healed: hit.healed };
    if (hit.healed) result.target = describeTarget(el);
    if (a === 'click') {
      el.click();
    } else if (a === 'fill') {
      if (step.ask) {
        await waitForUser(el, `Type the private value into “${accessibleName(el) || 'the highlighted box'}” yourself, then click outside it.`);
      } else if (el.isContentEditable) {
        el.focus();
        el.textContent = step.value || '';
        el.dispatchEvent(new InputEvent('input', { bubbles: true }));
      } else {
        el.focus();
        setNativeValue(el, step.value || '');
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
    } else if (a === 'select') {
      const want = step.option_text || step.value;
      const opt = [...el.options].find((o) => o.value === step.value || clean(o.textContent) === want);
      if (!opt) return { ok: false, error: `no option “${want}”` };
      el.value = opt.value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (a === 'check') {
      if (el.checked !== (step.checked !== false)) el.click();
    } else if (a === 'upload') {
      await waitForUser(el, `Pick ${(step.files || []).join(', ') || 'the file'} in the highlighted box yourself.`);
    } else if (a === 'press') {
      for (const type of ['keydown', 'keypress', 'keyup']) el.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
      const form = el.form;
      if (form && approved) form.requestSubmit();
    } else if (a === 'submit') {
      const form = el.tagName.toLowerCase() === 'form' ? el : el.form || el.closest('form');
      if (!form) return { ok: false, error: 'no form to submit' };
      form.requestSubmit();
    } else {
      return { ok: false, error: `unknown step ${a}` };
    }
    return result;
  }

  /** Ask the user to click the right element; resolves with its target description (or null on Esc). */
  function pickElement(step) {
    clearHighlights();
    renderPill(`I can't find ${step.description ? `the target for “${step.description}”` : 'this element'}. Click the right one (Esc cancels).`);
    return new Promise((resolve) => {
      const hover = (e) => {
        if (fromPill(e)) return;
        clearHighlights();
        const el = e.target.closest(INTERACTIVE) || e.target;
        if (el && el.getBoundingClientRect) highlight(el, '?', '#f5a524');
      };
      const finish = (val) => {
        document.removeEventListener('click', click, true);
        document.removeEventListener('mouseover', hover, true);
        document.removeEventListener('keydown', key, true);
        clearHighlights();
        renderPill();
        resolve(val);
      };
      const click = (e) => {
        if (!e.isTrusted || fromPill(e)) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        const el = e.target.closest(INTERACTIVE) || e.target;
        if (sensitiveFieldReason(el)) return;
        finish(describeTarget(el));
      };
      const key = (e) => { if (e.isTrusted && e.key === 'Escape') finish(null); };
      document.addEventListener('click', click, true);
      document.addEventListener('mouseover', hover, true);
      document.addEventListener('keydown', key, true);
    });
  }

  if (hasChrome) {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || typeof msg.type !== 'string' || !msg.type.startsWith('ORION_SKILL_')) return;
      (async () => {
        switch (msg.type) {
          case 'ORION_SKILL_RECORD':
            if (msg.on) {
              state.mode = 'record';
              state.recId = msg.rec_id;
              state.count = msg.count || 0;
              state.paused = '';
              checkPage();
              reportPrivateNames();
              renderPill();
            } else {
              flushAll();
              state.mode = msg.notice ? 'notice' : 'off';
              renderPill();
            }
            return { ok: true, paused: state.paused };
          case 'ORION_SKILL_NOTICE':
            if (state.mode === 'off') state.mode = 'notice';
            return { ok: true };
          case 'ORION_SKILL_PREVIEW':
            state.mode = 'replay';
            state.run = { run_id: msg.run_id, name: msg.name, status: 'preview', message: 'check the highlighted steps' };
            renderPill();
            return { ok: true, found: preview(msg.steps || []) };
          case 'ORION_SKILL_RUN_STATE':
            state.mode = 'replay';
            state.run = msg.run;
            if (['done', 'failed', 'stopped'].includes(msg.run.status)) clearHighlights();
            renderPill();
            return { ok: true };
          case 'ORION_SKILL_EXEC':
            state.mode = 'replay';
            try {
              return await execStep(msg.step, !!msg.approved);
            } catch (err) {
              return { ok: false, error: String(err && err.message || err) };
            }
          case 'ORION_SKILL_PICK':
            state.mode = 'replay';
            return { ok: true, target: await pickElement(msg.step || {}) };
          case 'ORION_SKILL_PING':
            return { ok: true, mode: state.mode };
          default:
            return { ok: false, error: 'unknown' };
        }
      })().then(sendResponse, (err) => sendResponse({ ok: false, error: String(err) }));
      return true;
    });
  }

  window.__orionSkillRecorder = {
    implicitRole, accessibleName, sensitiveFieldReason, pageSensitiveReason, riskReason, selectorsFor, cssPath,
    describeTarget, findTarget, similarity, execStep, preview, state,
    _setMode(mode) { state.mode = mode; },
  };
})();
