/* The review player (#review): watch the finished Reel big, stop anywhere and leave notes on the frame, then
   "Apply N notes".
   - A note is said (hold to talk: MediaRecorder keeps the audio, the browser's speech recognition drafts the words
     live where it has one, and Whisper on the server writes them out: POST api/review/transcribe, which serve.py
     answers for the preview), typed, or drawn on the frame (a point, a circle, an arrow or a freehand mark).
   - Likes count as much as changes ("keep" notes, or L / the heart for one tap while it plays).
   - Every note follows the hosted contract (creators/web/REVIEW.md): {id, t, kind, text, audio, drawing_png,
     frame_png, marks}; buildPayload() assembles exactly that.
   - With a real edit (hosted.js: window.CRHosted.on and a done job), the review plays that job's Reel, and
     CRHosted.applyNotes(payload, {onProgress}) returns the next job, whose result.changes give each note's
     before/after. Without one, applying is a stub: the after is the demo edit's real second cut, with what really
     changed at each note (media/local/review.json). Nothing is uploaded in the demo. */
(function () {
  "use strict";
  var A = window.CRApp;
  if (!A) return;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var ICON = A.ICON, esc = A.esc, fmt = A.fmt;
  function fmt1(t) { t = Math.max(0, t); return Math.floor(t / 60) + ":" + (t % 60).toFixed(1).padStart(4, "0"); }
  function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function round(x, n) { var k = Math.pow(10, n || 3); return Math.round(x * k) / k; }

  var root = $('[data-screen="review"]');
  var v1 = $("#rv-v1"), v2 = $("#rv-v2"), frame = $("#rv-frame"), ink = $("#rv-ink"), surface = $("#rv-surface");
  var track = $("#rv-track"), strip = $("#rv-strip"), wash = $("#rv-wash"), head = $("#rv-head"), pins = $("#rv-pins"), spansEl = $("#rv-spans");
  var tip = $("#rv-tip"), tipC = $("#rv-tip-c"), tipT = $("#rv-tip-t"), card = $("#rv-card");
  var text = $("#rv-text"), rail = $("#rv-rail");
  var touch = matchMedia("(hover: none)").matches;
  var COLORS = { change: "#6A4DF4", like: "#12A66A" };

  // ---------- state ----------
  var KEY = "cr-creators-review";
  var R = { base: 1, notes: [], applied: false, ab: 2, reel: null, after: null };   // base: the cut being reviewed; ab: before (1) or after (2); reel: whose notes; after: a real edit's next version
  try { var saved = JSON.parse(sessionStorage.getItem(KEY) || "null"); if (saved && saved.notes) R = Object.assign(R, saved); } catch (e) {}
  function persist() { try { sessionStorage.setItem(KEY, JSON.stringify(R)); } catch (e) {} }
  var audioStore = {};          // note id -> the recorded Blob (kept in memory only)
  var data = null, dur = 51.47, loaded = null, entered = false;
  var CR = window.CRReview = { enter: enter, leave: leave, payload: null, files: null, state: function () { return R; } };

  // A real edit (hosted.js) or the demo's two cuts.
  function hosted() { var H = window.CRHosted; return H && H.on ? H : null; }
  function hostedReel() {
    var H = hosted(), j = H && H.job, r = j && j.status === "done" && j.result;
    return r ? { key: "job:" + j.id, id: j.id, video: r.video_url, poster: r.poster_url || "", seconds: r.seconds || 0 } : null;
  }
  function reelKey() { var h = hostedReel(); return h ? h.key : "demo"; }
  function cutOf(k) {
    if (k >= 2 && R.after) return R.after;
    var h = hostedReel();
    if (h) return { video: h.video, poster: h.poster };
    return A.cuts[k >= 2 ? 2 : 1];
  }
  function verLabel(after) {   // a real Reel says its own version (the home opens any version); the demo has two cuts
    var n = Math.max(1, A.S.version || 1);
    if (hostedReel()) return after ? "New cut · v" + n : n > 1 ? "Your Reel · v" + n : "First cut · v1";
    return after ? "Second cut · v2" : "First cut · v1";
  }
  function keepText() { return "Keep v" + (R.after ? Math.max(2, A.S.version || 2) : 2); }
  function vid() { return R.applied && R.ab === 1 ? v1 : R.applied ? v2 : v1; }   // the <video> on screen
  function showVid() { var v = vid(); v1.classList.toggle("on", v === v1); v2.classList.toggle("on", v === v2); }

  // ---------- frame grabbers: hidden videos that seek and draw (filmstrip, scrub tooltip, frames for notes) ----------
  var parking = document.createElement("div");
  parking.setAttribute("aria-hidden", "true");
  parking.style.cssText = "position:fixed;left:-8px;top:0;width:4px;height:4px;overflow:hidden;opacity:0;pointer-events:none";
  document.body.appendChild(parking);
  function Grabber(src) {
    var v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.preload = "auto"; v.crossOrigin = "anonymous"; v.src = src;
    parking.appendChild(v);
    var ready = new Promise(function (res) { if (v.readyState >= 2) res(); else v.addEventListener("loadeddata", res, { once: true }); });
    var chain = Promise.resolve(), want = null, busy = false;
    function seek(t) {
      return new Promise(function (res) {
        t = clamp(t, 0, Math.max(0, (v.duration || dur) - 0.04));
        if (Math.abs(v.currentTime - t) < 0.01 && v.readyState >= 2) return res();
        var done = false, fin = function () { if (!done) { done = true; res(); } };
        v.addEventListener("seeked", fin, { once: true });
        setTimeout(fin, 1500);               // never hang the queue on a lost event
        v.currentTime = t;
      });
    }
    function pump() {
      if (busy || !want) return;
      var w = want; want = null; busy = true;
      ready.then(function () { return seek(w.t); }).then(function () { w.draw(v); busy = false; pump(); });
    }
    return {
      video: v,
      grab: function (t, draw) { chain = chain.then(function () { return ready; }).then(function () { return seek(t); }).then(function () { return draw(v); }); return chain; },
      peek: function (t, draw) { want = { t: t, draw: draw }; pump(); },
      drop: function () { v.removeAttribute("src"); v.load(); v.remove(); }
    };
  }
  function cover(ctx, src, w, h) { // draw a 9:16 source into w x h, cropping like object-fit: cover
    var sw = src.videoWidth || src.width, sh = src.videoHeight || src.height;
    if (!sw || !sh) return;
    var s = Math.max(w / sw, h / sh), dw = sw * s, dh = sh * s;
    ctx.drawImage(src, (w - dw) / 2, (h - dh) / 2, dw, dh);
  }
  var G = { strip: null, tip: null, after: null };

  // ---------- load ----------
  function load() {
    var key = reelKey();
    if (loaded === key) return Promise.resolve();
    if (R.reel !== key) { resetRound(); R.reel = key; persist(); }   // notes belong to one Reel
    loaded = key;
    var h = hostedReel();
    if (h) { data = null; if (h.seconds) dur = h.seconds; setSources(); return Promise.resolve(); }
    return fetch("media/local/review.json").then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }).then(function (d) {
      data = d;
      if (d && d.duration) dur = d.duration;
      setSources();
    });
  }
  function setSources() {
    var a = cutOf(R.base), b = cutOf(2);
    if (v1.getAttribute("src") !== a.video) { v1.poster = a.poster; v1.src = a.video; }
    if (R.base < 2 && (R.after || (data && !hostedReel())) && v2.getAttribute("src") !== b.video) { v2.poster = b.poster; v2.src = b.video; v2.preload = R.applied ? "auto" : "metadata"; }
    if (G.strip) G.strip.drop();
    if (G.tip) G.tip.drop();
    G.strip = Grabber(a.video); G.tip = Grabber(a.video);
    stripFrames = {}; layoutStrip();
  }

  // ---------- enter / leave ----------
  function enter() {
    document.body.classList.add("rv-on");
    load().then(function () {
      if (!entered) { entered = true; showVid(); }
      if (!R.applied) $("#rv-ver").textContent = verLabel(false);
      renderAll();
      if (R.applied) enterAfter(false);
      requestAnimationFrame(function () { sizeInk(); layoutStrip(); drawHead(vid().currentTime || 0); });
    });
  }
  function leave() {
    document.body.classList.remove("rv-on");
    [v1, v2].forEach(function (v) { v.pause(); });
    if (rec) stopTalk(true);
    if (comp) closeComposer();
    closeRail(); closeSheet();
    if (micStream) { micStream.getTracks().forEach(function (t) { t.stop(); }); micStream = null; }
  }

  // ---------- playback ----------
  function playing() { return !vid().paused && !vid().ended; }
  function toggle() {
    if (comp || busy) return;
    var v = vid();
    if (v.paused || v.ended) { if (v.ended || v.currentTime >= dur - 0.05) v.currentTime = 0; A.play(v); } else v.pause();
  }
  function setPlayUI() {
    var p = playing();
    root.classList.toggle("playing", p);
    var b = $("#rv-pp"); b.innerHTML = ICON(p ? "i-pause" : "i-play"); b.setAttribute("aria-label", p ? "Pause" : "Play");
    sizeIcon(b, 18);
    $("#rv-bigplay").setAttribute("aria-label", p ? "Pause" : "Play");
    if (p) loop();
  }
  function sizeIcon(el, n) { var s = el.querySelector("svg"); if (s) { s.setAttribute("width", n); s.setAttribute("height", n); } }
  [v1, v2].forEach(function (v) {
    ["play", "pause", "playing"].forEach(function (ev) { v.addEventListener(ev, function () { if (v === vid()) setPlayUI(); }); });
    v.addEventListener("seeked", function () { if (v === vid()) drawHead(v.currentTime); });
    v.addEventListener("loadedmetadata", function () { if (v === vid() && v.duration) { dur = v.duration; drawHead(v.currentTime); renderPins(); } });
    v.addEventListener("ended", function () { if (v !== vid()) return; setPlayUI(); if (moment) return endMoment(); if (!R.applied) setTimeout(function () { if (current() && !comp) openSheet(); }, 350); });
  });
  function current() { return root.classList.contains("on"); }
  $("#rv-pp").addEventListener("click", toggle);
  $("#rv-bigplay").addEventListener("click", function (e) { e.stopPropagation(); toggle(); });
  $("#rv-snd").addEventListener("click", function () {
    var m = !v1.muted; v1.muted = v2.muted = m;
    var b = $("#rv-snd"); b.innerHTML = ICON(m ? "i-sound-off" : "i-sound-on"); sizeIcon(b, 18);
    b.setAttribute("aria-pressed", String(m)); b.setAttribute("aria-label", m ? "Unmute" : "Mute");
  });

  // one animation loop while playing: the playhead, notes surfacing as you pass them, a before/after moment's end
  var looping = false, lastT = 0;
  function loop() {
    if (looping) return;
    looping = true;
    (function step() {
      var v = vid(), t = v.currentTime;
      drawHead(t);
      if (!v.paused && t > lastT && t - lastT < 0.6 && !comp && (!moment || moment.phase === 1))
        R.notes.forEach(function (n) { if (n.t > lastT && n.t <= t) surfaceNote(n, false, !!moment); });   // during "Before", only your marks
      if (moment && t >= moment.b) nextMoment();
      lastT = t;
      if (!v.paused && !v.ended && current()) requestAnimationFrame(step); else looping = false;
    })();
  }
  function drawHead(t) {
    var p = clamp(t / dur, 0, 1) * 100;
    head.style.left = p + "%"; wash.style.left = p + "%";
    track.setAttribute("aria-valuenow", String(Math.round(t))); track.setAttribute("aria-valuemax", String(Math.round(dur))); track.setAttribute("aria-valuetext", fmt(t));
    $("#rv-time").textContent = fmt(t) + " / " + fmt(dur);
    var near = null;
    R.notes.forEach(function (n) { if (Math.abs(n.t - t) < 0.35) near = n.id; });
    $$(".rv-pin", pins).forEach(function (el) { el.classList.toggle("now", el.dataset.id === near); });
    $$("#rv-list .rv-note").forEach(function (el) { el.classList.toggle("now", el.dataset.id === near); });
  }
  function seek(t, keepPaused) {
    var v = vid();
    t = clamp(t, 0, dur - 0.03);
    v.currentTime = t; lastT = t; drawHead(t);
    if (keepPaused) v.pause();
  }

  // ---------- timeline: filmstrip, scrub tooltip, pins ----------
  var stripFrames = {}, stripTimer = null;
  function layoutStrip() {
    clearTimeout(stripTimer);
    stripTimer = setTimeout(function () {
      var r = track.getBoundingClientRect();
      if (!r.width || !G.strip) return;
      var n = clamp(Math.round(r.width / (r.height * 9 / 16)), 6, 40);
      if (strip.children.length === n) return;
      strip.innerHTML = "";
      for (var i = 0; i < n; i++) {
        var c = document.createElement("canvas"); c.width = 72; c.height = 128;
        var t = round((i + 0.5) * dur / n, 1);
        c.dataset.t = t; strip.appendChild(c);
        if (stripFrames[t]) c.getContext("2d").drawImage(stripFrames[t], 0, 0);
        else (function (c, t) {
          G.strip.grab(t, function (v) {
            var k = document.createElement("canvas"); k.width = 72; k.height = 128; cover(k.getContext("2d"), v, 72, 128);
            stripFrames[t] = k; if (c.isConnected) c.getContext("2d").drawImage(k, 0, 0);
          });
        })(c, t);
      }
    }, 60);
  }
  function trackT(x) { var r = track.getBoundingClientRect(); return clamp((x - r.left) / r.width, 0, 1) * dur; }
  function showTip(t) {
    var tr = track.getBoundingClientRect(), tl = $("#rv-tl").getBoundingClientRect();
    var x = tr.left - tl.left + (t / dur) * tr.width;
    tip.style.left = clamp(x, 49, tl.width - 49) + "px";
    tipT.textContent = fmt(t);
    tip.hidden = false;
    // the nearest strip frame at once, then the exact frame
    var ctx = tipC.getContext("2d"), cells = strip.children;
    if (cells.length) { var c = cells[clamp(Math.floor(t / dur * cells.length), 0, cells.length - 1)]; ctx.drawImage(c, 0, 0, tipC.width, tipC.height); }
    G.tip && G.tip.peek(t, function (v) { cover(ctx, v, tipC.width, tipC.height); });
  }
  var scrubbing = false, wasPlaying = false;
  track.addEventListener("pointerdown", function (e) {
    if (busy) return;
    scrubbing = true; wasPlaying = playing(); track.setPointerCapture(e.pointerId);
    if (comp) closeComposer();
    vid().pause();
    var t = trackT(e.clientX); seek(t); showTip(t);
  });
  track.addEventListener("pointermove", function (e) {
    var t = trackT(e.clientX);
    if (scrubbing) { seek(t); showTip(t); }
    else if (e.pointerType === "mouse") showTip(t);
  });
  function endScrub(e) {
    if (!scrubbing) return;
    scrubbing = false;
    if (e.pointerType !== "mouse") tip.hidden = true;
    if (wasPlaying) A.play(vid());
  }
  track.addEventListener("pointerup", endScrub);
  track.addEventListener("pointercancel", endScrub);
  track.addEventListener("pointerleave", function () { if (!scrubbing) tip.hidden = true; });
  track.addEventListener("keydown", function (e) {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); seek(vid().currentTime + (e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 5 : 1)); }
  });

  function renderPins() {
    pins.innerHTML = R.notes.map(function (n) {
      return '<button type="button" class="rv-pin ' + n.kind + (R.applied ? " done" : "") + '" data-id="' + n.id + '" style="left:' + (clamp(n.t / dur, 0, 1) * 100) + '%" aria-label="' +
        esc((n.kind === "like" ? "Keep" : "Change") + " at " + fmt(n.t) + ": " + (n.text || "voice or drawing")) + '"></button>';
    }).join("");
  }
  function note(id) { return R.notes.filter(function (n) { return n.id === id; })[0]; }
  function showCard(n, el) {
    var tl = $("#rv-tl").getBoundingClientRect(), r = el.getBoundingClientRect();
    card.innerHTML = '<img src="' + esc(n.thumb || "") + '" alt=""><div><div class="rv-nmeta"><span class="tchip">' + fmt(n.t) + "</span>" + badge(n) + "</div><p>" + esc(n.text || (n.audio ? "Voice note" : "Drawing")) + "</p></div>";
    card.style.left = clamp(r.left + r.width / 2 - tl.left, 118, tl.width - 118) + "px";
    card.style.bottom = "calc(100% - 2px)";
    card.hidden = false;
  }
  pins.addEventListener("pointerover", function (e) { var p = e.target.closest(".rv-pin"); if (p && e.pointerType === "mouse") showCard(note(p.dataset.id), p); });
  pins.addEventListener("pointerout", function (e) { if (e.target.closest(".rv-pin")) card.hidden = true; });
  pins.addEventListener("focusin", function (e) { var p = e.target.closest(".rv-pin"); if (p) showCard(note(p.dataset.id), p); });
  pins.addEventListener("focusout", function () { card.hidden = true; });
  pins.addEventListener("click", function (e) {
    var p = e.target.closest(".rv-pin"); if (!p) return;
    var n = note(p.dataset.id); if (!n) return;
    goToNote(n);
    if (touch) { showCard(n, p); setTimeout(function () { card.hidden = true; }, 2200); }
  });
  function goToNote(n) {
    if (comp) closeComposer();
    if (moment) moment = null;
    seek(n.t, true);
    surfaceNote(n, true);
  }

  // ---------- drawing on the frame ----------
  var paintTimer = null;
  function sizeInk() {
    var r = frame.getBoundingClientRect(), d = Math.min(2, window.devicePixelRatio || 1);
    if (!r.width) return;
    ink.width = Math.round(r.width * d); ink.height = Math.round(r.height * d);
    repaint();
  }
  function lineW(w) { return Math.max(3, w / 100); }
  function drawMark(ctx, m, w, h, color) {
    var lw = lineW(w), P = function (p) { return [p[0] * w, p[1] * h]; };
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    if (m.tool === "point") {
      var c = P(m.pts[0]);
      ctx.beginPath(); ctx.arc(c[0], c[1], lw * 5.2, 0, Math.PI * 2); ctx.fillStyle = hexA(color, 0.22); ctx.fill();
      ctx.beginPath(); ctx.arc(c[0], c[1], lw * 2.1, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
      ctx.lineWidth = lw * 0.9; ctx.strokeStyle = "#fff"; ctx.stroke();
      return;
    }
    function path() {
      ctx.beginPath();
      if (m.tool === "circle") {
        var a = P(m.pts[0]), b = P(m.pts[1]);
        ctx.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.max(lw, Math.abs(b[0] - a[0]) / 2), Math.max(lw, Math.abs(b[1] - a[1]) / 2), 0, 0, Math.PI * 2);
      } else if (m.tool === "arrow") {
        var s = P(m.pts[0]), e = P(m.pts[1]), ang = Math.atan2(e[1] - s[1], e[0] - s[0]), len = Math.hypot(e[0] - s[0], e[1] - s[1]), hl = Math.min(len * 0.45, Math.max(lw * 4.5, len * 0.22));
        ctx.moveTo(s[0], s[1]); ctx.lineTo(e[0], e[1]);
        ctx.moveTo(e[0] - hl * Math.cos(ang - 0.5), e[1] - hl * Math.sin(ang - 0.5)); ctx.lineTo(e[0], e[1]); ctx.lineTo(e[0] - hl * Math.cos(ang + 0.5), e[1] - hl * Math.sin(ang + 0.5));
      } else {
        var pts = m.pts.map(P);
        ctx.moveTo(pts[0][0], pts[0][1]);
        for (var i = 1; i < pts.length - 1; i++) ctx.quadraticCurveTo(pts[i][0], pts[i][1], (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2);
        ctx.lineTo(pts[pts.length - 1][0], pts[pts.length - 1][1]);
      }
    }
    path(); ctx.strokeStyle = "rgba(255,255,255,.92)"; ctx.lineWidth = lw * 1.9; ctx.stroke();   // a white halo so it reads on any footage
    path(); ctx.strokeStyle = color; ctx.lineWidth = lw; ctx.stroke();
  }
  function hexA(hex, a) { var n = parseInt(hex.slice(1), 16); return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")"; }
  function paintMarks(ctx, marks, w, h, color) { ctx.clearRect(0, 0, w, h); marks.forEach(function (m) { drawMark(ctx, m, w, h, color); }); }
  var shown = null;   // a saved note's marks surfacing on the frame
  function repaint() {
    var ctx = ink.getContext("2d");
    if (comp) paintMarks(ctx, comp.marks, ink.width, ink.height, COLORS[comp.kind]);
    else if (shown) paintMarks(ctx, shown.marks || [], ink.width, ink.height, COLORS[shown.kind]);
    else ctx.clearRect(0, 0, ink.width, ink.height);
  }
  function surfaceNote(n, stay, marksOnly) {
    shown = n; repaint();
    if (!marksOnly) surface.innerHTML = '<div class="rv-bub ' + n.kind + '"><i>' + ICON(n.kind === "like" ? "i-heart-fill" : "i-pen") + "</i><span>" + esc(n.text || (n.audio ? "Voice note" : "Your drawing")) + "</span></div>";
    clearTimeout(paintTimer);
    paintTimer = setTimeout(function () { if (shown === n && (playing() || !stay)) { if (marksOnly) { shown = null; repaint(); } else clearSurface(); } }, stay ? 4000 : 1800);
  }
  function clearSurface() { shown = null; surface.innerHTML = ""; repaint(); }
  function norm(e) { var r = ink.getBoundingClientRect(); return [round(clamp((e.clientX - r.left) / r.width, 0, 1), 4), round(clamp((e.clientY - r.top) / r.height, 0, 1), 4)]; }
  var tool = "point", drawing = null;
  ink.addEventListener("pointerdown", function (e) {
    if (!comp) return;
    e.preventDefault(); ink.setPointerCapture(e.pointerId);
    var p = norm(e);
    if (tool === "point") { comp.marks.push({ tool: "point", pts: [p] }); drawing = null; }
    else { drawing = { tool: tool, pts: [p, p.slice()] }; if (tool === "pen") drawing.pts = [p]; comp.marks.push(drawing); }
    afterMark();
  });
  ink.addEventListener("pointermove", function (e) {
    if (!drawing) return;
    var p = norm(e);
    if (drawing.tool === "pen") { var l = drawing.pts[drawing.pts.length - 1]; if (Math.hypot(p[0] - l[0], p[1] - l[1]) > 0.004) drawing.pts.push(p); }
    else drawing.pts[1] = p;
    repaint();
  });
  function endDraw() {
    if (!drawing) return;
    var m = drawing, a = m.pts[0], b = m.pts[m.pts.length - 1], small = Math.hypot(b[0] - a[0], (b[1] - a[1]) * 16 / 9) < 0.04;
    if (small && m.tool === "circle") m.pts = [[clamp(a[0] - 0.12, 0, 1), clamp(a[1] - 0.07, 0, 1)], [clamp(a[0] + 0.12, 0, 1), clamp(a[1] + 0.07, 0, 1)]];   // a click circles that spot
    if (small && m.tool === "arrow") m.pts = [[clamp(a[0] + (a[0] < 0.6 ? 0.22 : -0.22), 0, 1), clamp(a[1] + (a[1] < 0.75 ? 0.14 : -0.14), 0, 1)], a];       // a click points an arrow at it
    if (m.tool === "pen" && m.pts.length < 3) comp.marks.pop();
    drawing = null; afterMark();
  }
  ink.addEventListener("pointerup", endDraw);
  ink.addEventListener("pointercancel", endDraw);
  function afterMark() { $("#rv-undo").disabled = !comp || !comp.marks.length; repaint(); }
  $("#rv-undo").addEventListener("click", function () { if (comp && comp.marks.length) { comp.marks.pop(); afterMark(); } });
  var HINTS = { point: "Click the frame to point at it", circle: "Drag on the frame to circle it", arrow: "Drag on the frame to draw an arrow", pen: "Draw on the frame" };
  function setTool(t) {
    tool = t;
    $$(".rv-tools [data-tool]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.tool === t)); });
    $("#rv-hint").textContent = touch ? HINTS[t].replace("Click", "Tap") : HINTS[t];
  }
  $(".rv-tools").addEventListener("click", function (e) { var b = e.target.closest("[data-tool]"); if (b) setTool(b.dataset.tool); });

  // ---------- the composer ----------
  var comp = null, stamp = null;
  function setKind(k) {
    if (!comp) return;
    comp.kind = k;
    root.classList.toggle("like", k === "like");
    $$(".rv-kind [data-kind]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.kind === k)); b.setAttribute("aria-label", b.dataset.kind === "like" ? "Keep: something you love" : "Change: something to fix"); });
    text.placeholder = k === "like" ? "Say or type what you love here" : "Say or type what to change";
    repaint();
  }
  $(".rv-kind").addEventListener("click", function (e) { var b = e.target.closest("[data-kind]"); if (b) setKind(b.dataset.kind); });
  function openComposer(o) {
    o = o || {};
    if (busy || R.applied) return false;
    if (comp) { if (o.at) { comp.marks.push({ tool: "point", pts: [o.at] }); afterMark(); } return true; }
    var v = vid(); v.pause(); if (moment) moment = null;
    clearSurface();
    comp = { t: round(v.currentTime, 3), kind: o.kind || "change", marks: [], audio: null, src: "typed", draft: "" };
    comp.thumb = snap(v, 216, 384);
    root.classList.add("composing");
    $("#rv-ctl").hidden = true; $("#rv-comp").hidden = false;
    stamp = document.createElement("span"); stamp.className = "rv-stamp"; stamp.textContent = fmt1(comp.t); frame.appendChild(stamp);
    text.value = ""; autosize(); voiceUI();
    setKind(comp.kind); setTool(tool);
    if (o.at) comp.marks.push({ tool: "point", pts: [o.at] });
    requestAnimationFrame(function () { sizeInk(); afterMark(); });
    if (!o.talk && !touch) text.focus({ preventScroll: true });
    return true;
  }
  function closeComposer() {
    if (!comp) return;
    if (rec) stopTalk(true);
    comp = null;
    root.classList.remove("composing", "like");
    $("#rv-comp").hidden = true; $("#rv-ctl").hidden = false;
    if (stamp) { stamp.remove(); stamp = null; }
    text.blur(); repaint();
  }
  function snap(v, w, h) { var c = document.createElement("canvas"); c.width = w; c.height = h; try { cover(c.getContext("2d"), v, w, h); } catch (e) {} return c; }
  function thumbOf(c, marks, kind) {
    var t = document.createElement("canvas"); t.width = c.width; t.height = c.height;
    var ctx = t.getContext("2d"); ctx.drawImage(c, 0, 0);
    marks.forEach(function (m) { drawMark(ctx, m, t.width, t.height, COLORS[kind]); });
    try { return t.toDataURL("image/jpeg", 0.82); } catch (e) { return ""; }
  }
  function autosize() { text.style.height = "auto"; text.style.height = Math.min(108, Math.max(50, text.scrollHeight)) + "px"; }
  text.addEventListener("input", function () { autosize(); if (comp) comp.src = comp.audio && text.value === comp.draft ? comp.src : "typed"; });
  text.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); saveNote(); }
    if (e.key === "Escape") { e.preventDefault(); closeComposer(); }
  });
  $("#rv-comp").addEventListener("submit", function (e) { e.preventDefault(); saveNote(); });
  $("#rv-cancel").addEventListener("click", closeComposer);
  function saveNote() {
    if (!comp) return;
    if (rec) { pendingSave = true; stopTalk(); return; }
    var words = text.value.trim();
    if (!words && !comp.marks.length && !comp.audio) { text.placeholder = "Say, type or draw something first"; text.focus(); return; }
    var n = { id: "n" + Date.now().toString(36) + Math.floor(Math.random() * 1e3), t: comp.t, kind: comp.kind, text: words,
      text_source: words ? (comp.src === "typed" || words !== comp.draft ? "typed" : comp.src) : null,
      marks: comp.marks.map(function (m) { return { tool: m.tool, pts: m.pts.map(function (p) { return [round(p[0], 4), round(p[1], 4)]; }) }; }),
      audio: comp.audio ? { s: round(comp.audio.s, 1), type: comp.audio.blob.type } : null, created: new Date().toISOString() };
    n.thumb = thumbOf(comp.thumb, n.marks, n.kind);
    if (comp.audio) { audioStore[n.id] = comp.audio.blob; if (comp.whisper) comp.whisper.then(function (w) { var m = note(n.id); if (m && w && (!m.text || m.text === comp_draft_of(n))) { m.text = w; m.text_source = "whisper"; persist(); renderAll(); } }); }
    drafts[n.id] = words;
    R.notes.push(n); R.notes.sort(function (a, b) { return a.t - b.t; });
    persist();
    var resume = comp.t < dur - 0.15;
    closeComposer();
    renderAll(n.id);
    if (resume) A.play(vid());   // keep watching
  }
  var drafts = {};
  function comp_draft_of(n) { return drafts[n.id]; }

  // ---------- hold to talk ----------
  var micStream = null, rec = null, pendingSave = false, SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  function getMic() {
    if (micStream && micStream.active) return Promise.resolve(micStream);
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) return Promise.resolve(null);
    return navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }).then(function (s) { micStream = s; return s; }).catch(function () { return null; });
  }
  function talkUI(on) {
    $$(".rv-talk").forEach(function (b) { b.classList.toggle("rec", on); b.setAttribute("aria-label", on ? "Release to stop" : "Hold to talk"); });
    $$(".rv-talk .lbl").forEach(function (l) { l.textContent = on ? "Release to stop" : "Hold to talk"; });
    $("#rv-listen").hidden = !on;
    if (on) $("#rv-listen-t").innerHTML = "<em>Listening…</em>";
  }
  function startTalk(at) {
    if (rec || busy) return;
    if (!openComposer({ talk: true, at: at })) return;
    var r = rec = { t0: performance.now(), chunks: [], draft: "", stop: false };
    talkUI(true);
    getMic().then(function (stream) {
      if (rec !== r) return;
      if (!stream) { rec = null; pendingSave = false; talkUI(false); A.toast("The microphone is off for this page. Type your note instead."); text.focus(); return; }
      var type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].filter(function (t) { return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t); })[0];
      try { r.mr = new MediaRecorder(stream, type ? { mimeType: type, audioBitsPerSecond: 128000 } : { audioBitsPerSecond: 128000 }); } catch (e) { r.mr = new MediaRecorder(stream); }   // Whisper hears consonants better at 128k
      r.mr.ondataavailable = function (e) { if (e.data && e.data.size) r.chunks.push(e.data); };
      r.mr.start(250);
      meter(r, stream);
      if (SR) try {   // a live draft where the browser can; Whisper writes the final words
        var s = r.sr = new SR(); s.lang = navigator.language || "en-US"; s.interimResults = true; s.continuous = true;
        s.onresult = function (e) { var out = ""; for (var i = 0; i < e.results.length; i++) out += e.results[i][0].transcript; r.draft = out.trim(); if (rec === r) $("#rv-listen-t").textContent = r.draft || "Listening…"; };
        s.onerror = function () {}; s.start();
      } catch (e) { r.sr = null; }
      if (r.stop) stopTalk();
    });
  }
  function meter(r, stream) {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext; if (!Ctx) return;
      var ac = r.ac = new Ctx(), an = ac.createAnalyser(); an.fftSize = 512;
      ac.createMediaStreamSource(stream).connect(an);
      var buf = new Uint8Array(an.fftSize), bars = $$(".rv-talk.rec .rv-lvl i");
      (function tick() {
        if (rec !== r) return;
        an.getByteTimeDomainData(buf);
        var s = 0; for (var i = 0; i < buf.length; i++) { var x = (buf[i] - 128) / 128; s += x * x; }
        var l = clamp(Math.sqrt(s / buf.length) * 4, 0, 1);
        bars.forEach(function (b, k) { b.style.setProperty("--l", (l * [0.7, 1, 0.85, 0.6][k]).toFixed(3)); });
        requestAnimationFrame(tick);
      })();
    } catch (e) {}
  }
  function stopTalk(discard) {
    var r = rec; if (!r) return;
    if (!r.mr && !discard) { r.stop = true; return; }   // still waiting for the microphone
    rec = null; talkUI(false);
    var held = performance.now() - r.t0;
    if (r.sr) try { r.sr.stop(); } catch (e) {}
    if (r.ac) try { r.ac.close(); } catch (e) {}
    if (!r.mr) return;
    r.mr.onstop = function () {
      if (discard || !comp) { pendingSave = false; return; }
      var blob = new Blob(r.chunks, { type: (r.mr.mimeType || "audio/webm").split(";")[0] });
      if (held < 400 || !blob.size) { pendingSave = false; if (!comp.audio) { $("#rv-voice").hidden = false; $("#rv-vlen").textContent = ""; } voiceState("Hold the mic while you talk"); return; }
      if (comp.audio) URL.revokeObjectURL(comp.audio.url);
      comp.audio = { blob: blob, url: URL.createObjectURL(blob), s: held / 1000 };
      if (r.draft && (!text.value.trim() || text.value === comp.draft)) { text.value = r.draft; comp.draft = r.draft; comp.src = "speech-draft"; autosize(); }
      voiceUI(r.draft ? "Draft from your browser · writing it out…" : "Writing it out…");
      var c = comp;
      c.whisper = transcribe(blob).then(function (w) {
        if (comp === c) {
          if (w && (!text.value.trim() || text.value === c.draft)) { text.value = w; c.draft = w; c.src = "whisper"; autosize(); }
          voiceState(w ? "Written out by Whisper" : (text.value.trim() ? "" : "Voice note: we'll write it out when you apply"));
        }
        return w;
      });
      if (pendingSave) { pendingSave = false; c.whisper.then(function () { if (comp === c) saveNote(); }); }
    };
    try { r.mr.stop(); } catch (e) {}
  }
  function transcribe(blob) {   // Whisper on the server (serve.py for the preview; the hosted API in production)
    return fetch("api/review/transcribe", { method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob })
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { var w = j && j.text ? String(j.text).trim() : ""; return w.charAt(0).toUpperCase() + w.slice(1); }).catch(function () { return ""; });
  }
  function voiceUI(state) {
    var a = comp && comp.audio;
    $("#rv-voice").hidden = !a;
    if (a) { $("#rv-vlen").textContent = fmt1(a.s); voiceState(state || ""); }
  }
  function voiceState(s) { $("#rv-vstate").textContent = s; }
  var player = null;
  function playBlob(url, btn) {
    if (player) { player.pause(); if (player._btn) player._btn.innerHTML = ICON("i-play"); }
    if (player && player._url === url) { player = null; return; }
    player = new Audio(url); player._url = url; player._btn = btn;
    btn.innerHTML = ICON("i-pause");
    player.onended = function () { btn.innerHTML = ICON("i-play"); player = null; };
    A.play(player);
  }
  $("#rv-vplay").addEventListener("click", function () { if (comp && comp.audio) playBlob(comp.audio.url, $("#rv-vplay")); });
  $("#rv-vrm").addEventListener("click", function () { if (!comp || !comp.audio) return; URL.revokeObjectURL(comp.audio.url); comp.audio = null; comp.whisper = null; voiceUI(); });

  // The release is heard on the whole page: the dock's mic hides under the composer the moment it opens.
  function untilRelease(isEnd) {
    function end(e) { if (!isEnd(e)) return; ["pointerup", "pointercancel", "keyup", "blur"].forEach(function (ev) { window.removeEventListener(ev, end, true); }); stopTalk(); }
    ["pointerup", "pointercancel", "keyup", "blur"].forEach(function (ev) { window.addEventListener(ev, end, true); });
  }
  function bindHold(btn) {
    btn.addEventListener("pointerdown", function (e) {
      if (e.button > 0) return;
      e.preventDefault(); startTalk();
      untilRelease(function (ev) { return ev.type === "blur" || (ev.type !== "keyup" && ev.pointerId === e.pointerId); });
    });
    btn.addEventListener("keydown", function (e) {
      if ((e.key !== " " && e.key !== "Enter") || e.repeat) return;
      e.preventDefault(); e.stopPropagation(); startTalk();
      untilRelease(function (ev) { return ev.type === "blur" || (ev.type === "keyup" && ev.key === e.key); });
    });
    btn.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  }
  bindHold($("#rv-talk")); bindHold($("#rv-talk2"));

  // hold on the video to point at that spot and talk; a tap plays or pauses
  var hold = null;
  frame.addEventListener("pointerdown", function (e) {
    if (comp || busy || e.button > 0 || e.target.closest("button")) return;
    if (R.applied) { hold = { x: e.clientX, y: e.clientY, held: false, timer: 0 }; return; }   // a tap still plays or pauses
    var at = norm(e), id = e.pointerId;
    hold = { x: e.clientX, y: e.clientY, held: false, timer: setTimeout(function () { if (!hold) return; hold.held = true; try { frame.setPointerCapture(id); } catch (x) {} startTalk(at); }, 330) };
  });
  frame.addEventListener("pointermove", function (e) { if (hold && !hold.held && Math.hypot(e.clientX - hold.x, e.clientY - hold.y) > 12) { clearTimeout(hold.timer); hold = null; } });
  function endHold() { if (!hold) return; clearTimeout(hold.timer); var h = hold; hold = null; if (h.held) stopTalk(); else toggle(); }
  frame.addEventListener("pointerup", function (e) { if (!comp || (hold && hold.held)) endHold(); });
  frame.addEventListener("pointercancel", function () { if (hold) { clearTimeout(hold.timer); if (hold.held) stopTalk(); hold = null; } });
  frame.addEventListener("contextmenu", function (e) { e.preventDefault(); });

  // ---------- likes: one tap, keep watching ----------
  function love() {
    if (busy || R.applied) return;
    var v = vid(), t = round(v.currentTime, 3);
    if (comp) { setKind("like"); return; }
    var n = { id: "n" + Date.now().toString(36) + Math.floor(Math.random() * 1e3), t: t, kind: "like", text: "Love this", text_source: "button", marks: [], audio: null, created: new Date().toISOString() };
    n.thumb = thumbOf(snap(v, 216, 384), [], "like");
    R.notes.push(n); R.notes.sort(function (a, b) { return a.t - b.t; }); persist();
    var b = $("#rv-love"); b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop");
    surfaceNote(n);
    renderAll(n.id);
  }
  $("#rv-love").addEventListener("click", love);
  $("#rv-note").addEventListener("click", function () { openComposer({}); });

  // ---------- notes list ----------
  function badge(n) { return '<span class="rv-badge ' + n.kind + '">' + ICON(n.kind === "like" ? "i-heart-fill" : "i-pen") + (n.kind === "like" ? "Keep" : "Change") + "</span>"; }
  function noteHTML(n, editable) {
    var voice = n.audio && audioStore[n.id] ? '<button type="button" class="rv-nvoice" data-voice="' + n.id + '" aria-label="Play your voice note">' + ICON("i-play") + fmt1(n.audio.s) + "</button>" : n.audio ? '<span class="rv-nvoice">' + ICON("i-mic") + fmt1(n.audio.s) + "</span>" : "";
    var body = editable
      ? '<label class="sr-only" for="rv-e-' + n.id + '">Note at ' + fmt(n.t) + '</label><textarea id="rv-e-' + n.id + '" data-edit="' + n.id + '" rows="2" placeholder="' + (n.kind === "like" ? "What you love here" : "What to change") + '">' + esc(n.text || "") + "</textarea>"
      : '<p class="rv-ntext' + (n.text ? "" : " dim") + '">' + esc(n.text || (n.audio ? "Voice note: we'll write it out when you apply" : "A drawing on the frame")) + "</p>" +
        (n.reply && n.reply.what ? '<p class="rv-did' + (/^(changed|kept)$/.test(n.reply.status) ? "" : " none") + '">' + ICON(/^(changed|kept)$/.test(n.reply.status) ? "i-circle-check" : "i-note") + "<span>" + esc(n.reply.what) + "</span></p>" : "");
    return '<li class="rv-note" data-id="' + n.id + '"><button type="button" class="rv-nthumb" data-go="' + n.id + '" aria-label="Go to ' + fmt(n.t) + '"><img src="' + esc(n.thumb || "") + '" alt=""></button>' +
      '<div><div class="rv-nmeta"><button type="button" class="tchip" data-go="' + n.id + '">' + fmt(n.t) + "</button>" + badge(n) + voice + "</div>" + body + "</div>" +
      '<button type="button" class="rv-ndel" data-del="' + n.id + '" aria-label="Delete the note at ' + fmt(n.t) + '">' + ICON("i-x") + "</button></li>";
  }
  function tally() {
    var c = R.notes.filter(function (n) { return n.kind !== "like"; }).length, l = R.notes.length - c;
    return [c ? c + " change" + (c === 1 ? "" : "s") : "", l ? l + " keep" + (l === 1 ? "" : "s") : ""].filter(Boolean).join(" · ");
  }
  function renderAll(fresh) {
    var n = R.notes.length;
    $("#rv-count").textContent = n; $("#rv-count").classList.toggle("has", n > 0);
    $("#rv-finish").hidden = !n;
    $("#rv-finish").textContent = R.applied ? keepText() : "Finish review";
    if (!R.applied) {
      $("#rv-rail-h").textContent = "Notes";
      $("#rv-tally").textContent = tally();
      $("#rv-empty").hidden = n > 0;
      $("#rv-list").hidden = false; $("#rv-changes").hidden = true;
      $("#rv-list").innerHTML = R.notes.map(function (x) { return noteHTML(x, false); }).join("");
      var b = $("#rv-apply");
      b.disabled = !n; b.textContent = n ? "Apply " + n + " note" + (n === 1 ? "" : "s") : "Apply notes";
      b.dataset.mode = "apply";
      $("#rv-proto").textContent = hostedReel() ? "Your notes stay in this tab until you apply them." : R.base >= 2 ? "Prototype: our demo Reel has two real cuts, so a third round can't be shown yet." : "Prototype: the after is our real second cut of this Reel. Nothing is uploaded.";
    }
    renderPins();
    $$(".rv-ndel svg, .rv-nvoice svg").forEach(function (s) { s.setAttribute("width", "14"); s.setAttribute("height", "14"); });
    if (fresh) { var li = $('#rv-list [data-id="' + fresh + '"]'); if (li) { li.classList.add("fresh"); li.scrollIntoView({ block: "nearest" }); } }
  }
  function listClick(e) {
    var g = e.target.closest("[data-go]"), d = e.target.closest("[data-del]"), vb = e.target.closest("[data-voice]"), mo = e.target.closest("[data-moment]");
    if (vb && audioStore[vb.dataset.voice]) { var u = vb._url || (vb._url = URL.createObjectURL(audioStore[vb.dataset.voice])); playBlob(u, vb); return; }
    if (d) { R.notes = R.notes.filter(function (n) { return n.id !== d.dataset.del; }); delete audioStore[d.dataset.del]; persist(); renderAll(); if (sheet) drawSheet(); return; }
    if (mo) { playMoment(note(mo.dataset.moment)); if (narrow()) closeRail(); return; }
    if (g) { var n = note(g.dataset.go); if (n) { if (sheet) closeSheet(); goToNote(n); if (narrow()) closeRail(); } }
  }
  rail.addEventListener("click", listClick);

  // ---------- the rail as a sheet on smaller screens ----------
  var scrim = document.createElement("div"); scrim.className = "rv-scrim"; root.appendChild(scrim);
  function narrow() { return matchMedia("(max-width: 1099px)").matches; }
  function openRail() { if (!narrow()) return; rail.classList.add("open"); scrim.classList.add("open"); $("#rv-notes-btn").setAttribute("aria-expanded", "true"); var x = $("#rv-rail-x"); setTimeout(function () { x.focus(); }, 50); }
  function closeRail() { rail.classList.remove("open"); scrim.classList.remove("open"); $("#rv-notes-btn").setAttribute("aria-expanded", "false"); }
  $("#rv-notes-btn").addEventListener("click", function () { if (rail.classList.contains("open")) closeRail(); else openRail(); });
  $("#rv-rail-x").addEventListener("click", closeRail);
  scrim.addEventListener("click", closeRail);
  function keepAfter() {   // a real edit's next version becomes the Reel on the result screen
    if (R.after && hosted() && hosted().useJob) hosted().useJob(R.after.id);
    closeRail(); A.go("result");
  }
  $("#rv-finish").addEventListener("click", function () { if (R.applied) keepAfter(); else openSheet(); });
  function resetRound() {
    R = { base: 1, notes: [], applied: false, ab: 2, reel: R.reel, after: null }; persist(); audioStore = {}; afterFrames = {};
    if (G.after) { G.after.drop(); G.after = null; }
    root.classList.remove("applied"); $("#rv-ab").hidden = true; $("#rv-restart").hidden = true; spansEl.innerHTML = ""; $("#rv-changes").hidden = true;
    $("#rv-ver").textContent = verLabel(false); $("#rv-ver").classList.remove("v2");
    v2.pause(); showVid(); clearSurface(); closeRail();
  }
  $("#rv-restart").addEventListener("click", function () {   // the demo has two real cuts, so a new round starts from the first
    resetRound(); A.S.cut = 1; A.S.version = 1; A.save();
    setSources(); seek(0, true); renderAll();
  });
  $("#rv-apply").addEventListener("click", function () {
    var m = this.dataset.mode;
    if (m === "keep") { keepAfter(); return; }
    if (narrow()) closeRail();
    apply();
  });

  // ---------- the review sheet: every note, then "Apply N notes" ----------
  var sheet = null, sheetFocus = null;
  function openSheet() {
    if (sheet || R.applied || !current()) return;
    vid().pause(); if (comp) closeComposer(); closeRail();
    sheetFocus = document.activeElement;
    sheet = document.createElement("div"); sheet.className = "overlay";
    sheet.innerHTML = '<div class="modal rv-sheet" role="dialog" aria-modal="true" aria-labelledby="rv-sh-h"></div>';
    root.appendChild(sheet);
    drawSheet();
    requestAnimationFrame(function () { sheet && sheet.classList.add("open"); });
    sheet.addEventListener("click", function (e) {
      if (e.target === sheet || e.target.closest("[data-close]")) return closeSheet();
      if (e.target.closest("[data-again]")) { closeSheet(); seek(0); A.play(vid()); return; }
      if (e.target.closest("[data-apply]")) { closeSheet(); apply(); return; }
      var k = e.target.closest("[data-kind-of]");
      if (k) { var n = note(k.dataset.kindOf); n.kind = n.kind === "like" ? "change" : "like"; n.thumb = n.thumb; persist(); renderAll(); drawSheet(); return; }
      listClick(e);
    });
    sheet.addEventListener("input", function (e) { var t = e.target.closest("[data-edit]"); if (!t) return; var n = note(t.dataset.edit); n.text = t.value; n.text_source = "typed"; persist(); renderAll(); });
    var f = sheet.querySelector("[data-apply], [data-again]"); if (f) setTimeout(function () { f.focus(); }, 60);
  }
  function drawSheet() {
    var m = sheet.querySelector(".modal"), n = R.notes.length, cutName = R.base >= 2 ? "second cut" : "first cut";
    if (!n) {
      m.innerHTML = '<button type="button" class="modal-x" data-close aria-label="Close">' + ICON("i-x") + "</button>" +
        '<h2 id="rv-sh-h">No notes on your ' + cutName + "</h2><p class=\"sub\">Happy with it? It's ready to download. Or watch again and stop wherever something feels off.</p>" +
        '<div class="rv-sheet-f"><button type="button" class="btn btn-ghost" data-again>Watch again</button><a class="btn" href="#result">Back to my Reel</a></div>';
    } else {
      m.innerHTML = '<button type="button" class="modal-x" data-close aria-label="Close">' + ICON("i-x") + "</button>" +
        '<h2 id="rv-sh-h">' + n + " note" + (n === 1 ? "" : "s") + " on your " + cutName + '</h2><p class="sub">' + esc(tally()) + ". Fix any words, then we make the next version. Your " + cutName + " stays.</p>" +
        '<ol class="rv-list">' + R.notes.map(function (x) { return noteHTML(x, true).replace(badge(x), '<button type="button" class="rv-badge ' + x.kind + '" data-kind-of="' + x.id + '" aria-label="Switch between change and keep">' + ICON(x.kind === "like" ? "i-heart-fill" : "i-pen") + (x.kind === "like" ? "Keep" : "Change") + "</button>"); }).join("") + "</ol>" +
        '<div class="rv-sheet-f"><button type="button" class="btn btn-ghost" data-close>Keep watching</button><button type="button" class="btn" data-apply>Apply ' + n + " note" + (n === 1 ? "" : "s") + "</button></div>";
    }
    $$(".modal-x svg, .rv-ndel svg, .rv-badge svg, .rv-nvoice svg", m).forEach(function (s) { s.setAttribute("width", s.closest(".rv-badge") ? "12" : "16"); s.setAttribute("height", s.closest(".rv-badge") ? "12" : "16"); });
  }
  function closeSheet() {
    if (!sheet) return;
    var s = sheet; sheet = null;
    s.classList.remove("open"); setTimeout(function () { s.remove(); }, 200);
    if (sheetFocus && sheetFocus.focus && sheetFocus.isConnected) sheetFocus.focus({ preventScroll: true });
  }

  // ---------- the contract: what "Apply" sends to the hosted pipeline (creators/web/REVIEW.md) ----------
  function toBlob(c, type) { return new Promise(function (res) { try { c.toBlob(function (b) { res(b); }, type || "image/png"); } catch (e) { res(null); } }); }
  function buildPayload() {
    var src = cutOf(R.base), g = Grabber(src.video), files = {}, out = [], W = 0, H = 0;
    return R.notes.reduce(function (p, n) {
      return p.then(function () {
        return g.grab(n.t, function (v) {
          W = v.videoWidth; H = v.videoHeight;
          var f = document.createElement("canvas"); f.width = W; f.height = H; f.getContext("2d").drawImage(v, 0, 0, W, H);
          var d = null;
          if (n.marks.length) { d = document.createElement("canvas"); d.width = W; d.height = H; paintMarks(d.getContext("2d"), n.marks, W, H, COLORS[n.kind]); }
          return Promise.all([toBlob(f), d ? toBlob(d) : null]).then(function (b) {
            var base = "notes/" + n.id, au = audioStore[n.id], ext = au ? (/mp4|aac|m4a/.test(au.type) ? ".m4a" : ".webm") : "";
            files[base + "_frame.png"] = b[0];
            if (b[1]) files[base + "_drawing.png"] = b[1];
            if (au) files[base + ext] = au;
            out.push({ id: n.id, t: n.t, kind: n.kind, text: n.text || "", text_source: n.text_source || null,
              audio: au ? base + ext : null, audio_s: n.audio ? n.audio.s : null,
              drawing_png: b[1] ? base + "_drawing.png" : null, frame_png: base + "_frame.png", marks: n.marks, created: n.created });
          });
        });
      });
    }, Promise.resolve()).then(function () {
      g.drop();
      return { json: { version: 1, reel: { id: "demo-energy", cut: R.base, video: src.video, duration: round(dur, 2), width: W, height: H }, notes: out }, files: files };
    });
  }

  // ---------- apply (stub: the after is the demo's real second cut) ----------
  var busy = false;
  function changeFor(n) {   // the second cut's real change at this note (within 1.5 s), from the frame diff
    if (!data) return null;
    var best = null;
    data.changes.forEach(function (c) { var d = n.t < c.from ? c.from - n.t : n.t > c.to ? n.t - c.to : 0; if (d <= 1.5 && (!best || d < best.d)) best = { c: c, d: d }; });
    return best && best.c;
  }
  function apply() {
    if (busy || !R.notes.length) return;
    if (comp) closeComposer();
    vid().pause(); busy = true; root.classList.add("busy");
    var box = $("#rv-applying"), list = $("#rv-applying-list");
    $("#rv-applying-h").textContent = "Applying " + R.notes.length + " note" + (R.notes.length === 1 ? "" : "s");
    list.innerHTML = R.notes.map(function (n) { return '<li data-id="' + n.id + '"><i>' + ICON("i-check") + "</i><span>" + fmt(n.t) + " · " + esc(n.text || (n.audio ? "Voice note" : "Drawing")) + "<small></small></span></li>"; }).join("");
    box.hidden = false;
    buildPayload().then(function (p) {
      CR.files = p.files;
      CR.payload = { json: p.json, files: Object.keys(p.files).map(function (k) { return { name: k, type: p.files[k] ? p.files[k].type : null, size: p.files[k] ? p.files[k].size : 0 }; }) };
      var H = hosted(), rows = $$("li", list);
      if (H && H.applyNotes && hostedReel()) return applyReal(H, p, rows, box);
      var k = 0, stub = R.base < 2 && !!data && !hostedReel();
      (function next() {
        if (k > 0) { rows[k - 1].classList.remove("on"); rows[k - 1].classList.add("ok"); }
        if (k < rows.length) {
          var n = note(rows[k].dataset.id), c = stub && changeFor(n);
          rows[k].classList.add("on");
          rows[k].querySelector("small").textContent = !stub ? "Ready for the editor" : n.kind === "like" ? "Kept as it is" : c ? "Re-cutting " + fmt(c.from) + "–" + fmt(c.to) : "Noted for the editor";
          k++; return setTimeout(next, 520);
        }
        setTimeout(function () {
          box.hidden = true; busy = false; root.classList.remove("busy");
          if (!stub) { A.toast(hostedReel() ? "Your notes are saved here. Applying them to a real edit is switching on next." : "Your notes are ready for the editor. Our demo Reel has no third cut to show."); return; }
          R.applied = true; R.ab = 2; persist();
          A.S.cut = 2; A.S.version = Math.max(A.S.version, 1) + 1; A.save();
          enterAfter(true);
        }, 420);
      })();
    });
  }
  // A real edit: hosted.js sends the round and answers with the next job when it's done (REVIEW.md).
  function applyReal(H, p, rows, box) {
    rows.forEach(function (r) { r.classList.add("on"); r.querySelector("small").textContent = "With the editor"; });
    var done = function () { box.hidden = true; busy = false; root.classList.remove("busy"); };
    Promise.resolve(H.applyNotes(p, { formData: formData(p), onProgress: function (j) {
      var st = j && j.stages && j.stages.filter(function (x) { return x.state === "active"; })[0];
      if (st) $("#rv-applying-h").textContent = st.label + "…";
    } })).then(function (j) {
      var r = j && j.result;
      if (!r || !r.video_url) throw new Error("no result");
      if (r.rendered === false) {   // nothing to re-cut: the same Reel, and each note gets the editor's answer in place
        (r.changes || []).forEach(function (c) { var n = note(c.note); if (n) n.reply = { status: c.status, what: c.what }; });
        persist(); done(); renderAll();
        if (narrow()) openRail();
        A.toast("Nothing to re-cut this time. Each note has an answer.");
        return;
      }
      R.notes.forEach(function (n) { delete n.reply; });
      R.after = { video: r.video_url, poster: r.poster_url || "", id: j.id, changes: r.changes || [] };
      R.applied = true; R.ab = 2; persist();
      A.S.version = Math.max(A.S.version, 1) + 1; A.save();
      rows.forEach(function (row) { var c = serverChange(note(row.dataset.id)); row.classList.remove("on"); row.classList.add("ok"); row.querySelector("small").textContent = c ? c.what : "Done"; });
      setSources();
      setTimeout(function () { done(); enterAfter(true); }, 500);
    }).catch(function () { done(); A.toast("We couldn't apply your notes just now. They're all still here, so try again."); });
  }
  function formData(p) {   // the request body REVIEW.md describes: the notes JSON, then each file under its path
    var fd = new FormData();
    fd.append("notes", new Blob([JSON.stringify(p.json)], { type: "application/json" }), "notes.json");
    Object.keys(p.files).forEach(function (k) { if (p.files[k]) fd.append(k, p.files[k], k); });
    return fd;
  }
  CR.formData = formData;
  function serverChange(n) {   // {what, from, to, status} from a real round's report
    var c = R.after && R.after.changes && n ? R.after.changes.filter(function (x) { return x.note === n.id; })[0] : null;
    return c ? { what: c.what || "", from: c.span ? c.span[0] : n.t, to: c.span ? c.span[1] : n.t, status: c.status || "changed" } : null;
  }
  var afterFrames = {};
  function enterAfter(fresh) {
    root.classList.add("applied");
    $("#rv-ab").hidden = false; setAB(R.ab, fresh);
    $("#rv-ver").textContent = verLabel(true); $("#rv-ver").classList.add("v2");
    $("#rv-finish").hidden = false; $("#rv-finish").textContent = keepText();
    var real = !!R.after;
    spansEl.innerHTML = (real ? R.notes.map(serverChange).filter(function (c) { return c && c.status === "changed"; }) : data ? data.changes : []).map(function (c) { return '<i style="left:' + (c.from / dur * 100) + "%;width:" + ((c.to - c.from) / dur * 100) + '%"></i>'; }).join("");
    $("#rv-rail-h").textContent = "What changed";
    $("#rv-tally").textContent = R.notes.length + " note" + (R.notes.length === 1 ? "" : "s") + " applied";
    $("#rv-empty").hidden = true; $("#rv-list").hidden = true;
    var used = {}, ch = $("#rv-changes");
    ch.hidden = false;
    ch.innerHTML = R.notes.map(function (n) {
      var c = real ? serverChange(n) : n.kind === "like" ? null : changeFor(n); if (c && !real) used[c.from] = 1;
      var did = real ? (c ? c.what : "Noted.") : n.kind === "like" ? "Kept as it is." : c ? c.what + "." : "Our demo second cut didn't change this moment. With the hosted editor, this note becomes its own change.";
      if (real && c && c.status !== "changed" && c.status !== "kept") c = null;   // ask / not possible: shown plainly, without a tick
      return '<li class="rv-change" data-id="' + n.id + '"><div class="rv-wipe" data-wipe="' + n.id + '" role="slider" tabindex="0" aria-label="Before and after at ' + fmt(n.t) + '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="50">' +
        '<img class="b" src="' + esc(n.thumb || "") + '" alt=""><img class="a" alt="" data-after="' + n.id + '"><span class="h"></span><span class="lb">Before</span><span class="la">After</span></div>' +
        '<div><div class="rv-nmeta"><button type="button" class="tchip" data-go="' + n.id + '">' + fmt(n.t) + "</button>" + badge(n) + "</div>" +
        (n.text ? "<q>" + esc(n.text) + "</q>" : "") +
        '<p class="rv-did' + (c || n.kind === "like" ? "" : " none") + '">' + ICON(c || n.kind === "like" ? "i-circle-check" : "i-note") + "<span>" + esc(did) + "</span></p>" +
        '<button type="button" class="rv-moment" data-moment="' + n.id + '">' + ICON("i-play") + "Before, then after</button></div></li>";
    }).join("") + (function () {
      var also = (data && !real ? data.changes : []).filter(function (c) { return !used[c.from]; }).map(function (c) { return fmt(c.from) + " · " + c.what; })
        .concat((data && !real && data.everywhere || []).map(function (x) { return x.what; }));
      return also.length ? '<li class="rv-also"><b>Also new in this cut</b><ul>' + also.map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("") + "</ul></li>" : "";
    })();
    $$(".rv-did svg, .rv-moment svg, .rv-badge svg", ch).forEach(function (s) { if (!s.closest(".rv-badge")) { s.setAttribute("width", "15"); s.setAttribute("height", "15"); } });
    var b = $("#rv-apply"); b.disabled = false; b.textContent = keepText(); b.dataset.mode = "keep";
    $("#rv-restart").hidden = real;
    $("#rv-proto").textContent = real ? "Your first cut stays as v1." : "Prototype: the after is our real second cut of this Reel, made from notes like these. Nothing is uploaded.";
    renderPins();
    // the after frame at each note, from the second cut
    if (!G.after) G.after = Grabber(cutOf(2).video);
    R.notes.forEach(function (n) {
      var img = $('[data-after="' + n.id + '"]');
      if (afterFrames[n.id]) { img.src = afterFrames[n.id]; return; }
      G.after.grab(n.t, function (v) { var c = snap(v, 216, 384); try { afterFrames[n.id] = c.toDataURL("image/jpeg", 0.82); } catch (e) { return; } var i = $('[data-after="' + n.id + '"]'); if (i) i.src = afterFrames[n.id]; });
    });
    if (fresh) { A.toast("v2 is ready. Your first cut stays as v1."); if (narrow()) setTimeout(openRail, 400); }
  }
  function setAB(k, autoplay) {
    var from = vid(), t = from.currentTime, was = !from.paused;
    from.pause();
    R.ab = k; persist();
    var to = vid();
    if (to.readyState >= 1) to.currentTime = t; else to.addEventListener("loadedmetadata", function () { to.currentTime = t; }, { once: true });
    if (to.preload !== "auto") to.preload = "auto";
    showVid();
    $$("#rv-ab [data-ab]").forEach(function (b) { b.setAttribute("aria-pressed", String(+b.dataset.ab === k)); });
    if (was || autoplay) A.play(to);
    setPlayUI(); drawHead(t);
  }
  $("#rv-ab").addEventListener("click", function (e) { var b = e.target.closest("[data-ab]"); if (b && +b.dataset.ab !== R.ab) { moment = null; setAB(+b.dataset.ab); } });
  // drag a before/after wipe
  rail.addEventListener("pointerdown", function (e) {
    var w = e.target.closest("[data-wipe]"); if (!w) return;
    e.preventDefault(); w.setPointerCapture(e.pointerId);
    var mv = function (ev) { var r = w.getBoundingClientRect(), x = clamp((ev.clientX - r.left) / r.width, 0, 1) * 100; w.style.setProperty("--x", x + "%"); w.setAttribute("aria-valuenow", String(Math.round(x))); };
    mv(e);
    var up = function () { w.removeEventListener("pointermove", mv); w.removeEventListener("pointerup", up); w.removeEventListener("pointercancel", up); };
    w.addEventListener("pointermove", mv); w.addEventListener("pointerup", up); w.addEventListener("pointercancel", up);
  });
  rail.addEventListener("keydown", function (e) {
    var w = e.target.closest("[data-wipe]"); if (!w || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault(); e.stopPropagation();
    var x = clamp(+w.getAttribute("aria-valuenow") + (e.key === "ArrowRight" ? 10 : -10), 0, 100);
    w.style.setProperty("--x", x + "%"); w.setAttribute("aria-valuenow", String(x));
  });
  // "Before, then after": the moment around a note in the first cut, then in the second
  var moment = null;
  function playMoment(n) {
    if (!n || !R.applied) return;
    if (comp) closeComposer();
    var a = Math.max(0, n.t - 1.6), b = Math.min(dur - 0.05, n.t + 2.4);
    moment = { n: n, a: a, b: b, phase: 1 };
    setAB(1); seek(a); A.play(vid());
    surface.innerHTML = '<div class="rv-bub"><i>' + ICON("i-compare") + "</i><span>Before</span></div>";
  }
  function nextMoment() {
    if (!moment) return;
    if (moment.phase === 1) {
      moment.phase = 2; var a = moment.a;
      setAB(2); seek(a); A.play(vid());
      surface.innerHTML = '<div class="rv-bub like"><i>' + ICON("i-check") + "</i><span>After</span></div>";
    } else endMoment();
  }
  function endMoment() { moment = null; vid().pause(); setTimeout(function () { if (!moment) surface.innerHTML = ""; }, 900); }

  // ---------- keyboard ----------
  var talkKey = false;
  document.addEventListener("keydown", function (e) {
    if (!current() || e.metaKey && e.key.toLowerCase() !== "z" || e.ctrlKey && e.key.toLowerCase() !== "z" || e.altKey) return;
    if (sheet) { if (e.key === "Escape") closeSheet(); return; }
    var typing = /^(input|textarea|select)$/i.test(e.target.tagName || "");
    if (e.key === "Escape") { if (rail.classList.contains("open")) closeRail(); else if (comp) closeComposer(); else if (shown) clearSurface(); return; }
    if (typing) return;
    if (e.key === "Enter" && comp && !e.target.closest(".rv-talk, .rv-x, #rv-vplay, #rv-vrm")) { e.preventDefault(); saveNote(); return; }   // draw, then Enter
    var k = e.key.toLowerCase();
    if ((e.metaKey || e.ctrlKey) && k === "z") { if (comp && comp.marks.length) { e.preventDefault(); comp.marks.pop(); afterMark(); } return; }
    if (e.key === " " && !e.target.closest(".rv-talk")) { e.preventDefault(); if (comp) return; toggle(); return; }
    if (k === "n" && !busy) { e.preventDefault(); openComposer({}); return; }
    if (k === "l") { e.preventDefault(); love(); return; }
    if (k === "b" && R.applied) { e.preventDefault(); moment = null; setAB(R.ab === 2 ? 1 : 2); return; }
    if (k === "t" && !e.repeat && !busy) { e.preventDefault(); talkKey = true; startTalk(); return; }
    if (comp && { p: 1, c: 1, a: 1, d: 1 }[k]) { setTool({ p: "point", c: "circle", a: "arrow", d: "pen" }[k]); return; }
    if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && !comp && !e.target.closest("[data-wipe], .rv-track")) { e.preventDefault(); seek(vid().currentTime + (e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 5 : 1)); }
    if ((e.key === "." || e.key === ",") && !comp && vid().paused) { e.preventDefault(); seek(vid().currentTime + (e.key === "." ? 1 : -1) / 30, true); }
  });
  document.addEventListener("keyup", function (e) { if (talkKey && e.key.toLowerCase() === "t") { talkKey = false; stopTalk(); } });

  // ---------- layout ----------
  if (window.ResizeObserver) {
    new ResizeObserver(function () { if (current()) { sizeInk(); layoutStrip(); } }).observe(frame);
    new ResizeObserver(function () { if (current()) layoutStrip(); }).observe(track);
  }
  window.addEventListener("resize", function () { if (!narrow()) closeRail(); });
})();
