/* The creators home (#home) and Styles (#styles): where a signed-in creator lands once they've made a Reel.
   Decided from the category's evidence (big-frontier/dashboard-research.md, "Recommendation"):
   - Home = what's cooking ("Now editing", a real progress bar and minutes left), then "Your Reels" newest first,
     grouped Today / Yesterday / Earlier. One take = one card; its notes rounds are versions (v2, v3) inside it.
   - Phones get a bottom bar (Home · New Reel · Styles); desktops a left sidebar and a grid of 9:16 cards.
   - "N free Reels left" is a quiet pill that opens the plans (never a dead button); no banners.
   Data: GET /api/creators/projects through hosted.js. Without the backend (the static preview) the prototype's own
   demo edit is the one Reel. app.js routes here (#home, #styles) and lends window.CRApp. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var root = $('[data-screen="dash"]');
  if (!root) return;
  function A() { return window.CRApp; }
  function H() { var h = window.CRHosted; return h && h.hosted ? h : null; }   // the backend is there
  function esc(s) { return A().esc(s == null ? "" : s); }
  function icon(id) { return A().ICON(id); }

  var LIVE = { queued: 1, editing: 1, making: 1 };
  var view = "home", list = null, failed = false, active = false, pollT = null, looks = null, wasLive = {}, busy = false;
  var baseTitle = document.title;

  // ---------- data ----------
  function demoList() {   // the static preview: the prototype's demo edit, once one was made
    var a = A(), S = a.S;
    if (!S.demoAt) return [];
    var c = a.cut(), n = Math.max(1, S.version || 1), look = a.styleLabel();
    return [{ id: "demo", title: S.demoTitle || "Our sample take, edited", look: S.style || "energy",
      look_name: look.charAt(0).toUpperCase() + look.slice(1), state: "ready", version: n, ready_version: n,
      versions: [], poster_url: c.poster, download_url: c.video, open_id: "demo", latest_id: "demo",
      created_at: S.demoAt, updated_at: S.demoAt }];
  }
  function fetchList() { var h = H(); return h ? h.loadProjects() : Promise.resolve(demoList()); }
  function find(id) { return (list || []).filter(function (p) { return p.id === id; })[0]; }
  function lastLook() { var l = (list || [])[0]; return (l && l.look) || A().S.lastLook || null; }

  // ---------- render: home ----------
  function dayOf(t) {
    var now = new Date(), start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    return t >= start ? "Today" : t >= start - 86400 ? "Yesterday" : "Earlier";
  }
  function when(t, day) {
    var s = Date.now() / 1000 - t, d = new Date(t * 1000);
    if (day === "Today") return s < 60 ? "Just now" : s < 3600 ? Math.floor(s / 60) + " min ago" : Math.floor(s / 3600) + " h ago";
    if (day === "Yesterday") return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }
  function thumb(p, live) {
    if (p.poster_url) return '<span class="thumb"><img src="' + esc(p.poster_url) + '" alt="" loading="lazy"></span>';
    var S = A().S, local = live && S.clip && S.clip.url && !S.clip.sample ? S.clip.url : "";
    var src = local || (live ? p.take_url : "");
    if (src) return '<span class="thumb"><video muted playsinline preload="metadata" src="' + esc(src) + '#t=0.5"></video></span>';
    return '<span class="thumb th-empty">' + icon("i-film") + "</span>";
  }
  function chip(p) {
    if (p.state === "ready") return '<span class="chip-s ok">Ready</span>';
    if (p.state === "making") return '<span class="chip-s ac">Making v' + p.version + "</span>";
    if (p.state === "queued") return '<span class="chip-s">In line</span>';
    if (p.state === "editing") return '<span class="chip-s ac">Editing</span>';
    return '<span class="chip-s bad">' + (p.ready_version ? "v" + p.version + " didn't finish" : "Didn't finish") + "</span>";
  }
  function reelCard(p, day) {
    var badge = p.ready_version > 1 ? '<span class="vbadge">v' + p.ready_version + "</span>" : "";
    var fail = p.state === "failed";
    return '<article class="reel' + (fail && !p.ready_version ? " is-failed" : "") + '" data-id="' + esc(p.id) + '">' + thumb(p, false) +
      '<div class="rbody"><h3 class="rt"><button type="button" class="ropen" data-open-p="' + esc(p.id) + '">' + esc(p.title) + "</button></h3>" +
      '<p class="rmeta">' + esc(p.look_name) + " · " + esc(when(p.updated_at, day)) + "</p>" +
      '<p class="rstat">' + chip(p) + badge + "</p>" +
      (fail ? '<p class="rnote">Nothing was charged.</p>' : "") +
      (fail && !p.ready_version ? '<button type="button" class="btn btn-sm btn-ghost retry" data-retry="' + esc(p.id) + '">Try again</button>' : "") +
      '</div><button type="button" class="rmore" data-more="' + esc(p.id) + '" aria-haspopup="menu" aria-label="More for ' + esc(p.title) + '">' + icon("i-more") + "</button></article>";
  }
  function nowCard(p) {
    var pct = Math.round((p.progress || 0) * 100);
    var line = p.state === "queued" ? (p.in_line > 1 ? "In line: " + (p.in_line - 1) + " ahead of you" : "Next in line")
      : pct + "%" + (p.minutes_left ? " · about " + p.minutes_left + " min left" : p.eta_note === "late" ? " · taking a little longer than usual"
        : p.eta_note === "almost" ? " · almost done" : "");
    var what = (p.state === "making" ? "Making v" + p.version + " · " : "") + p.look_name;
    var note = p.notify ? "Editing on our servers. We'll email you when it's ready." : "Editing on our servers. You can close this tab.";
    return '<article class="now-card" data-id="' + esc(p.id) + '">' + thumb(p, true) +
      '<div class="nbody"><h3 class="rt"><button type="button" class="ropen" data-open-job="' + esc(p.latest_id) + '" data-v="' + p.version + '">' + esc(p.title) + "</button></h3>" +
      '<p class="rmeta">' + esc(what) + "</p>" +
      '<div class="nbar" role="progressbar" aria-label="Edit progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '"><i style="width:' + Math.max(3, pct) + '%"></i></div>' +
      '<p class="nline tnum">' + esc(line) + '</p><p class="nnote">' + esc(note) + "</p></div></article>";
  }
  function uploadCard(u) {
    var pct = Math.round((u.frac || 0) * 100);
    return '<article class="now-card">' + '<span class="thumb th-empty">' + icon("i-upload") + "</span>" +
      '<div class="nbody"><h3 class="rt"><button type="button" class="ropen" data-upload>' + esc(u.name || "Your take") + "</button></h3>" +
      '<p class="rmeta">Uploading your take</p>' +
      '<div class="nbar" role="progressbar" aria-label="Upload progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '"><i style="width:' + Math.max(3, pct) + '%"></i></div>' +
      '<p class="nline tnum">' + pct + '% uploaded</p><p class="nnote">Keep this tab open until it\'s uploaded.</p></div></article>';
  }
  function skeleton() {
    var row = '<article class="reel sk" aria-hidden="true"><span class="thumb"></span><div class="rbody"><i class="sk-l" style="width:72%"></i><i class="sk-l" style="width:44%"></i><i class="sk-l" style="width:26%"></i></div></article>';
    return '<div class="reel-list">' + row + row + row + "</div>";
  }
  function render() {
    var up = H() && H().upload, ps = list || [];
    var live = ps.filter(function (p) { return LIVE[p.state]; });
    var lib = ps.filter(function (p) { return !(LIVE[p.state] && !p.ready_version); });   // a first edit still cooking sits in Now editing only
    var now = live.map(nowCard);
    if (up) now.unshift(uploadCard(up));
    $("#now").hidden = !now.length;
    $("#now-list").innerHTML = now.join("");
    var reels = $("#reels");
    if (list === null) {
      $("#empty").hidden = true; $("#reels-sec").hidden = false;
      reels.innerHTML = failed ? '<div class="load-err"><p>We couldn\'t load your Reels.</p><button type="button" class="btn btn-sm btn-ghost" data-reload>Try again</button></div>' : skeleton();
      return;
    }
    $("#empty").hidden = !!(lib.length || now.length);
    $("#reels-sec").hidden = !lib.length;
    var groups = [], by = {};
    lib.forEach(function (p) { var d = dayOf(p.updated_at); if (!by[d]) { by[d] = []; groups.push(d); } by[d].push(p); });
    reels.innerHTML = groups.map(function (d) {
      return '<h2 class="day-h">' + d + '</h2><div class="reel-list">' + by[d].map(function (p) { return reelCard(p, d); }).join("") + "</div>";
    }).join("");
  }

  // ---------- header, nav ----------
  function drawHeader() {
    var h = H(), S = A().S, me = h && h.me;
    var admin = !!(me && me.user && me.user.admin);
    var left = h ? (me ? me.free_left : null) : S.freeLeft;
    var show = !admin && typeof left === "number" && left < 99;
    var text = left > 0 ? left + " free Reel" + (left === 1 ? "" : "s") + " left" : "No free Reels left";
    $$("[data-left]", root).forEach(function (b) { b.hidden = !show; });
    $$("[data-left-t]", root).forEach(function (t) { t.textContent = text; });
    var mail = (me && me.user && me.user.email) || S.email || "";
    $$("[data-avatar]", root).forEach(function (a) { a.textContent = (mail || "Y").charAt(0).toUpperCase(); });
    $$("[data-mail]", root).forEach(function (m) { m.textContent = mail || "Your account"; });
  }
  function drawNav() {
    $$("[data-nav]", root).forEach(function (a) { if (a.dataset.nav === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
    $$(".dash-view", root).forEach(function (v) { v.hidden = v.dataset.view !== view; });
    $("#dash").dataset.view = view;
    document.title = (view === "styles" ? "Styles" : "Your Reels") + " · ChannelRecipe";
  }

  // ---------- refresh + polling (only while something is cooking and the home is on screen) ----------
  function refresh() {
    clearTimeout(pollT);
    return fetchList().then(function (ps) {
      ps.forEach(function (p) { if (wasLive[p.id] && p.state === "ready" && active) A().toast("“" + p.title + "” is ready."); });
      wasLive = {}; ps.forEach(function (p) { if (LIVE[p.state]) wasLive[p.id] = 1; });
      list = ps; failed = false;
    }).catch(function () { failed = true; }).then(function () { if (active) { render(); schedule(); } });
  }
  function schedule() {
    clearTimeout(pollT);
    var h = H();
    if (!active || view !== "home" || !h) return;
    if (h.upload || (list || []).some(function (p) { return LIVE[p.state]; })) pollT = setTimeout(refresh, h.upload ? 3000 : 4000);
  }

  // ---------- actions ----------
  function openJob(id, screen, version, el) {
    var h = H();
    if (!h) { A().go(screen === "review" ? "review" : "result"); return; }
    if (busy) return;
    busy = true; if (el) el.setAttribute("aria-busy", "true");
    h.openJob(id, screen, version).then(function () { busy = false; if (el) el.removeAttribute("aria-busy"); });
  }
  function openProject(p, el) {
    if (!p) return;
    if (!p.ready_version) return openJob(p.latest_id, "editing", Math.max(1, p.version), el);   // still cooking, or it stopped: show what happened
    openJob(p.open_id, "result", p.ready_version, el);
  }
  function rename(p) {
    A().openModal("rename", function (ov) {
      var input = $("#rn-input", ov);
      input.value = p.title;
      setTimeout(function () { input.select(); }, 30);
      $("#rn-form", ov).addEventListener("submit", function (e) {
        e.preventDefault();
        var t = input.value.replace(/\s+/g, " ").trim();
        var done = function (q) { p.title = (q && q.title) || t || p.title; render(); A().closeModal(); A().toast("Renamed."); };
        if (!H()) { A().S.demoTitle = t; A().save(); return done(); }
        $("#rn-save", ov).disabled = true;
        H().api("/api/creators/projects/" + encodeURIComponent(p.id), { method: "PATCH", body: { title: t } })
          .then(done).catch(function (err) { $("#rn-save", ov).disabled = false; A().toast(err.message); });
      });
    });
  }
  function remove(p) {
    A().openModal("delete", function (ov) {
      $("#dl-sub", ov).textContent = "“" + p.title + "” goes from Your Reels" + (p.ready_version > 1 ? ", with all " + p.ready_version + " versions." : ".");
      $("#dl-go", ov).addEventListener("click", function () {
        var b = this;
        var done = function () {
          list = (list || []).filter(function (x) { return x.id !== p.id; });
          if (H()) { H().list = list; H().forget((p.versions || []).map(function (v) { return v.id; }).concat([p.latest_id])); }
          render(); A().closeModal(); A().toast("Deleted.");
        };
        if (!H()) { A().S.demoAt = null; A().save(); return done(); }
        b.disabled = true;
        H().api("/api/creators/projects/" + encodeURIComponent(p.id), { method: "DELETE" })
          .then(done).catch(function (err) { b.disabled = false; A().closeModal(); A().toast(err.message); });
      });
    });
  }
  function menu(anchor, title, items) {
    A().openModal("menu", function (ov) {
      ov.classList.add("menu-ov");
      var m = $(".menu", ov);
      $(".menu-h", m).textContent = title;
      $(".menu-items", m).innerHTML = items.map(function (it, k) {
        if (it.note) return '<p class="menu-note">' + esc(it.note) + "</p>";
        var inner = icon(it.icon) + "<span>" + esc(it.label) + "</span>";
        return it.href ? '<a role="menuitem" class="menu-item" href="' + esc(it.href) + '" data-k="' + k + '">' + inner + "</a>"
          : '<button type="button" role="menuitem" class="menu-item' + (it.danger ? " danger" : "") + '" data-k="' + k + '">' + inner + "</button>";
      }).join("");
      m.addEventListener("click", function (e) {
        var b = e.target.closest("[data-k]"); if (!b) return;
        var it = items[+b.dataset.k];
        A().closeModal();
        if (it.run) it.run();
      });
      if (matchMedia("(min-width: 640px)").matches) {   // a popover by its button; phones get a sheet from the bottom
        var r = anchor.getBoundingClientRect(), mw = m.offsetWidth, mh = m.offsetHeight;
        var top = r.bottom + 6 + mh > innerHeight - 8 ? Math.max(8, r.top - mh - 6) : r.bottom + 6;
        m.style.left = Math.round(Math.min(innerWidth - mw - 8, Math.max(8, r.right - mw))) + "px";
        m.style.top = Math.round(top) + "px";
      }
    });
  }
  function reelMenu(p, anchor) {
    var items = [], live = LIVE[p.state];
    if (p.download_url) items.push({ icon: "i-download", label: "Download", href: p.download_url, run: function () { A().toast("Downloading your Reel."); } });
    if (p.ready_version && !live) items.push({ icon: "i-note", label: "Give notes", run: function () { openJob(p.open_id, "review", p.ready_version); } });
    if (!p.ready_version && p.state === "failed") items.push({ icon: "i-upload", label: "Try again", run: function () { A().newReel({ look: p.look, step: 5 }); } });
    items.push({ icon: "i-pencil", label: "Rename", run: function () { rename(p); } });
    if (!live) items.push({ icon: "i-trash", label: "Delete", danger: true, run: function () { remove(p); } });
    menu(anchor, p.title, items);
  }
  function accountMenu(anchor) {
    var h = H(), mail = (h && h.me && h.me.user && h.me.user.email) || A().S.email || "";
    menu(anchor, "Your account", [{ note: mail ? "Signed in as " + mail : "Prototype account" },
      { icon: "i-logout", label: "Log out", run: function () {
        if (h) return h.logout();
        try { sessionStorage.clear(); } catch (e) {}
        location.hash = "signup"; location.reload();
      } }]);
  }
  function plans() {
    var h = H(), left = h && h.me ? h.me.free_left : A().S.freeLeft;
    A().openPlans(left > 0 ? "You have " + left + " free Reel" + (left === 1 ? "" : "s") + " left. Every plan keeps everything you've seen."
      : "You've used your free Reels. Every plan keeps everything you've seen.");
  }

  // ---------- styles ----------
  function drawStyles() {
    if (looks) return paintLooks();
    fetch("looks.json").then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; })
      .then(function (l) { looks = l || []; if (view === "styles") paintLooks(); });
  }
  function paintLooks() {
    if ($("#look-grid .look-card")) return markLast();
    $("#look-grid").innerHTML = looks.map(function (l) {
      return '<article class="look-card"><button type="button" class="lvid" data-key="' + esc(l.key) + '" aria-label="Play the ' + esc(l.name) + ' look">' +
        '<img src="' + esc(l.poster) + '" alt="" loading="lazy"><video muted playsinline loop preload="none" src="' + esc(l.clip) + '"></video>' +
        '<span class="ltag" hidden>Last used</span><span class="lplay">' + icon("i-play") + "</span></button>" +
        "<b>" + esc(l.name) + '</b><span class="lline">' + esc(l.line) + "</span>" +
        '<button type="button" class="btn btn-sm btn-ghost btn-block" data-use="' + esc(l.key) + '">Use this look</button></article>';
    }).join("");
    $$(".lvid", root).forEach(function (b) {
      b.addEventListener("mouseenter", function () { if (matchMedia("(hover: hover)").matches) { b.hoverAt = Date.now(); playLook(b, true); } });
      b.addEventListener("mouseleave", function () { if (matchMedia("(hover: hover)").matches) playLook(b, false); });
    });
    markLast();
  }
  function markLast() { var last = lastLook(); $$(".lvid", root).forEach(function (b) { $(".ltag", b).hidden = b.dataset.key !== last; }); }
  function playLook(b, on) {
    var v = $("video", b);
    if (on) { $$(".lvid.playing", root).forEach(function (o) { if (o !== b) playLook(o, false); }); v.preload = "auto"; A().play(v); }
    else v.pause();
    b.classList.toggle("playing", on);
  }

  // ---------- events ----------
  root.addEventListener("click", function (e) {
    var t = e.target;
    var el;
    if ((el = t.closest("[data-new]"))) return A().newReel();
    if ((el = t.closest("[data-open-p]"))) return openProject(find(el.dataset.openP), el.closest("article"));
    if ((el = t.closest("[data-open-job]"))) return openJob(el.dataset.openJob, "editing", +el.dataset.v || 1, el.closest("article"));
    if ((el = t.closest("[data-upload]"))) return A().go("step-5");
    if ((el = t.closest("[data-more]"))) return reelMenu(find(el.dataset.more), el);
    if ((el = t.closest("[data-retry]"))) { var p = find(el.dataset.retry); return A().newReel({ look: p && p.look, step: 5 }); }
    if ((el = t.closest("[data-left]"))) return plans();
    if ((el = t.closest("[data-acct]"))) return accountMenu(el);
    if ((el = t.closest("[data-reload]"))) { failed = false; render(); return refresh(); }
    if ((el = t.closest("[data-use]"))) return A().newReel({ look: el.dataset.use, step: 5 });
    if ((el = t.closest("#learn-card"))) return A().newReel({ paste: true });
    if ((el = t.closest(".lvid"))) { if (Date.now() - (el.hoverAt || 0) < 500) return; return playLook(el, !el.classList.contains("playing")); }   // a hover just started it
  });
  document.addEventListener("visibilitychange", function () { if (!document.hidden && active && view === "home" && H()) refresh(); });

  // ---------- in / out (app.js's router) ----------
  function enter(v) {
    view = v === "styles" ? "styles" : "home";
    active = true;
    document.body.classList.add("on-dash");
    var h = H();
    if (list === null && h && h.list) list = h.list;   // fetched while the page loaded
    drawHeader(); drawNav();
    if (view === "home") { render(); refresh(); if (h && h.refreshMe) h.refreshMe().then(drawHeader).catch(function () {}); }
    else {
      clearTimeout(pollT); $$("video", $("#now-list")).forEach(function (x) { x.pause(); }); drawStyles();
      if (list === null) fetchList().then(function (ps) { list = ps; if (view === "styles" && looks) paintLooks(); }).catch(function () {});   // for "Last used"
    }
  }
  function leave() {
    active = false; clearTimeout(pollT);
    $$("video", root).forEach(function (v) { v.pause(); });
    $$(".lvid.playing", root).forEach(function (b) { b.classList.remove("playing"); });
    document.body.classList.remove("on-dash");
    document.title = baseTitle;
  }
  window.CRHome = { enter: enter, leave: leave, refresh: function () { if (active) return refresh(); },
    upload: function () { if (active && view === "home") { render(); schedule(); } }, list: function () { return list; } };
  // app.js routes before this file runs: a page opened on #home or #styles starts here
  if (A() && A().current() === "dash") enter(location.hash.slice(1));
})();
