/* Picture timing for the timeline editor (cr_timing.py). Loaded into the engine's own /editor/<slug> page after its
   script. It changes nothing of the editor: every timing change is the editor's own `retime` op, sent through the
   editor's own op(), so it saves, previews, undoes and renders exactly like dragging a shot's edge there.
   - Timing panel on the selected picture: click the word it should appear on (or end on), nudge 0.1 s, put the cut
     at the playhead, and Hear the picture and voice across the cut.
   - Dragging an edge snaps to the nearest spoken word (Alt drags freely) and plays the word it lands on.
   - The narration's words along the voice track; [ and ] at the playhead, Alt+arrows nudge the start (Shift: end). */
'use strict';
(() => {
  // The editor's own state and calls (top-level bindings of its page script). A future editor without them: no add-on.
  const ready = () => {
    try {
      return typeof op === 'function' && typeof seek === 'function' && typeof tAt === 'function' && typeof X === 'function'
        && typeof fmt === 'function' && typeof toast === 'function' && typeof PPS === 'number' && typeof NOW === 'number'
        && typeof V === 'object'
        && typeof D !== 'undefined' && typeof SEL !== 'undefined';
    } catch (_) {return false;}
  };
  const insp = document.getElementById('insp'), laneV = document.getElementById('laneV'), laneA = document.getElementById('laneA');
  const scroller = document.getElementById('scroll'), content = document.getElementById('content');
  if (!ready() || !insp || !laneV || !laneA || !scroller || !content) return;
  const slug = typeof SLUG === 'string' ? SLUG : decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
  const MIN = 0.8;            // the editor's own shortest shot (timeline.MIN_SLOT)
  const STEP = 0.1, WINDOW = 3.2, SNAP_PX = 14, SAVE_AFTER = 650;
  const T = {words: [], source: '', pending: null, timer: 0, hearSoon: 0, voice: null, stopVoice: 0, stopAt: null};
  const mk = (tag, cls, txt) => {const n = document.createElement(tag); if (cls) n.className = cls; if (txt != null) n.textContent = txt; return n;};
  const button = (label, title, handler, disabled) => {
    const b = mk('button', 'btn sm', label); b.type = 'button'; if (title) b.title = title; b.disabled = !!disabled;
    b.addEventListener('click', handler); return b;
  };

  // ── the selected picture, and where its edges may go (the editor's rule: no shot shorter than 0.8 s) ──────────────
  function selected() {
    if (!D || !SEL || SEL.kind !== 'slots' || !Array.isArray(SEL.ids) || SEL.ids.length !== 1) return null;
    const i = SEL.ids[0]; return D.items && D.items[i] ? i : null;
  }
  function bounds(i, edge) {
    const it = D.items, x = it[i];
    if (edge === 'start') return i > 0 ? [it[i - 1].t + MIN, x.t + x.dur - MIN] : null;
    return i + 1 < it.length ? [x.t + MIN, it[i + 1].t + it[i + 1].dur - MIN] : null;
  }
  const edgeAt = (i, edge) => edge === 'start' ? D.items[i].t : D.items[i].t + D.items[i].dur;
  const shown = (i, edge) => T.pending && T.pending.i === i && T.pending.edge === edge ? T.pending.t : edgeAt(i, edge);
  function nearestWord(t, within) {
    let best = null;
    for (const w of T.words) {const d = Math.abs(w.t0 - t); if (d <= within && (!best || d < Math.abs(best.t0 - t))) best = w; if (w.t0 > t + within) break;}
    return best;
  }

  // ── changing a cut: the editor's own retime op, exactly as its edge drag sends it ─────────────────────────────────
  async function moveEdge(i, edge, t) {
    const x = D.items[i], b = x && bounds(i, edge);
    if (!b) return;
    t = Math.round(Math.max(b[0], Math.min(b[1], t)) * 1000) / 1000;
    if (Math.abs(t - edgeAt(i, edge)) < 0.01) return;
    await op(edge === 'start' ? {op: 'retime', slot: i, start: t, end: x.t + x.dur} : {op: 'retime', slot: i, start: x.t, end: t});
  }
  function nudge(i, edge, delta) {
    const b = bounds(i, edge); if (!b) return;
    if (T.pending && (T.pending.i !== i || T.pending.edge !== edge)) commit();
    const t = Math.max(b[0], Math.min(b[1], shown(i, edge) + delta));
    T.pending = {i, edge, t}; seek(t); mount(true);
    clearTimeout(T.timer); T.timer = setTimeout(commit, SAVE_AFTER);    // a run of nudges is saved as one change
    clearTimeout(T.hearSoon); T.hearSoon = setTimeout(() => hearWord(t, 0.7), 250);   // and you hear where it lands
  }
  function commit() {
    clearTimeout(T.timer); const p = T.pending; T.pending = null;
    if (p) return moveEdge(p.i, p.edge, p.t).finally(() => mount(true));
  }
  function atPlayhead(i, edge, free) {
    const b = bounds(i, edge); if (!b) return;
    if (NOW < b[0] - 0.05 || NOW > b[1] + 0.05) {
      toast(`Move the playhead between ${fmt(b[0], 2)} and ${fmt(b[1], 2)}: every shot keeps at least ${MIN} s.`, true);
      return;
    }
    const w = free ? null : nearestWord(NOW, 0.2);          // a pause just after the word still lands on it
    if (T.pending) commit();
    return moveEdge(i, edge, w && w.t0 >= b[0] && w.t0 <= b[1] ? w.t0 : NOW);
  }

  // ── hearing it ───────────────────────────────────────────────────────────────────────────────────────────────────
  function voice() {
    if (!T.voice) {T.voice = new Audio(`/f/audio/${encodeURIComponent(slug)}`); T.voice.preload = 'auto';}
    return T.voice;
  }
  function hearWord(t, seconds) {                            // the narration alone, from t (while dragging)
    const a = voice(); clearTimeout(T.stopVoice); if (!V.paused) V.pause();
    try {a.currentTime = Math.max(0, t);} catch (_) {}
    a.play().catch(() => {}); T.stopVoice = setTimeout(() => a.pause(), Math.round(seconds * 1000));
  }
  function hearCut(t) {                                      // the editor's player: picture and voice across the cut
    if (T.voice) T.voice.pause();
    seek(Math.max(0, t - 1.6)); T.stopAt = t + 1.4; V.play().catch(() => {});
  }
  V.addEventListener('timeupdate', () => {if (T.stopAt != null && V.currentTime >= T.stopAt) {T.stopAt = null; V.pause();}});
  V.addEventListener('pause', () => {T.stopAt = null;});

  // ── the Timing panel under the selected picture's buttons ─────────────────────────────────────────────────────────
  function edgeRow(i, edge) {
    const b = bounds(i, edge), at = shown(i, edge), pending = T.pending && T.pending.i === i && T.pending.edge === edge;
    const row = mk('div', 'crt-edge'), top = mk('div', 'crt-edge-top');
    const name = edge === 'start' ? 'Appears' : 'Ends';
    if (!b) {
      row.append(mk('div', 'crt-note', edge === 'start' ? 'Appears when the video starts.' : 'Ends when the video ends.'));
      return row;
    }
    const word = nearestWord(at, 0.12);
    const label = mk('b', pending ? 'is-pending' : '', `${name} at ${fmt(at, 2)}${word ? ` · on “${word.w}”` : ''}${pending ? ' · saving…' : ''}`);
    top.append(label,
      button(`−${STEP} s`, `${name} ${STEP} s earlier${edge === 'start' ? ' (Alt+←)' : ' (Alt+Shift+←)'}`, () => nudge(i, edge, -STEP), at - STEP < b[0] - 1e-6),
      button(`+${STEP} s`, `${name} ${STEP} s later${edge === 'start' ? ' (Alt+→)' : ' (Alt+Shift+→)'}`, () => nudge(i, edge, STEP), at + STEP > b[1] + 1e-6),
      button('At playhead', `${name} where the playhead is now (${edge === 'start' ? '[' : ']'}), on the word spoken there`, () => atPlayhead(i, edge)),
      button('▶ Hear', 'Play the picture and voice across this cut', () => hearCut(at)));
    row.append(top);
    const near = T.words.filter(w => w.t0 >= Math.max(b[0], at - WINDOW) - 1e-6 && w.t0 <= Math.min(b[1], at + WINDOW) + 1e-6);
    if (near.length) {
      const chips = mk('div', 'crt-words-row'), on = word;
      chips.setAttribute('aria-label', `${name} on a word`);
      for (const w of near.slice(0, 18)) {
        const chip = mk('button', 'crt-chip' + (w === on ? ' is-now' : ''), w.w); chip.type = 'button';
        chip.title = `${edge === 'start' ? 'Show this picture from' : 'End this picture on'} “${w.w}” (${fmt(w.t0, 2)})`;
        chip.addEventListener('click', () => {if (T.pending) commit(); hearWord(w.t0, Math.min(0.9, Math.max(0.4, w.t1 - w.t0 + 0.2))); moveEdge(i, edge, w.t0);});
        chip.addEventListener('mouseenter', () => {if (V.paused) seek(w.t0);});
        chips.append(chip);
      }
      row.append(chips);
    }
    return row;
  }
  function panel(i) {
    const box = mk('section', 'crt-panel'); box.dataset.slot = String(i);
    const head = mk('div', 'crt-head'); head.append(mk('span', '', 'Timing'), mk('span', 'crt-head-hint', 'Click the word this picture should appear on'));
    box.append(head, edgeRow(i, 'start'), edgeRow(i, 'end'));
    if (!T.words.length) box.append(mk('div', 'crt-note', T.source ? 'This video has no word timings: use the nudges, or play it and press [ where the picture should start.' : 'Loading the narration’s words…'));
    box.append(mk('div', 'crt-keys', 'Keys: [ starts this picture at the playhead, ] ends it there · Alt+←/→ moves its start 0.1 s, Alt+Shift+←/→ its end · Drag a shot’s edge: it snaps to the words (hold Alt to drag freely). Only the shots you change render again.'));
    return box;
  }
  function mount(force) {
    const i = selected(), old = insp.querySelector('.crt-panel');
    if (T.pending && T.pending.i !== i) commit();
    if (i == null) {
      if (old) old.remove();
      const empty = insp.querySelector('.empty');
      if (empty && !empty.querySelector('.crt-tip')) empty.append(mk('span', 'crt-tip', 'A picture at the wrong moment? Click it, then click the word it should appear on.'));
      return;
    }
    const x = D.items[i], sig = JSON.stringify([i, x.t, x.dur, T.pending, T.words.length]);
    if (old && !force && old.dataset.sig === sig) return;
    const fresh = panel(i); fresh.dataset.sig = sig;
    if (old) old.replaceWith(fresh);
    else {const acts = insp.querySelector('.acts'); if (acts) acts.after(fresh); else insp.append(fresh);}
  }
  // the editor redraws its inspector on every refresh: the panel goes back each time
  new MutationObserver(() => {if (!insp.querySelector('.crt-panel') || selected() == null) mount();}).observe(insp, {childList: true});

  // ── dragging an edge: the editor's drag, snapped to the spoken words and heard ───────────────────────────────────
  document.addEventListener('mousedown', e => {
    const handle = e.button === 0 && e.target.closest ? e.target.closest('#laneV .clip .eh') : null;
    if (!handle || !D) return;
    const i = +handle.closest('.clip').dataset.i, x = D.items[i]; if (!x) return;
    const edge = handle.classList.contains('r') ? 'end' : 'start', b = bounds(i, edge);
    e.preventDefault(); e.stopPropagation();                // this drag instead of the editor's: the same op, snapped
    if (!b) return;
    if (T.pending) commit();
    const was = edgeAt(i, edge), ghost = mk('div', 'ghost crt-ghost'); content.append(ghost);
    let at = was, heard = null, wait = 0;
    const move = ev => {
      let t = Math.max(b[0], Math.min(b[1], tAt(ev))), word = null;
      if (!ev.altKey && T.words.length) {const w = nearestWord(t, SNAP_PX / PPS); if (w && w.t0 >= b[0] && w.t0 <= b[1]) {t = w.t0; word = w;}}
      at = t; ghost.style.left = `${X(at)}px`;
      const d = at - was;
      ghost.replaceChildren(mk('span', '', `${fmt(at, 2)} (${d >= 0 ? '+' : ''}${d.toFixed(2)} s)${word ? ` · “${word.w}”` : ''}`));
      seek(at);
      const target = word ? word.t0 : at;
      if (heard == null || Math.abs(target - heard) > 0.05) {
        clearTimeout(wait);
        wait = setTimeout(() => {heard = target; hearWord(target, word ? Math.min(0.9, Math.max(0.4, word.t1 - word.t0 + 0.2)) : 0.6);}, word ? 90 : 220);
      }
    };
    const up = () => {
      window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); clearTimeout(wait); ghost.remove();
      if (Math.abs(at - was) >= 0.05) moveEdge(i, edge, at);
    };
    move(e); window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  }, true);

  // ── keys (only with one picture selected and no text field in use) ───────────────────────────────────────────────
  window.addEventListener('keydown', e => {
    const tag = (document.activeElement && document.activeElement.tagName) || '', pool = document.getElementById('pool');
    if (/INPUT|TEXTAREA|SELECT/.test(tag) || (pool && !pool.classList.contains('hidden')) || e.metaKey || e.ctrlKey) return;
    const i = selected(); if (i == null) return;
    if (e.key === '[' || e.key === ']') atPlayhead(i, e.key === '[' ? 'start' : 'end', e.altKey);
    else if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) nudge(i, e.shiftKey ? 'end' : 'start', e.key === 'ArrowLeft' ? -STEP : STEP);
    else return;
    e.preventDefault(); e.stopImmediatePropagation();
  }, true);

  // ── the narration's words along the voice track ──────────────────────────────────────────────────────────────────
  const layer = mk('div', 'crt-words'); layer.setAttribute('aria-hidden', 'true'); laneA.append(layer);
  let frame = 0;
  function drawWords() {
    frame = 0;
    if (!T.words.length || PPS < 12) {layer.replaceChildren(); return;}          // too far out: the ticks would merge
    const from = scroller.scrollLeft / PPS - 2, to = (scroller.scrollLeft + scroller.clientWidth) / PPS + 2;
    layer.replaceChildren(...T.words.filter(w => w.t1 >= from && w.t0 <= to).map(w => {
      const width = X(w.t1 - w.t0), n = mk('span', 'crt-word', width >= 24 ? w.w : '');      // a tick where a word has no room
      n.style.left = `${X(w.t0)}px`; n.style.width = `${Math.max(2, width)}px`; return n;
    }));
  }
  const redraw = () => {if (!frame) frame = requestAnimationFrame(drawWords);};
  scroller.addEventListener('scroll', redraw, {passive: true});
  new MutationObserver(redraw).observe(content, {attributes: true, attributeFilter: ['style']});   // zoom changes its width
  window.addEventListener('resize', redraw);

  fetch(`/cr-editor/words/${encodeURIComponent(slug)}`, {cache: 'no-store'})
    .then(r => r.ok ? r.json() : {words: [], source: 'none'})
    .catch(() => ({words: [], source: 'none'}))
    .then(d => {
      T.source = d.source || 'none';
      T.words = (Array.isArray(d.words) ? d.words : []).filter(w => Array.isArray(w) && w.length >= 3)
        .map(w => ({w: String(w[0]), t0: Number(w[1]), t1: Number(w[2])})).filter(w => isFinite(w.t0) && isFinite(w.t1))
        .sort((a, b) => a.t0 - b.t0);
      mount(true); redraw();
    });
  mount(true);
  window.CR_TIMING = {state: T, moveEdge, nudge, commit, selected, bounds};            // for support and tests
})();
