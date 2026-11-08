/* OrionOS site: two tabs (Site, Preview the OS) and every word and list from site/site.json.
   No build step: edit site.json and reload. */
(function () {
  'use strict';
  var qs = new URLSearchParams(location.search);
  var SOCIAL = qs.get('social') === '1';
  var $ = function (s) { return document.querySelector(s); };
  var el = function (tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k];
      else if (k === 'cls') n.className = attrs[k];
      else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return n;
  };

  // ---------------------------------------------------------------- tabs (hash: #preview, everything else is the site)
  var frame = $('#os-frame');
  function frameSrc(src) {
    if (frame.getAttribute('src') !== src) frame.setAttribute('src', src);
  }
  function show() {
    var preview = location.hash === '#preview';
    $('#site').hidden = preview;
    $('#preview').hidden = !preview;
    $('#tab-site').setAttribute('aria-selected', String(!preview));
    $('#tab-preview').setAttribute('aria-selected', String(preview));
    document.body.style.overflow = preview ? 'hidden' : '';
    if (preview && !frame.getAttribute('src')) frameSrc('app/');   // load the OS only when someone wants it
    if (preview) window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', show);
  $('#tour-link').addEventListener('click', function (e) {
    e.preventDefault();
    frameSrc('app/?tour=1');
    frame.focus();
  });
  show();

  // ---------------------------------------------------------------- content
  fetch('site/site.json', { cache: 'no-cache' })
    .then(function (r) { return r.json(); })
    .then(render)
    .catch(function () { /* the static fallback text in index.html stays */ });

  function gh(repo, href) {
    if (!href) return repo;
    if (/^https?:/.test(href)) return href;
    return repo + (/\.\w+$/.test(href) ? '/blob/main/' : '/tree/main/') + href;
  }

  function render(d) {
    var repo = d.meta.repo;
    document.title = d.meta.title;
    $('#repo-link').href = repo;
    document.querySelectorAll('[data-k]').forEach(function (n) {
      var v = n.getAttribute('data-k').split('.').reduce(function (o, k) { return o && o[k]; }, d);
      if (typeof v === 'string') n.textContent = v;
    });
    $('#hero-primary').textContent = d.hero.primary.label;
    $('#hero-primary').href = d.hero.primary.href;
    $('#hero-secondary').textContent = d.hero.secondary.label;
    $('#hero-secondary').href = d.hero.secondary.href;

    if (SOCIAL) {
      document.body.classList.add('social');
      $('.hero-text').insertBefore(el('img', { src: 'site/img/orion-os-lockup.png', alt: 'orion os', cls: 'social-logo' }), $('.hero-text').firstChild);
      return;
    }

    // features: the ones with a picture are big cards
    var f = $('#features');
    d.features.forEach(function (x) {
      var big = !!x.image;
      f.appendChild(el('article', { cls: 'feature' + (big ? ' big' : '') }, [
        big ? el('img', { src: x.image, alt: x.title, loading: 'lazy', width: '1600', height: '1000' }) : null,
        el('div', { cls: 'ft' }, [el('h3', { text: x.title }), el('p', { text: x.text })]),
      ]));
    });
    // the same picture twice in a row reads as a mistake: keep only the first card's picture
    var seen = {};
    f.querySelectorAll('.feature.big img').forEach(function (img) {
      var src = img.getAttribute('src');
      if (seen[src]) { img.parentNode.classList.remove('big'); img.remove(); }
      seen[src] = true;
    });

    d.safety.forEach(function (x) {
      $('#safety').appendChild(el('div', { cls: 'card safety-card' }, [el('h3', { text: x.title }), el('p', { text: x.text })]));
    });
    d.try.forEach(function (x) {
      $('#try-cards').appendChild(el('div', { cls: 'card try-card' }, [
        el('span', { cls: 'time', text: x.time }), el('h3', { text: x.title }), el('p', { text: x.what }),
        x.cmd ? el('code', { text: x.cmd }) : null,
        el('a', { href: gh(repo, x.href), text: 'Read the guide →' }),
      ]));
    });
    var sh = d.start_here;
    $('#start-here').append(el('h3', { text: sh.title }), el('p', { text: sh.text }), el('p', { cls: 'honest', text: sh.honest }));

    // commands
    var groups = [
      ['voice', 'Say it'], ['menu', 'Menu'], ['quick', 'Quick commands'], ['keys', 'Keyboard'], ['ctl', 'orion-ctl'],
    ].filter(function (g) { return (d.commands[g[0]] || []).length; });
    var tabs = $('#cmd-tabs');
    function pick(id) {
      tabs.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.id === id)); });
      var list = $('#cmd-list');
      list.textContent = '';
      (d.commands[id] || []).forEach(function (c) {
        var left;
        if (id === 'keys') left = el('div', {}, c.keys.map(function (k) { return el('kbd', { text: k }); }));
        else if (id === 'menu') left = el('div', {}, [el('code', { text: c.label }), el('span', { cls: 'grp', text: c.group === 'main' ? 'main' : 'more' })]);
        else left = el('div', {}, [el('code', { text: c.say || c.cmd })]);
        list.appendChild(el('div', { cls: 'cmd' }, [left, el('span', { text: c.does })]));
      });
    }
    groups.forEach(function (g) {
      var b = el('button', { type: 'button', role: 'tab', text: g[1] });
      b.dataset.id = g[0];
      b.addEventListener('click', function () { pick(g[0]); });
      tabs.appendChild(b);
    });
    if (groups.length) pick(groups[0][0]);

    d.screenshots.filter(function (s) { return !s.hidden; }).forEach(function (s) {
      $('#shots').appendChild(el('figure', { cls: 'shot' }, [
        el('a', { href: 'screenshots/' + s.file }, [el('img', { src: 'screenshots/' + s.file, alt: s.caption, loading: 'lazy', width: '1600', height: '1000' })]),
        el('figcaption', {}, [el('b', { text: s.title }), s.caption]),
      ]));
    });
    // feature images point at screenshots/... relative to docs/
    d.links.forEach(function (l) {
      $('#links').appendChild(el('a', { href: gh(repo, l.href), text: l.label }));
    });
    if (d.meta.updated) $('#updated').textContent = 'Updated ' + d.meta.updated + '.';
  }
})();
