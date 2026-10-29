/* /creators landing page behaviour. */
(function () {
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function play(v) { var p = v && v.play(); if (p && p.catch) p.catch(function () {}); }

  var nav = $("#nav");
  function navState() { nav.classList.toggle("solid", window.scrollY > 8); }
  window.addEventListener("scroll", navState, { passive: true }); navState();

  // Muted loops play only while on screen (preload=none keeps the first paint light).
  var vids = $$("video[data-auto]");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { var v = e.target; if (e.isIntersecting) { v.preload = "auto"; if (!v.dataset.hold) play(v); } else if (!v.paused && v.muted) v.pause(); });
    }, { threshold: 0.3 });
    vids.forEach(function (v) { io.observe(v); });
  }
  // The launch film: a muted loop once it's on screen; "Watch with sound" (or a click on it) starts it over with sound.
  var film = $("#film"), fv = $("#film-video"), fplay = $("#film-play");
  if (film && fv) {
    var fsrc = function () { return (fv.clientWidth || 960) * (window.devicePixelRatio || 1) >= 1500 ? fv.dataset.hd : fv.dataset.sd; };
    var fload = function () { if (!fv.getAttribute("src")) { fv.src = fsrc(); fv.preload = "auto"; } };
    var withSound = function () {
      fload(); film.classList.add("sound");
      fv.loop = false; fv.muted = false; fv.controls = true; fv.currentTime = 0; play(fv);
    };
    fplay.addEventListener("click", withSound);
    fv.addEventListener("click", function () { if (!film.classList.contains("sound")) withSound(); });
    if (!reduce && "IntersectionObserver" in window) {
      var fio = new IntersectionObserver(function (es) {
        es.forEach(function (e) {
          if (film.classList.contains("sound")) return;
          if (e.isIntersecting) { fload(); fv.muted = true; fv.loop = true; play(fv); } else if (!fv.paused) fv.pause();
        });
      }, { threshold: 0.35 });
      fio.observe(fv);
    }
  }

  // The jobs an evening of editing goes to, struck through as they scroll in.
  var chores = $(".chores");
  if (chores) {
    if (reduce || !("IntersectionObserver" in window)) chores.classList.add("in");
    else { var io3 = new IntersectionObserver(function (es) { if (es[0].isIntersecting) { chores.classList.add("in"); io3.disconnect(); } }, { threshold: 0.35 }); io3.observe(chores); }
  }
  $$(".play-on").forEach(function (b) { b.addEventListener("click", function () { var v = b.parentElement.querySelector("video"); if (v.paused) { v.preload = "auto"; play(v); } else v.pause(); }); });

  // Hero: a creator's raw take and the same take in a look. Both looks are real edits (creator-edit-work/v2).
  var LOOKS = {
    story: { raw: ["https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/s_raw.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/s_raw.jpg", "Raw · 0:54"], edit: ["https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/s_edit.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/s_edit.jpg", "Cinematic story · 0:50"],
      spec: [["i-type", "Type", "Heavy sans for the big lines, serif italic for the feeling"], ["i-music", "Music", "An emotional score that stays under your voice and swells in your pauses"],
             ["i-wave", "Sound", "A deep hit on each title card, a build into your last line"], ["i-film", "B-roll", "Film-grain shots on every reason you give"]] },
    energy: { raw: ["https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_raw.jpg", "Raw · 0:53"], edit: ["https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_edit.mp4", "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/e_edit.jpg", "Creator energy · 0:51"],
      spec: [["i-type", "Type", "Bold captions, yellow on the words that land"], ["i-music", "Music", "A playful groove, always under your voice"],
             ["i-wave", "Sound", "A pop when the ticket stamps, a hit on the last word"], ["i-motion", "Motion", "A hook card, punch-ins on punchlines, an order ticket"]] }
  };
  var look = "story", mobileShow = "edit";
  var raw = $("#raw-video"), reel = $("#reel-video"), sound = $("#reel-sound"), hear = $("#hear-it");
  function setSrc(v, m) { if (v.getAttribute("src") !== m[0]) { var muted = v.muted; v.poster = m[1]; v.src = m[0]; v.muted = muted; } }
  function setSound(on) {
    reel.muted = !on;
    sound.setAttribute("aria-pressed", String(on)); sound.setAttribute("aria-label", on ? "Turn sound off" : "Turn sound on");
    sound.querySelector("use").setAttribute("href", on ? "icons.svg#i-sound-on" : "icons.svg#i-sound-off");
  }
  function showLook(k) {
    look = k; var L = LOOKS[k];
    $$(".look-tabs button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.look === k)); });
    $("#look-spec").innerHTML = L.spec.map(function (r) { return '<li><span class="ic"><svg><use href="icons.svg#' + r[0] + '"/></svg></span><span><b>' + r[1] + "</b>" + r[2] + "</span></li>"; }).join("");
    setSrc(raw, L.raw); $("#raw-tag").textContent = L.raw[2];
    var m = mobile() && mobileShow === "raw" ? L.raw : L.edit;
    setSrc(reel, m); var tag = $("#reel-tag"); tag.lastElementChild.textContent = m[2]; tag.classList.toggle("good", m === L.edit);
    if (!reduce) { play(raw); play(reel); }
  }
  function mobile() { return window.matchMedia("(max-width: 900px)").matches; }
  $$(".look-tabs button").forEach(function (b) { b.addEventListener("click", function () { if (b.dataset.look !== look) showLook(b.dataset.look); }); });
  showLook("story");
  sound.addEventListener("click", function () { var on = reel.muted; if (on) { raw.muted = true; reel.preload = "auto"; play(reel); } setSound(on); });
  var hearTimer = null;
  function hearLabel(t) { hear.lastChild.textContent = t; }
  hear.addEventListener("click", function () {
    clearTimeout(hearTimer);
    $(".stage").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    if (mobile()) showOnMobile("raw");
    setSound(false); reel.pause();
    raw.preload = "auto"; raw.currentTime = 0; raw.muted = false; play(raw); hearLabel("Playing the raw take…");
    hearTimer = setTimeout(function () {
      raw.muted = true;
      if (mobile()) showOnMobile("edit");
      reel.preload = "auto"; reel.currentTime = 0; setSound(true); play(reel); hearLabel("Playing the edit…");
      hearTimer = setTimeout(function () { hearLabel("Hear the before and after"); }, 12000);
    }, 6000);
  });
  // Mobile: one phone with a Raw / Reel toggle.
  function showOnMobile(which) {
    mobileShow = which;
    $$(".stage-toggle button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.show === which)); });
    var m = LOOKS[look][which === "raw" ? "raw" : "edit"];
    setSrc(reel, m); play(reel);
    var tag = $("#reel-tag"); tag.lastElementChild.textContent = m[2]; tag.classList.toggle("good", which !== "raw");
  }
  $$(".stage-toggle button").forEach(function (b) { b.addEventListener("click", function () { showOnMobile(b.dataset.show); }); });

  // The real mix of the story Reel: voice and bed loudness every 0.2 s, and each effect where it hits.
  var sv = $("#soundviz");
  if (sv) fetch("media/local/sound_story.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (!d) return;
    function area(vals) {                // each track on its own scale, so the dips and swells show
      var sorted = vals.slice().sort(function (a, b) { return a - b; }), lo = sorted[Math.floor(sorted.length * 0.04)], hi = sorted[sorted.length - 1];
      var n = vals.length, pts = vals.map(function (v, k) { var h = Math.max(0.03, Math.min(1, (v - lo) / Math.max(1, hi - lo))); return [k / (n - 1) * 1000, 50 - h * 48]; });
      var top = pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
      var bot = pts.slice().reverse().map(function (p) { return p[0].toFixed(1) + "," + (100 - p[1]).toFixed(1); }).join(" L");
      return '<svg viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true"><path d="M' + top + " L" + bot + ' Z"/></svg>';
    }
    var lastLabel = -99, fx = d.sfx.slice().sort(function (a, b) { return a.t - b.t; }).map(function (e) {
      var x = e.t / d.seconds * 100, lab = x - lastLabel > 7 ? "<span>" + e.kind + "</span>" : "";   // labels never collide
      if (lab) lastLabel = x;
      return '<i style="left:' + x.toFixed(2) + '%">' + lab + "</i>";
    }).join("");
    var ticks = [0, 10, 20, 30, 40, 49].map(function (t) { return "<span>0:" + String(t).padStart(2, "0") + "</span>"; }).join("");
    sv.innerHTML = '<div class="sv-row"><span>Voice</span><div class="sv-track sv-voice">' + area(d.voice) + "</div></div>" +
      '<div class="sv-row"><span>Music</span><div class="sv-track sv-bed">' + area(d.bed) + "</div></div>" +
      '<div class="sv-row"><span>Effects</span><div class="sv-fx">' + fx + "</div></div>" +
      '<div class="sv-axis">' + ticks + "</div>" +
      '<div class="sv-key"><span><i style="background:#C9C9D2"></i>Voice</span><span><i style="background:var(--accent)"></i>Music: dips when you talk, lifts in your pauses</span><span><i style="background:var(--ink)"></i>' + d.sfx.length + " effects in 50 s</span></div>";
  }).catch(function () {});

  // Beat by beat: real frames of each edit, the line said and what the edit did (media/local/beats.json).
  var beatsEl = $("#beats-list"), BEATS = null;
  function fmtT(t) { t = Math.floor(t); return "0:" + String(t).padStart(2, "0"); }
  function showBeats(k) {
    $$(".beat-tabs button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.beats === k)); });
    beatsEl.innerHTML = BEATS[k].map(function (b) {
      return '<li class="beat"><button type="button" data-look="' + k + '" data-t="' + b.t + '" aria-label="Play this beat at ' + fmtT(b.t) + '">' +
        '<span class="bf"><img src="' + b.img + '" alt="" loading="lazy"><span class="tchip">' + fmtT(b.t) + '</span><span class="bplay"><svg><use href="icons.svg#i-play"/></svg></span></span>' +
        '<q>' + b.said.replace(/[<>&]/g, "") + '</q><span class="did">' + b.did.replace(/[<>&]/g, "") + "</span></button></li>";
    }).join("");
    beatsEl.scrollLeft = 0;
  }
  if (beatsEl) fetch("media/local/beats.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d) { BEATS = d; showBeats("story"); } }).catch(function () {});
  $$(".beat-tabs button").forEach(function (b) { b.addEventListener("click", function () { if (BEATS) showBeats(b.dataset.beats); }); });
  // Tap a beat: the hero plays that moment of that edit, with sound.
  if (beatsEl) beatsEl.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-t]"); if (!b) return;
    var t = +b.dataset.t;
    if (b.dataset.look !== look) showLook(b.dataset.look);
    if (mobile()) showOnMobile("edit");
    $(".stage").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    raw.muted = true; reel.preload = "auto";
    var go = function () { reel.currentTime = Math.max(0, t - 0.6); setSound(true); play(reel); };
    if (reel.readyState >= 1) go(); else reel.addEventListener("loadedmetadata", go, { once: true });
  });

  // The mood board: three rows of real looks (media/local/looks.json). Every tile shows its poster; the ones on screen play.
  var board = $("#moodboard");
  if (board) fetch("media/local/looks.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (looks) {
    if (!looks) return;
    var rows = [[], [], []];
    looks.forEach(function (l, i) { rows[i % 3].push(l); });
    board.innerHTML = rows.map(function (row, k) {
      var tiles = row.concat(row).map(function (l) {   // doubled, so the loop has no seam
        return '<div class="mb-tile' + (l.ours ? " ours" : "") + '"><img src="https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/looks/' + l.slug + '.jpg" alt="" loading="lazy"><video muted playsinline loop preload="none" data-src="https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/looks/' + l.slug + '.mp4"></video><b>' + l.name + "</b></div>";
      }).join("");
      return '<div class="mb-row r' + (k + 1) + '">' + tiles + "</div>";
    }).join("");
    if (reduce || !("IntersectionObserver" in window)) return;
    var io2 = new IntersectionObserver(function (es) {
      es.forEach(function (e) { var v = e.target.querySelector("video"); if (!v) return;
        if (e.isIntersecting) { if (!v.src) v.src = v.dataset.src; play(v); } else if (!v.paused) v.pause(); });
    }, { root: null, threshold: 0.6 });
    $$(".mb-tile", board).forEach(function (t) { io2.observe(t); });
  }).catch(function () {});

  // Meters fill when they scroll into view.
  function fillIn(el) { $$("i[data-w]", el).forEach(function (i) { i.style.width = i.dataset.w + "%"; }); }
  $$("[data-meter]").forEach(function (el) {
    if (!("IntersectionObserver" in window)) return fillIn(el);
    var mo = new IntersectionObserver(function (es) { if (es[0].isIntersecting) { fillIn(el); mo.disconnect(); } }, { threshold: 0.4 });
    mo.observe(el);
  });

  // Caption looks cycle on the still.
  $$("[data-capstyles]").forEach(function (box) {
    var words = $$(".word", box), dots = $$(".style-dots i", box.parentElement), k = 0;
    if (reduce) return;
    setInterval(function () { k = (k + 1) % words.length; words.forEach(function (w, n) { w.classList.toggle("on", n === k); }); dots.forEach(function (d, n) { d.classList.toggle("on", n === k); }); }, 2200);
  });

  // Audio waves: an uneven raw take with a noisy floor vs an even, mastered voice.
  $$("[data-wave]").forEach(function (el) {
    var n = 70, h = "";
    for (var k = 0; k < n; k++) {
      var s = Math.abs(Math.sin(k * 0.55) * 0.6 + Math.sin(k * 1.7) * 0.4);
      var v = el.dataset.wave === "raw" ? (k % 11 < 3 ? 0.1 : 0.16 + s * (k % 7 === 0 ? 0.95 : 0.45)) : 0.4 + s * 0.5;
      h += '<i style="height:' + Math.round(Math.min(1, v) * 100) + '%"></i>';
    }
    el.innerHTML = h;
  });

  // Paste boxes: validate as you type, carry the link into the app.
  var BAD = "That isn't a TikTok, Reel or Short link. Open the video, tap Share, then Copy link.";
  $$("[data-paste]").forEach(function (box) {
    var form = $("form", box), input = $("input", box), field = $(".field", box), msg = $(".field-msg", box), ex = $("[data-example]", box);
    function show(state, text) { field.classList.toggle("bad", state === "bad"); field.classList.toggle("good", state === "good"); msg.className = "field-msg" + (state ? " " + state : ""); msg.textContent = text || ""; }
    input.addEventListener("input", function () {
      var v = input.value.trim(); if (!v) return show("", "");
      var r = window.CRReel.parse(v);
      if (r) show("good", (r.handle ? r.handle + " · " : "") + r.platform + ". We'll learn its edit.");
      else if (v.length > 12) show("bad", BAD); else show("", "");
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var v = input.value.trim(), r = v && window.CRReel.parse(v);
      if (!v) { show("bad", "Paste a link first, or drop in your raw clip instead."); input.focus(); return; }
      if (!r) { show("bad", BAD); input.focus(); return; }
      location.href = "app.html?ref=" + encodeURIComponent(r.url) + "#signup";
    });
    if (ex) ex.addEventListener("click", function () {
      input.value = "https://www.youtube.com/watch?v=aXwgg5Tj5Nc"; input.dispatchEvent(new Event("input"));
      var card = $("#learned"); card.classList.add("busy"); $("#learned-pill").textContent = "Learning…"; $("#learned-pill").className = "pill accent";
      setTimeout(function () { card.classList.remove("busy"); fillIn(card); $("#learned-pill").textContent = "Saved"; $("#learned-pill").className = "pill ok"; show("good", "That's a Short we learned. Press Learn it to start with this style."); }, 1300);
    });
  });

  var cl = $("[data-cutline]"); if (cl) window.CRCutline.mount(cl);
  window.CRPlans.mount($("#pricing"), { onChoose: function (p) { location.href = "app.html" + (p.id === "free" ? "" : "?plan=" + p.id) + "#signup"; } });
})();
