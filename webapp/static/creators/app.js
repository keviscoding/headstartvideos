/* The creator app prototype: sign-up -> 5-step onboarding -> editing -> result.
   Everything runs in the browser. Nothing is uploaded: a chosen clip is only read locally for its preview, and the
   editing screen replays a real edit: a creator's public Reel re-edited in the creator-energy look (media/local/demo_energy.json,
   from creator-edit-work/v2). */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var ICON = function (id, cls) { return '<svg' + (cls ? ' class="' + cls + '"' : "") + '><use href="icons.svg#' + id + '"/></svg>'; };
  function play(v) { if (!v) return; var p = v.play(); if (p && p.catch) p.catch(function () {}); }
  function fmt(t) { t = Math.max(0, Math.round(t)); return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0"); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  // ---------- state ----------
  var KEY = "cr-creators-proto";
  var S = { make: null, topic: null, platforms: [], refs: [], style: null, house: false, clip: null, freeLeft: 2, email: "", heard: null, version: 1, cut: 1 };
  try { var saved = JSON.parse(sessionStorage.getItem(KEY) || "null"); if (saved) Object.assign(S, saved, { clip: saved.clip && saved.clip.sample ? saved.clip : null }); } catch (e) {}
  function save() { try { var c = Object.assign({}, S); if (c.clip && !c.clip.sample) c.clip = null; sessionStorage.setItem(KEY, JSON.stringify(c)); } catch (e) {} }
  var params = new URLSearchParams(location.search);
  // The demo edit's two real cuts (creator-edit-work/v2/renders): the first cut is what a new edit shows; the second
  // is what the review player's "Apply notes" lands on (review.js, media/local/review.json).
  var CUTS = {
    1: { video: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/review/e_v1.mp4", poster: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/review/e_v1.jpg", beats: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/review/beats_v1/" },
    2: { video: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_edit.mp4", poster: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_edit.jpg", beats: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/beats/" }
  };
  function cut() { return CUTS[S.cut >= 2 ? 2 : 1]; }
  var STYLES = {
    story: { name: "Cinematic story", clip: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_story.mp4", poster: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_story.jpg", line: "Score, B-roll and title cards" },
    energy: { name: "Creator energy", clip: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.mp4", poster: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.jpg", line: "Punch-ins and yellow key words" },
    house: { name: "the look that fits your video", clip: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.mp4", poster: "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.jpg", line: "We pick it from what you said" }
  };
  // The example link is one we really learned (creators.learn, 2026-10-29): these are its measured numbers.
  var EXAMPLE = { url: "https://www.youtube.com/watch?v=aXwgg5Tj5Nc", learned: "Learned: 3.2 cuts per 10 s · 4.6 words/s · punch-ins up to 2.45×" };
  function styleLabel() {
    if (S.refs.length) {
      var r = S.refs[0], who = r.author || r.handle;
      return (who ? who + "'s style" : r.title ? "the example Short's style" : "the " + r.platform + "'s style") + (S.refs.length > 1 ? " + " + (S.refs.length - 1) + " more" : "");
    }
    return (STYLES[S.style] || STYLES.house).name;
  }
  // A pasted Reel's real picture and creator, from the server (owner's Loom 2026-10-30, P1-21: "I would like if the
  // preview comes up here, rather than a basic color gradient thing"). Without the backend, the plain card.
  function refThumb(r, cls) {
    var glyph = { tiktok: "p-tiktok", instagram: "p-instagram", youtube: "p-youtube" }[r.kind] || "i-link";
    return '<span class="' + cls + (r.thumb ? "" : r.loading ? " loading" : " plain") + '">' +
      (r.thumb ? '<img src="' + esc(r.thumb) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : r.loading ? "" : ICON(glyph)) + "</span>";
  }
  function previewRef(r) {
    var H = window.CRHosted;
    if (!H || !H.on || !H.preview || r.previewed) return;
    r.loading = true;
    H.preview(r.url).then(function (d) {
      r.loading = false; r.previewed = true;
      if (d) { if (d.thumb) r.thumb = d.thumb; r.author = d.author || r.author; r.title = r.title || d.title; if (!r.handle && d.handle) r.handle = "@" + d.handle; }
      save(); drawRefs(); if (stepNow === 5) startState();
    });
  }

  // ---------- toast + modals ----------
  var toastTimer;
  function toast(text) { $("#toast-t").textContent = text; var t = $("#toast"); t.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3200); }
  var openOverlay = null, lastFocus = null;
  function openModal(name, setup) {
    closeModal();
    lastFocus = document.activeElement;
    var ov = document.createElement("div");
    ov.className = "overlay";
    ov.appendChild($("#tpl-" + name).content.cloneNode(true));
    document.body.appendChild(ov);
    openOverlay = ov;
    requestAnimationFrame(function () { ov.classList.add("open"); });
    ov.addEventListener("click", function (e) { if (e.target === ov || e.target.closest("[data-close]")) closeModal(); });
    if (setup) setup(ov);
    var f = ov.querySelector("button, [href], input"); if (f) f.focus();
  }
  function closeModal() {
    if (!openOverlay) return;
    var ov = openOverlay; openOverlay = null;
    ov.classList.remove("open"); setTimeout(function () { ov.remove(); }, 200);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { if (openOverlay) closeModal(); else if ($("#heard").classList.contains("open")) closeHeard(); }
    if (e.key === "Tab" && openOverlay) { // keep focus inside the dialog
      var f = $$("button, [href], input, select, textarea", openOverlay).filter(function (x) { return !x.disabled && x.offsetParent; });
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
  });
  function drawQR(svg) { // a decorative QR-shaped pattern for the prototype (real code comes from the backend)
    var n = 29, h = "", seed = 7;
    function rnd() { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; }
    function finder(x, y) { h += '<rect x="' + x + '" y="' + y + '" width="7" height="7" rx="1.6" fill="#16161A"/><rect x="' + (x + 1) + '" y="' + (y + 1) + '" width="5" height="5" rx="1" fill="#fff"/><rect x="' + (x + 2) + '" y="' + (y + 2) + '" width="3" height="3" rx=".6" fill="#6D5AE0"/>'; }
    for (var y = 0; y < n; y++) for (var x = 0; x < n; x++) {
      var inF = (x < 8 && y < 8) || (x > 20 && y < 8) || (x < 8 && y > 20);
      if (!inF && rnd() > 0.52) h += '<rect x="' + x + '" y="' + y + '" width="1" height="1" rx=".3" fill="#16161A"/>';
    }
    finder(0, 0); finder(22, 0); finder(0, 22);
    svg.innerHTML = h;
  }
  function openPlans(reason) {
    openModal("plans", function (ov) {
      if (reason) $("#pl-sub", ov).textContent = reason;
      window.CRPlans.mount(ov, { onChoose: function (p) { closeModal(); toast(p.id === "free" ? "You're on the free plan." : p.name + " plan: checkout opens here (prototype)."); } });
    });
  }
  document.addEventListener("click", function (e) {
    var o = e.target.closest("[data-open]");
    if (!o) return;
    var n = o.dataset.open;
    if (n === "plans") return openPlans();
    if (n === "qr") return openModal("qr", function (ov) { drawQR($("#qr-code", ov)); });
    if (n === "drive") return openModal("drive", function (ov) { $("[data-drive]", ov).addEventListener("click", function () { closeModal(); toast("Drive picking switches on with the real backend."); }); });
  });

  // ---------- router ----------
  var screens = {};
  $$("[data-screen]").forEach(function (s) { screens[s.dataset.screen] = s; });
  var current = null;
  function route() {
    var h = (location.hash || "#signup").slice(1);
    var m = h.match(/^step-([1-5])$/);
    if (h === "start") h = "step-1", m = ["", "1"];
    var dashView = h === "home" || h === "styles" ? h : null;   // the home (home.js): your Reels, and Styles
    var name = m ? "onboard" : dashView ? "dash" : (screens[h] ? h : "signup");
    var H = window.CRHosted;
    // a real edit's screens, the sign-up and the home wait for hosted.js to know who's signed in (else the demo, or
    // "Create your free account", would flash in first)
    if (/^(result|editing|review|signup|dash)$/.test(name) && H && H.ready && !H.settled) { H.ready.then(route); return; }
    if (H && H.hosted && H.me && H.me.user) {
      // signed in: never the sign-up (Back from onboarding used to land on "Welcome back, log in"); the home once
      // they've made a Reel, else onboarding
      if (name === "signup" || (name === "dash" && !H.on)) { replace(H.on && H.hasReels() ? "home" : "step-1"); return; }
    } else if (H && H.hosted && name === "dash") { replace("signup"); return; }
    if (name === "result" && !demo && !(window.CRHosted && window.CRHosted.on)) { loadDemo().then(route); return; }
    if (name === "review" && !demo) { loadDemo().then(route); return; }   // the review player reads the demo's data too
    if (current !== name) {
      Object.keys(screens).forEach(function (k) { screens[k].classList.toggle("on", k === name); });
      $$("video").forEach(function (v) { if (!screens[name].contains(v)) v.pause(); });
      if (current === "review" && window.CRReview) window.CRReview.leave();
      if (current === "dash" && window.CRHome) window.CRHome.leave();
      current = name;
      window.scrollTo(0, 0);
    }
    if (name === "signup") enterSignup(h === "signin");
    if (name === "onboard") showStep(+m[1]);
    if (name === "editing") startEditing();
    if (name === "result") enterResult();
    if (name === "review" && window.CRReview) window.CRReview.enter(demo);
    if (name === "dash" && window.CRHome) window.CRHome.enter(dashView);
    $$("[data-screen].on video[data-auto]").forEach(play);
  }
  window.addEventListener("hashchange", route);
  function go(h) { if (location.hash === "#" + h) route(); else location.hash = h; }
  function replace(h) { if (location.hash === "#" + h) route(); else location.replace("#" + h); }

  // ---------- New Reel: from the home, the result or Styles, straight to the look then the upload ----------
  // The profile questions (steps 1-3) are for the first Reel only. The last-used look comes pre-picked; Styles' "Use
  // this look" and "Try again" skip to the upload with theirs.
  function lastLook() { var H = window.CRHosted, l = H && H.list && H.list[0]; return (l && l.look) || S.lastLook || null; }
  function newReel(o) {
    o = o || {};
    if (S.freeLeft <= 0) return openPlans("You've used your 2 free Reels. Every plan keeps everything you've seen.");
    if (S.clip && S.clip.url && !S.clip.sample) URL.revokeObjectURL(S.clip.url);
    S.flow = "new"; S.refs = []; S.house = false; S.clip = null; CRApp.file = null;
    if (o.reuse) S.clip = { name: o.reuse.name || "Your take", url: o.reuse.url, reuse: o.reuse.key, dur: o.reuse.seconds || 0 };
    S.style = o.paste ? null : o.look || lastLook() || null;
    save(); rv.pause(); refState("", "");
    go(o.step === 5 && S.style ? "step-5" : "step-4");
    if (o.paste) setTimeout(function () { refInput.focus({ preventScroll: true }); refInput.scrollIntoView({ block: "center" }); }, 80);
  }

  // ---------- sign up ----------
  var signin = false;
  function enterSignup(isSignin) {
    signin = isSignin;
    $("#su-h").textContent = signin ? "Welcome back" : "Create your free account";
    $("#su-sub").textContent = signin ? "Log in to pick up where you left off." : "2 free edits with everything included. No card.";
    $("#su-switch").innerHTML = signin ? 'New here? <button type="button" data-auth-toggle>Create an account</button>' : 'Already have an account? <button type="button" data-auth-toggle>Log in</button>';
    var ref = params.get("ref"), r = ref && window.CRReel.parse(ref);
    $("#ref-chip").hidden = !r;
    if (r) { $("#ref-chip-name").textContent = (r.handle ? r.handle + " · " : "") + r.platform; if (!S.refs.some(function (x) { return x.url === r.url; })) { S.refs = [r]; S.style = null; S.house = false; save(); } }
    var plan = params.get("plan"), p = plan && window.CRPlans.list.filter(function (x) { return x.id === plan; })[0];
    $("#plan-chip").hidden = !p;
    if (p) $("#plan-chip-name").textContent = p.name;
  }
  document.addEventListener("click", function (e) { if (e.target.closest("[data-auth-toggle]")) go(signin ? "signup" : "signin"); });
  $("#google-btn").addEventListener("click", function () { if (window.CRHosted && window.CRHosted.google()) return; afterAuth("Signed in with Google (prototype)."); });
  $("#email-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var v = $("#email").value.trim(), msg = $("#email-msg");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)) { msg.className = "field-msg bad"; msg.textContent = v ? "That email doesn't look right. Check for typos." : "Enter your email to continue."; $("#email").focus(); return; }
    msg.className = "field-msg"; msg.textContent = "";
    if (window.CRHosted && window.CRHosted.email(v)) return; // the real sign-in: an emailed code
    S.email = v; save();
    afterAuth("We'd email a sign-in link to " + v + " (prototype).");
  });
  function afterAuth(text) { toast(text); go(signin && S.make ? "step-5" : "step-1"); }

  // ---------- onboarding ----------
  var stepNow = 1;
  function setPressed(group, values) { $$(".tile, .style-tile", group).forEach(function (t) { t.setAttribute("aria-pressed", String(values.indexOf(t.dataset.v || t.dataset.style) >= 0)); }); }
  function showStep(n) {
    stepNow = n;
    if (S.flow === "new" && n < 4) { S.flow = null; save(); }   // back into the questions: that's onboarding again
    var nm = S.flow === "new";
    $$("[data-step]").forEach(function (s) { s.hidden = +s.dataset.step !== n; });
    $("#ob-split").classList.toggle("newflow", nm);
    $("#ob-label").textContent = nm ? "New Reel · " + (n - 3) + " of 2" : "Step " + n + " of 5";
    $$("#ob-progress i").forEach(function (i, k) { i.classList.toggle("done", k < n); });
    setPressed($('[data-single="make"]'), [S.make]);
    setPressed($('[data-single="topic"]'), [S.topic]);
    setPressed($('[data-multi="platforms"]'), S.platforms);
    $("#faceless-note").hidden = S.make !== "faceless";
    if (n === 3) platState();
    if (n === 4) { drawRefs(); styleState(); if (nm) showPicked(); }
    if (n === 5) { drawFile(); startState(); }
    preview(n);
    var h = $('[data-step="' + n + '"] .qtitle'); if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
  }
  function next() { if (stepNow < 5) go("step-" + (stepNow + 1)); }
  $$("[data-back]").forEach(function (b) { b.addEventListener("click", function () { history.length > 1 ? history.back() : go(S.flow === "new" && stepNow === 4 ? "home" : "step-" + Math.max(1, stepNow - 1)); }); });
  $$("[data-skip]").forEach(function (b) { b.addEventListener("click", next); });
  $$("[data-next]").forEach(function (b) { b.addEventListener("click", next); });

  $$("[data-single]").forEach(function (group) {
    group.addEventListener("click", function (e) {
      var t = e.target.closest(".tile"); if (!t) return;
      var key = group.dataset.single;
      S[key] = t.dataset.v; save();
      setPressed(group, [t.dataset.v]);
      if (key === "heard") return heardPick(t.dataset.v);
      if (key === "make") { $("#faceless-note").hidden = t.dataset.v !== "faceless"; if (t.hasAttribute("data-hold")) return; }
      preview(stepNow);
      setTimeout(next, 280); // let the tick pop before moving on
    });
  });
  var platGroup = $('[data-multi="platforms"]');
  platGroup.addEventListener("click", function (e) {
    var t = e.target.closest(".tile"); if (!t) return;
    var v = t.dataset.v, i = S.platforms.indexOf(v);
    if (i >= 0) S.platforms.splice(i, 1); else S.platforms.push(v);
    save(); setPressed(platGroup, S.platforms); platState(); preview(3);
  });
  function platState() {
    var b = $("#plat-next"), n = S.platforms.length;
    b.disabled = !n;
    b.innerHTML = n ? "Continue " + ICON("i-arrow") : "Pick at least one";
    b.querySelector && b.querySelector("svg") && (b.querySelector("svg").setAttribute("width", "18"), b.querySelector("svg").setAttribute("height", "18"));
  }
  $("#plat-next").addEventListener("click", next);
  document.addEventListener("keydown", function (e) {
    if (current !== "onboard" || openOverlay || e.metaKey || e.ctrlKey || e.altKey) return;
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    var stepEl = $('[data-step="' + stepNow + '"]');
    if (/^[1-9]$/.test(e.key)) {
      var tiles = $$("[data-single] .tile, [data-multi] .tile", stepEl);
      var t = tiles[+e.key - 1]; if (t) { e.preventDefault(); t.click(); t.focus(); }
    } else if (e.key === "Enter" && !e.target.closest("button, a")) {
      var btn = $(".qfoot .btn", stepEl); if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
    }
  });

  // step 4: whose editing
  var refInput = $("#ref-input"), refField = $("#ref-field"), refMsg = $("#ref-msg");
  function refState(state, text) { refField.classList.toggle("bad", state === "bad"); refField.classList.toggle("good", state === "good"); refMsg.className = "field-msg" + (state ? " " + state : ""); refMsg.textContent = text || ""; }
  refInput.addEventListener("input", function () {
    var v = refInput.value.trim();
    if (!v) return refState("", "");
    var r = window.CRReel.parse(v);
    if (r) refState("good", (r.handle ? r.handle + " · " : "") + r.platform + ". Press Add.");
    else if (v.length > 12) refState("bad", "That isn't a TikTok, Reel or Short link. Open the video, tap Share, then Copy link.");
    else refState("", "");
  });
  function addRef(r, learned) {
    if (S.refs.some(function (x) { return x.url === r.url; })) { refState("bad", "You've already added that one."); return; }
    if (S.refs.length >= 3) { refState("bad", "Three is plenty. Remove one to add another."); return; }
    if (learned) r.learned = learned;
    S.refs.push(r); S.style = null; S.house = false; save();
    previewRef(r);
    refInput.value = ""; refState("good", S.refs.length < 3 ? "Added. Add up to " + (3 - S.refs.length) + " more of theirs for a closer match." : "Added. That's the most we use.");
    drawRefs(); styleState(); preview(4);
  }
  $("#ref-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var v = refInput.value.trim();
    if (!v) { refState("bad", "Paste a link first."); refInput.focus(); return; }
    var r = window.CRReel.parse(v);
    if (!r) { refState("bad", "That isn't a TikTok, Reel or Short link. Open the video, tap Share, then Copy link."); refInput.focus(); return; }
    var why = window.CRHosted && window.CRHosted.refProblem && window.CRHosted.refProblem(r);
    if (why) { refState("bad", why); refInput.focus(); return; }
    addRef(r);
  });
  $("#ref-example").addEventListener("click", function () { var r = window.CRReel.parse(EXAMPLE.url); r.title = "Fast talking-head Short (example)"; addRef(r, EXAMPLE.learned); });
  function drawRefs() {
    var box = $("#refs");
    S.refs.forEach(previewRef);                       // links added before a reload, or on the landing page
    box.innerHTML = S.refs.map(function (r, k) {
      var name = r.author || r.handle || (r.loading ? "Getting its preview…" : r.platform);
      var sub = r.author || r.handle ? r.platform + (r.title && !r.learned ? " · “" + r.title + "”" : "") : r.platform + " · " + r.url.replace(/^https?:\/\/(www\.)?/, "");
      return '<div class="ref">' + refThumb(r, "thumb " + r.kind) +
        '<span class="info"><b>' + esc(name) + "</b>" +
        "<span>" + esc(sub) + "</span>" +
        '<span class="' + (r.learned ? "learned" : "") + '">' + esc(r.learned || "We'll learn its cuts, captions and sound when you start") + "</span></span>" +
        '<button type="button" class="rm" data-rm="' + k + '" aria-label="Remove ' + esc(r.author || r.handle || r.platform) + ' link">' + ICON("i-x") + "</button></div>";
    }).join("");
    $$(".rm svg", box).forEach(function (s) { s.setAttribute("width", "16"); s.setAttribute("height", "16"); });
    $("#ref-count").textContent = S.refs.length ? S.refs.length + " of 3 links" : "";
  }
  $("#refs").addEventListener("click", function (e) {
    var b = e.target.closest("[data-rm]"); if (!b) return;
    S.refs.splice(+b.dataset.rm, 1); save(); drawRefs(); styleState(); preview(4); refState("", ""); refInput.focus();
  });
  var stylesEl = $("#styles");
  stylesEl.addEventListener("click", function (e) {
    var t = e.target.closest(".style-tile"); if (!t) return;
    S.style = S.style === t.dataset.style ? null : t.dataset.style;
    if (S.style) { S.refs = []; S.house = false; refState("", ""); drawRefs(); }
    save(); styleState(); preview(4);
  });
  function bindTile(t) {
    var v = $("video", t), img = $("img", t);
    function on() { img.style.opacity = 0; v.preload = "auto"; play(v); }
    function off() { v.pause(); }
    t.addEventListener("mouseenter", on); t.addEventListener("mouseleave", off); t.addEventListener("focus", on); t.addEventListener("blur", off);
    v.style.position = "absolute"; v.style.inset = "0"; img.style.transition = "opacity .2s";
  }
  $$(".style-tile", stylesEl).forEach(bindTile);
  // the looks the engine draws (looks.json, from creators/looks/catalog.py): each tile plays its reference clip, the
  // same raw take edited in that look
  fetch("looks.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (looks) {
    if (!looks) return;
    looks.forEach(function (l) {
      STYLES[l.key] = { name: l.name, clip: l.clip, poster: l.poster, line: l.line };
      var t = document.createElement("button"); t.type = "button"; t.className = "style-tile"; t.setAttribute("aria-pressed", "false"); t.dataset.style = l.key;
      t.innerHTML = '<div class="vid"><img src="' + esc(l.poster) + '" alt="" loading="lazy"><video muted playsinline loop preload="none" src="' + esc(l.clip) + '"></video></div><span class="tickbox"><svg><use href="icons.svg#i-check"/></svg></span><div class="cap2"><b>' + esc(l.name) + "</b><span>" + esc(l.line) + "</span></div>";
      stylesEl.appendChild(t); bindTile(t);
    });
    if (S.style && !STYLES[S.style]) { S.style = null; save(); }
    styleState(); if (location.hash === "#step-4") { preview(4); if (S.flow === "new") showPicked(); }
  }).catch(function () {});
  function showPicked() {   // the pre-picked look, scrolled into view inside the look board
    var t = $('.style-tile[aria-pressed="true"]', stylesEl);
    if (t) stylesEl.scrollTop += t.getBoundingClientRect().top - stylesEl.getBoundingClientRect().top - 8;
  }
  function styleState() {
    $$(".style-tile", stylesEl).forEach(function (t) { t.setAttribute("aria-pressed", String(S.style === t.dataset.style)); });
    var b = $("#style-next"), ok = S.refs.length || S.style;
    b.disabled = !ok;
    b.innerHTML = S.refs.length ? "Learn this style " + ICON("i-arrow") : S.style ? "Use " + esc((STYLES[S.style] || { name: "this look" }).name) + " " + ICON("i-arrow") : "Pick a look or paste a link";
    var s = b.querySelector("svg"); if (s) { s.setAttribute("width", "18"); s.setAttribute("height", "18"); }
  }
  $("#style-next").addEventListener("click", next);
  $("[data-house]").addEventListener("click", function () { S.refs = []; S.style = null; S.house = true; save(); next(); });

  // step 5: the raw clip (read locally only)
  var MAXS = 300;
  function drawFile() {
    var slot = $("#file-slot"), c = S.clip;
    $("#drop").hidden = !!c;
    if (!c) { slot.innerHTML = ""; return; }
    var cls = c.error ? "bad" : c.warn ? "warn" : "";
    var note = c.error || c.warn || (c.sample ? "Our sample: one take, straight to camera" : c.reuse ? "Your take from before: no upload needed" : "Ready to edit");
    slot.innerHTML = '<div class="file-card ' + cls + '"><span class="fthumb">' +
      (c.sample ? '<img src="https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.jpg" alt="">' : c.url ? '<video muted playsinline preload="metadata" src="' + esc(c.url) + '#t=0.5"></video>' : ICON("i-film")) + "</span>" +
      '<span class="fi"><b>' + esc(c.name) + "</b><span>" + [c.dur ? fmt(c.dur) : null, c.w ? c.w + "×" + c.h : null, c.size ? (c.size / 1048576).toFixed(c.size > 1e8 ? 0 : 1) + " MB" : null].filter(Boolean).join(" · ") + "</span>" +
      '<span class="note">' + esc(note) + "</span></span>" +
      '<button type="button" class="btn btn-sm btn-ghost" id="file-change">Change</button></div>';
    $("#file-change").addEventListener("click", function () { if (S.clip && S.clip.url && !S.clip.sample) URL.revokeObjectURL(S.clip.url); S.clip = null; save(); drawFile(); startState(); preview(5); });
  }
  function startState() {
    var b = $("#start-edit"), c = S.clip, ok = c && !c.error;
    $("#free-pill").textContent = S.freeLeft > 0 ? S.freeLeft + " free edit" + (S.freeLeft === 1 ? "" : "s") + " left" : "Free edits used";
    b.disabled = !ok;
    b.innerHTML = !c ? "Choose a clip first" : c.error ? "Pick another clip" : (S.freeLeft > 0 ? "Start my free edit " : "Start my edit ") + ICON("i-arrow");
    var s = b.querySelector("svg"); if (s) { s.setAttribute("width", "18"); s.setAttribute("height", "18"); }
    // the look this edit is in, with a way to change it (Submagic's upload shows its template the same way)
    var st = !S.refs.length && STYLES[S.style];
    $("#look-chip").hidden = !(S.refs.length || S.style || S.house);
    $("#look-chip-t").textContent = styleLabel();
    $("#look-chip-th").innerHTML = S.refs.length ? refThumb(S.refs[0], "rthumb") : st && st.poster ? '<img src="' + esc(st.poster) + '" alt="">' : ICON("i-sparkles");
    $("#look-chip").classList.toggle("is-ref", !!S.refs.length);
    var eta = window.CRHosted && window.CRHosted.on && window.CRHosted.eta ? window.CRHosted.eta() : "About 2 minutes"; // real edits: measured
    $("#start-hint").textContent = ok ? eta + " · in " + styleLabel() : eta;
  }
  function takeFile(f) {
    if (!f) return;
    if (!/^video\//.test(f.type) && !/\.(mov|mp4|m4v)$/i.test(f.name)) { S.clip = { name: f.name, size: f.size, error: "That isn't a video. Pick an MP4 or MOV." }; drawFile(); startState(); return; }
    CRApp.file = f; // the real upload sends this file (hosted.js)
    var url = URL.createObjectURL(f), v = document.createElement("video");
    v.preload = "metadata"; v.muted = true; v.src = url;
    S.clip = { name: f.name, size: f.size, url: url };
    v.onloadedmetadata = function () {
      var c = S.clip; if (!c || c.url !== url) return;
      c.dur = v.duration; c.w = v.videoWidth; c.h = v.videoHeight;
      if (isFinite(c.dur) && c.dur > MAXS) c.error = "That clip is " + fmt(c.dur) + ". Free edits take up to 5:00. Trim it, or pick another.";
      else if (c.w && c.h && c.w > c.h) c.warn = "It's landscape. We'll crop it to 9:16 around your face.";
      else if (c.dur && c.dur < 5) c.warn = "That's very short. Edits work best from about 20 seconds.";
      drawFile(); startState(); preview(5);
    };
    v.onerror = function () { var c = S.clip; if (c && c.url === url) { c.warn = "We can't preview this one in the browser, but we can still edit it."; drawFile(); startState(); } };
    drawFile(); startState(); preview(5);
  }
  $("#file").addEventListener("change", function (e) { takeFile(e.target.files[0]); e.target.value = ""; });
  $("#look-chip-change").addEventListener("click", function () { go("step-4"); });
  var drop = $("#drop");
  ["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); }); });
  drop.addEventListener("drop", function (e) { takeFile(e.dataTransfer.files[0]); });
  $("#use-sample").addEventListener("click", function () { S.clip = { name: "raw-take-sample.mov", sample: true, dur: 53.2, w: 1080, h: 1920, size: 0 }; save(); drawFile(); startState(); preview(5); });
  $("#start-edit").addEventListener("click", function () {
    if (!S.clip || S.clip.error) return;
    if (window.CRHosted && window.CRHosted.start()) return; // signed in: upload and start a real edit
    if (S.freeLeft <= 0) return openPlans("You've used your 2 free edits. Every plan keeps everything you've seen.");
    S.freeLeft -= 1; S.version = 1; S.cut = 1; S.flow = null; S.lastLook = S.style || S.lastLook; S.demoAt = Date.now() / 1000; save();
    go("editing");
  });

  // live preview on the right
  var pv = $("#pv-video");
  function setPv(src, poster, tag, good) {
    if (pv.getAttribute("src") !== src) { pv.poster = poster; pv.src = src; }
    play(pv);
    $("#pv-tag-t").textContent = tag; $("#pv-tag").classList.toggle("good", !!good);
  }
  var PV_ICONS = { 1: ["i-music", "i-check"], 2: ["i-film", "i-music"], 3: ["i-frame", "i-wave"], 4: ["i-sparkles", "i-type"], 5: ["i-scissors", "i-check"] };
  function preview(n) {
    var cap = $("#pv-caption"), plats = $("#pv-plats"), c1 = $("#pv-chip1-t"), c2 = $("#pv-chip2-t");
    plats.hidden = n !== 3;
    $("#pv-chip1 .ico").innerHTML = ICON(PV_ICONS[n][0]); $("#pv-chip2 .ico").innerHTML = ICON(PV_ICONS[n][1]);
    if (n === 1) { setPv("https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.jpg", "Creator energy look", true); c1.innerHTML = "<b>Music</b> scored to your story"; c2.innerHTML = "<b>Watched back</b> before you see it"; cap.innerHTML = "<b>This is what you'll get:</b> your own footage, edited like the creators you watch."; }
    if (n === 2) { var tl = { money: "the numbers you say", tech: "the apps you name", fitness: "the moves you show", education: "each step", coaching: "the line that lands", podcast: "the quote", lifestyle: "the punchline" }[S.topic] || "the words that matter";
      setPv("https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_story.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_story.jpg", "B-roll on your words", true); c1.innerHTML = "<b>B-roll</b> on " + esc(tl); c2.innerHTML = "<b>Music</b> in your video's mood"; cap.innerHTML = "<b>Shots land the moment you say it,</b> tuned to your topic."; }
    if (n === 3) { setPv("https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.jpg", "1080×1920 · −14 LUFS", true); c1.innerHTML = "<b>9:16</b> full HD"; c2.innerHTML = "<b>Loud enough</b> for every app";
      cap.innerHTML = "<b>One export for everywhere you post.</b>";
      plats.innerHTML = ["TikTok", "Reels", "Shorts", "LinkedIn", "Facebook", "X"].map(function (p) { return '<span class="pill ' + (S.platforms.indexOf(p) >= 0 ? "on" : "off") + '">' + p + "</span>"; }).join(""); }
    if (n === 4) { var st = STYLES[S.style] || STYLES.story;
      if (S.refs.length) { setPv("https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/look_energy.jpg", S.refs.length > 1 ? "Learning " + S.refs.length + " videos" : "Learning their look", false); c1.innerHTML = "<b>Their look,</b> your footage"; c2.innerHTML = "<b>~45 s</b> to learn it"; cap.innerHTML = "<b>We measure how they edit</b> and cut your clip the same way."; }
      else { setPv(st.clip, st.poster, S.style ? st.name : "Pick a look", !!S.style); c1.innerHTML = "<b>" + esc(st.name) + "</b>"; c2.innerHTML = esc(st.line); cap.innerHTML = "<b>Real edits of real Reels.</b> Hover a look to watch it."; } }
    if (n === 5) { var c = S.clip;
      if (c && !c.sample && c.url && !c.error) { setPv(c.url, "", "Your raw take" + (c.dur ? " · " + fmt(c.dur) : ""), false); cap.innerHTML = window.CRHosted && window.CRHosted.on ? "<b>That's your clip.</b> Only you and our edit machine see it." : "<b>That's your clip.</b> It stays on your device in this prototype."; }
      else { setPv("https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.jpg", c && c.sample ? "Sample raw take · 0:53" : "A raw take, like yours", false); cap.innerHTML = "<b>One take, straight to camera.</b> That's all a look needs."; }
      c1.innerHTML = "<b>Best take</b> kept"; c2.innerHTML = "<b>Minutes,</b> not hours"; }
  }

  // ---------- editing ----------
  var demo = null;
  function loadDemo() { return demo ? Promise.resolve(demo) : fetch("media/local/demo_energy.json").then(function (r) { return r.json(); }).then(function (d) { demo = d; return d; }); }
  var editTimer = null;
  function startEditing() {
    clearInterval(editTimer);
    if (window.CRHosted && window.CRHosted.on) return window.CRHosted.editing();
    var own = S.clip && !S.clip.sample && S.clip.url;
    var v = $("#ed-video");
    if (own) { v.removeAttribute("poster"); v.src = S.clip.url; $("#ed-tag").textContent = "Your raw take"; } else { v.poster = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.jpg"; v.src = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.mp4"; $("#ed-tag").textContent = "Sample raw take"; }
    play(v);
    $("#ed-lead").innerHTML = S.refs.length ? "Learning <b>" + esc(styleLabel()) + "</b>, then cutting your clip to match." : "In <b>" + esc(styleLabel()) + "</b>. You can close this tab: we'll email you.";
    loadDemo().then(function (d) { runEdit(d); });
  }
  function runEdit(d) {
    var learn = S.refs.length > 0;
    var c = d.check, broll = d.added.filter(function (a) { return a.kind === "B-roll"; }).length;
    var stages = [
      { t: "Listening to every word", d: d.words_total + " words, each timed to a hundredth of a second", w: 12 },
      { t: "Tightening the take", d: d.pauses_cut + " pauses trimmed, " + d.pause_seconds + " s of dead air", w: 10 },
      { t: learn ? "Learning " + styleLabel() : "Loading " + styleLabel(), d: learn ? "Cuts, captions, music level, when the sounds hit" : "Type, music level, sound rules, cut rhythm", w: learn ? 14 : 6 },
      { t: "Scoring your music", d: "An original bed, " + d.music.under_db + " dB under your voice, ending on “" + d.music.ends_on + "”", w: 16 },
      { t: "Cutting in B-roll and motion", d: broll + " B-roll moments, an order ticket, " + d.punch_ins + " punch-ins", w: 16 },
      { t: "Setting captions and sound", d: d.captions + " captions · " + d.sfx + " effects, each on something you see", w: 12 },
      { t: "Mastering the mix", d: c.lufs.toFixed(1).replace("-", "−") + " LUFS, peak " + c.true_peak_db.toFixed(1).replace("-", "−") + " dB", w: 8 },
      { t: "Watching it back", d: "Next to your raw take, " + c.watch_passes + " passes", w: 12 }
    ];
    var total = stages.reduce(function (a, s) { return a + s.w; }, 0);
    $("#stages").innerHTML = stages.map(function (s, k) { return '<div class="stg" data-k="' + k + '"><span class="st">' + ICON("i-check") + "</span><div><b>" + esc(s.t) + '</b><div class="d">' + esc(s.d) + "</div></div></div>"; }).join("");
    // transcript of the first take (with retakes) streaming in
    var words = d.words, tx = $("#tx");
    tx.innerHTML = words.map(function (w, i) {
      var cls = "w" + (w.p ? " p" : "") + (w.c ? " c" : "");
      var txt = w.f && w.f.toLowerCase() !== w.w.toLowerCase().replace(/[.,!?]$/, "") ? '<span class="badge fix">' + esc(w.w) + " → " + esc(w.f) + "</span>" : esc(w.w);
      return '<span class="' + cls + '" data-i="' + i + '" style="opacity:0">' + txt + "</span>";
    }).join(" ");
    var spans = $$(".w", tx), shown = 0, struck = 0;
    var dropIdx = words.map(function (w, i) { return w.x ? i : -1; }).filter(function (i) { return i >= 0; });
    var t0 = performance.now(), SPEED = 14000; // the prototype plays a ~2 min edit in 14 s
    var bar = $("#ed-bar"), pct = $("#ed-pct");
    function tick() {
      var p = Math.min(1, (performance.now() - t0) / SPEED), acc = 0, k;
      var pw = p * total;
      for (k = 0; k < stages.length; k++) {
        var el = $('.stg[data-k="' + k + '"]');
        var a = acc, b = acc + stages[k].w; acc = b;
        el.classList.toggle("done", pw >= b);
        el.classList.toggle("active", pw >= a && pw < b);
      }
      // words appear during stage 1, retakes strike during stage 2
      var s1 = stages[0].w / total, s2 = (stages[0].w + stages[1].w) / total;
      var nShow = Math.floor(Math.min(1, p / s1) * spans.length);
      for (; shown < nShow; shown++) spans[shown].style.opacity = 1;
      var nStrike = p > s1 ? Math.floor(Math.min(1, (p - s1) / (s2 - s1)) * dropIdx.length) : 0;
      for (; struck < nStrike; struck++) spans[dropIdx[struck]].classList.add("x");
      var r = Math.round(p * 100);
      bar.querySelector("i").style.width = r + "%"; bar.setAttribute("aria-valuenow", r); pct.textContent = r + "%";
      $("#ed-eta").textContent = p < 1 ? "About " + Math.max(1, Math.ceil((1 - p) * 2)) + " min left" : "Done";
      if (p >= 1) { clearInterval(editTimer); setTimeout(function () { if (current === "editing") go("result"); }, 700); }
    }
    editTimer = setInterval(tick, 120); tick();
  }

  // ---------- result ----------
  var rv = $("#rs-video"), heardTimer = null, resultReady = false;
  function enterResult() {
    clearInterval(editTimer);
    if (window.CRHosted && window.CRHosted.on) return window.CRHosted.result();
    var d = demo;
    $("#rs-free").textContent = S.freeLeft > 0 ? S.freeLeft + " of 2 free edits left" : "Free edits used";
    $("#rs-h").textContent = "“" + d.hook + "”";
    $("#rs-meta").textContent = fmt(d.edit_seconds) + " Reel from a " + fmt(d.raw_seconds) + " raw take · 1080×1920 · " + styleLabel().replace(/^./, function (c) { return c.toUpperCase(); });
    $("#rs-changed-sub").textContent = "From " + fmt(d.raw_seconds) + " of raw footage to " + fmt(d.edit_seconds) + ".";
    if (!resultReady) {
      resultReady = true;
      var c = d.check;
      $("#rs-checks").innerHTML = [
        [c.lufs.toFixed(1).replace("-", "−"), "LUFS, in range for TikTok and Reels"],
        [c.true_peak_db.toFixed(1).replace("-", "−") + " dB", "peak, so phones never distort it"],
        [String(c.frozen_stretches), c.frozen_stretches === 1 ? "frozen frame: " + c.frozen_note : "frozen frames"],
        [c.watch_passes + " passes", "watched next to your raw take"]
      ].map(function (x) { return '<div class="ck"><b class="tnum"><i>' + ICON("i-check") + "</i>" + esc(x[0]) + "</b><span>" + esc(x[1]) + "</span></div>"; }).join("");
      $("#rs-added").innerHTML = d.added.map(function (r) { return '<li><button type="button" data-seek="' + r.at + '"><span class="tchip">' + fmt(r.at) + '</span><span class="kind">' + esc(r.kind) + '</span><span class="txt">' + esc(r.text) + "</span></button></li>"; }).join("");
      $("#rs-acount").textContent = d.added.length;
      // Real frames of this edit, beat by beat, with the line you said there (media/local/beats.json).
      fetch("media/local/beats.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (b) {
        if (!b || !b.energy) return;
        beats = b.energy; drawBeats();
      }).catch(function () {});
      $("#rs-sound-sub").textContent = "Made for this video, mixed under your voice.";
      $("#rs-sound").innerHTML = [
        "Music: " + d.music.name + ", " + d.music.under_db + " dB under you. It dips when you talk and its last hit lands on “" + d.music.ends_on + "”.",
        d.sfx + " sound effects, each on something you see: a whoosh into B-roll, a pop when the ticket stamps, a hit on the last word.",
        "Your voice, levelled and mastered to " + c.lufs.toFixed(1).replace("-", "−") + " LUFS."
      ].map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("");
      // What the editor did to this take, worded for the creator.
      $("#rs-notes").innerHTML = [
        "Kept your one take whole and trimmed " + d.pauses_cut + " pauses (" + d.pause_seconds + " s).",
        "Opened on your line “" + d.hook + "”, with a hook card on top.",
        "Put punch-ins on your punchlines and yellow on the words that land.",
        "Held the last frame under MOVE. so the ending hits."
      ].map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("");
      drawMarkers();
      window.CRCutline.render($("#rs-cutline"), d);
    }
    syncCut();
    rv.currentTime = 0;
    clearTimeout(heardTimer);
    if (!S.heard) heardTimer = setTimeout(openHeard, 9000);
  }
  // Real frames of the cut on screen, beat by beat, with the line said there.
  var beats = null, shownCut = 1;
  function drawBeats() {
    if (!beats) return;
    var dir = cut().beats;
    $("#rs-added").innerHTML = beats.map(function (r) { return '<li><button type="button" data-seek="' + r.t + '"><span class="th"><img src="' + esc(dir + r.img.split("/").pop()) + '" alt="" loading="lazy"></span><span class="tchip">' + fmt(r.t) + '</span><span class="txt">' + esc(r.did) + '<span class="said2">“' + esc(r.said) + '”</span></span></button></li>'; }).join("");
    $("#rs-acount").textContent = beats.length;
  }
  function syncCut() { // after the review applied notes, the result shows the second cut
    var c = cut();
    $("#rs-dl").setAttribute("href", c.video);
    $("#rs-tag-t").textContent = mode === "after" ? "Your Reel · v" + S.version : $("#rs-tag-t").textContent;
    if (shownCut === (S.cut >= 2 ? 2 : 1)) return;
    shownCut = S.cut >= 2 ? 2 : 1;
    if (mode === "after") { rv.src = c.video; rv.poster = c.poster; }
    drawBeats();
  }
  function drawMarkers() {
    var sc = $("#rs-scrub"), dur = demo.edit_seconds;
    $$(".mk", sc).forEach(function (m) { m.remove(); });
    demo.added.forEach(function (r) { var m = document.createElement("span"); m.className = "mk"; m.style.left = (r.at / dur * 100) + "%"; sc.appendChild(m); });
  }
  var mode = "after";
  function upd() {
    var dur = rv.duration || (demo ? (mode === "after" ? demo.edit_seconds : demo.raw_seconds) : 0), p = dur ? rv.currentTime / dur : 0;
    $(".scrub .fill").style.width = (p * 100) + "%"; $(".scrub .knob").style.left = (p * 100) + "%";
    $("#rs-time").textContent = fmt(rv.currentTime) + " / " + fmt(dur);
    $("#rs-scrub").setAttribute("aria-valuenow", Math.round(rv.currentTime)); $("#rs-scrub").setAttribute("aria-valuemax", Math.round(dur));
    var pp = $("#rs-pp"); pp.innerHTML = ICON(rv.paused ? "i-play" : "i-pause"); pp.setAttribute("aria-label", rv.paused ? "Play" : "Pause");
  }
  ["timeupdate", "play", "pause", "loadedmetadata", "seeked"].forEach(function (ev) { rv.addEventListener(ev, upd); });
  $("#rs-pp").addEventListener("click", function () { if (rv.paused) play(rv); else rv.pause(); });
  rv.addEventListener("click", function () { if (rv.paused) play(rv); else rv.pause(); });
  $("#rs-snd").addEventListener("click", function () {
    rv.muted = !rv.muted; var b = $("#rs-snd");
    b.innerHTML = ICON(rv.muted ? "i-sound-off" : "i-sound-on"); b.setAttribute("aria-pressed", String(rv.muted)); b.setAttribute("aria-label", rv.muted ? "Unmute" : "Mute");
  });
  function seekTo(t) {
    if (mode !== "after") setMode("after");
    var go2 = function () { rv.currentTime = Math.max(0, t - 0.4); play(rv); };
    if (rv.readyState >= 1) go2(); else rv.addEventListener("loadedmetadata", go2, { once: true });
  }
  document.addEventListener("click", function (e) { var s = e.target.closest("[data-seek]"); if (s) seekTo(+s.dataset.seek); });
  var scrub = $("#rs-scrub"), dragging = false;
  function scrubAt(x) { var r = scrub.getBoundingClientRect(), p = Math.min(1, Math.max(0, (x - r.left) / r.width)); if (rv.duration) rv.currentTime = p * rv.duration; }
  scrub.addEventListener("pointerdown", function (e) { dragging = true; scrub.setPointerCapture(e.pointerId); scrubAt(e.clientX); });
  scrub.addEventListener("pointermove", function (e) { if (dragging) scrubAt(e.clientX); });
  scrub.addEventListener("pointerup", function () { dragging = false; });
  scrub.addEventListener("keydown", function (e) { if (e.key === "ArrowRight") { rv.currentTime += 2; e.preventDefault(); } if (e.key === "ArrowLeft") { rv.currentTime -= 2; e.preventDefault(); } });
  function setMode(m) {
    mode = m;
    $$("[data-ba]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.ba === m)); });
    var wasPlaying = !rv.paused;
    var hs = window.CRHosted && window.CRHosted.on && window.CRHosted.media(m); // a real edit's own files
    rv.src = hs ? hs.src : m === "after" ? cut().video : "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.mp4";
    rv.poster = hs ? hs.poster : m === "after" ? cut().poster : "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.jpg";
    $("#rs-tag-t").textContent = m === "after" ? "Your Reel · v" + S.version : "Raw take · " + fmt(hs ? hs.seconds : demo.raw_seconds);
    $("#rs-tag").classList.toggle("good", m === "after");
    if (wasPlaying) play(rv);
    upd();
  }
  $$("[data-ba]").forEach(function (b) { b.addEventListener("click", function () { if (b.dataset.ba !== mode) setMode(b.dataset.ba); }); });
  $("#rs-dl").addEventListener("click", function () { toast(window.CRHosted && window.CRHosted.on ? "Downloading your Reel." : "Downloading your Reel (prototype: a 720p preview file)."); if (!S.heard) { clearTimeout(heardTimer); heardTimer = setTimeout(openHeard, 1500); } });
  $("#rs-again").addEventListener("click", function () { newReel(); });   // straight to the look, then the upload
  $("#rs-relook").addEventListener("click", function () {                 // the same take, in another look
    var H = window.CRHosted, j = H && H.on && H.job, r = j && j.result;
    newReel(j && j.take_key ? { reuse: { key: j.take_key, name: j.take_name, url: r && r.take_url, seconds: r && r.summary && r.summary.take_seconds } } : {});
  });
  // tweak chat
  var msgs = $("#rs-msgs");
  function say(who, html) { var m = document.createElement("div"); m.className = "msg " + who; m.innerHTML = html; msgs.appendChild(m); msgs.scrollTop = msgs.scrollHeight; return m; }
  var REPLIES = [
    [/bigger|larger|size/i, "Making the captions about 15% bigger and keeping them below your eyes."],
    [/zoom|punch/i, "Calming the punch-ins: only on jump cuts, and at most 1.15×."],
    [/yellow|colou?r|red|white|green|blue/i, "Switching the caption colour and keeping the same font."],
    [/first take|keep my|intro|start/i, "Bringing back your first take of the hook and trimming the pause after it."],
    [/face|less b-?roll|cutaway/i, "Keeping you on screen: the B-roll becomes small cards over your shoulder, never a full cutaway."],
    [/music|song|beat|sound/i, "Trying a different bed in the same mood, still ducked under your voice."],
    [/cinematic|story/i, "Re-cutting it in Cinematic story: framed take, title cards, a score that swells in your pauses."]
  ];
  function newVersion(text) {
    var m = say("cr", esc(text) + '<div class="bar2"><i></i></div>');
    requestAnimationFrame(function () { m.querySelector("i").style.width = "100%"; });
    setTimeout(function () {
      S.version += 1; save();
      var vb = document.createElement("button"); vb.type = "button"; vb.dataset.v = String(S.version); vb.textContent = "v" + S.version;
      $("#rs-vers").appendChild(vb);
      $$("#rs-vers button").forEach(function (b) { b.setAttribute("aria-pressed", String(b === vb)); });
      $("#rs-tag-t").textContent = "Your Reel · v" + S.version;
      m.innerHTML = esc(text) + "<br><b>v" + S.version + " is ready.</b> v1 is still there if you want it back.";
      toast("v" + S.version + " is ready (prototype: same preview file).");
    }, 2600);
  }
  $("#rs-vers").addEventListener("click", function (e) { var b = e.target.closest("button"); if (!b) return; $$("#rs-vers button").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); }); $("#rs-tag-t").textContent = "Your Reel · v" + b.dataset.v; });
  function ask(text) {
    text = text.trim(); if (!text) return;
    say("me", esc(text));
    var reply = REPLIES.filter(function (r) { return r[0].test(text); })[0];
    setTimeout(function () { newVersion(reply ? reply[1] : "On it: “" + text + "”. I'll keep everything else the same."); }, 450);
  }
  $("#rs-chips").addEventListener("click", function (e) { var c = e.target.closest(".chip"); if (c) ask(c.textContent); });
  $("#rs-form").addEventListener("submit", function (e) { e.preventDefault(); var i = $("#rs-input"); if (!i.value.trim()) { i.focus(); return; } ask(i.value); i.value = ""; });

  // heard-about sheet
  function openHeard() { if (current !== "result" || S.heard || openOverlay) return; $("#heard").classList.add("open"); }
  function closeHeard() { $("#heard").classList.remove("open"); clearTimeout(heardTimer); }
  $("[data-close-heard]").addEventListener("click", function () { S.heard = "skipped"; save(); closeHeard(); });
  function heardPick(v) {
    if (v === "creator") { $("#heard-handle").hidden = false; $("#heard-at").focus(); return; }
    $("#heard-handle").hidden = true; S.heard = v; save(); closeHeard(); toast("Thanks, that helps a lot.");
  }
  $("#heard-handle").addEventListener("submit", function (e) {
    e.preventDefault(); var h = $("#heard-at").value.trim().replace(/^@/, "");
    if (!h) { $("#heard-at").focus(); return; }
    S.heard = "creator:@" + h; save(); closeHeard(); toast("Thanks! We'll thank @" + h + ".");
  });

  // what hosted.js (the real backend) needs from the prototype
  var CRApp = window.CRApp = { S: S, save: save, go: go, replace: replace, newReel: newReel, toast: toast, openPlans: openPlans, styleLabel: styleLabel, STYLES: STYLES,
    fmt: fmt, esc: esc, ICON: ICON, play: play, startState: startState, drawFile: drawFile, openModal: openModal, closeModal: closeModal, example: EXAMPLE.url, preview: function () { if (current === "onboard") preview(stepNow); }, current: function () { return current; }, file: null, cuts: CUTS, cut: cut };
  route();
})();
