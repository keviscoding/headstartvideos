/* Real edits for the creator app (phase 1, 2026-10-30). Without the backend (the static preview, or editing still
   switched off) this does nothing and the prototype runs as before. With it (/api/creators/config says hosted):
   - sign-in is real: Google when the site has a client id, else an emailed code (the main site's own sign-in);
   - "Start my edit" uploads the take (and any clips of the creator's own) straight to storage with a signed link,
     then starts a job: one edit machine of ours does the work;
   - the editing screen shows that job's real stages, in the launch film's words, until the Reel is ready;
   - the result plays the real Reel, with Download and, on phones, Share (to Instagram, TikTok…).
   app.js calls in through window.CRHosted at those points; window.CRApp is what it lends us. */
(function () {
  "use strict";
  if (window.CRHosted) return; // already provided (a test double of the backend): leave it be
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var H = window.CRHosted = { on: false, hosted: false, settled: false, config: null, me: null, job: null };
  var JOB_KEY = "cr-creators-job";
  var own = []; // the creator's own B-roll files for this edit
  var pollTimer = null, uploading = false;

  function app() { return window.CRApp; }
  function toast(t) { if (app()) app().toast(t); }
  function api(path, opts) {
    opts = opts || {};
    return fetch(path, {
      method: opts.method || "GET", credentials: "same-origin",
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { var e = new Error(d.detail || "Something went wrong. Try again."); e.status = r.status; throw e; }
        return d;
      });
    });
  }
  function savedJob() { try { return localStorage.getItem(JOB_KEY) || ""; } catch (e) { return ""; } }
  function saveJob(id) { try { if (id) localStorage.setItem(JOB_KEY, id); else localStorage.removeItem(JOB_KEY); } catch (e) {} }

  // ---------- start: is the backend there, and who's signed in ----------
  function loadMe() {
    return api("/api/creators/me").then(function (me) {
      H.me = me;
      H.on = !!(me.user && me.allowed);
      var A = app();
      if (A && H.on) { A.S.freeLeft = me.free_left === null || me.free_left === undefined ? 99 : me.free_left; A.save(); }
      return me;
    });
  }
  // only pages the site serves carry <meta name="cr-backend"> (publish.py adds it): the static preview never asks
  var backend = document.querySelector('meta[name="cr-backend"]');
  H.ready = (backend ? api("/api/creators/config") : Promise.reject(new Error("no backend"))).then(function (cfg) {
    if (!cfg.hosted) return null;
    H.hosted = true; H.config = cfg;
    return loadMe().then(loadSavedJob).then(function () { setup(); return H; });
  }).catch(function () { return null; }) // the static preview: no backend, the prototype stays
    .then(function (h) { H.settled = true; return h; });

  // the creator's current edit, so any screen that waits for hosted.js (result, review) opens on it after a reload
  function loadSavedJob() {
    var id = savedJob();
    if (!H.on || !id || H.job) return null;
    return api("/api/creators/jobs/" + id).then(function (j) { H.job = j; }).catch(function (e) { if (e.status === 404) saveJob(""); });
  }
  function setup() {
    mountGoogle();
    var A = app();
    if (!A) return;
    var running = (H.me.jobs || []).filter(function (j) { return ["queued", "starting", "running"].indexOf(j.status) >= 0; })[0];
    var scr = A.current();
    if (H.on) {
      showHostedBits();
      if (running) { H.job = running; saveJob(running.id); if (scr !== "editing") A.go("editing"); else H.editing(); return; }
      if (scr === "signup") A.go(A.S.make ? "step-5" : "step-1");
      A.startState();
    } else if (H.me.user && !H.me.allowed) {
      toast("Real edits open soon. You can look around in the meantime.");
    }
  }
  function showHostedBits() {
    $("#own-clips").hidden = false;
    var proto = $(".proto span"); if (proto) proto.textContent = "Early access: real edits on our machines. Demo Reels belong to their creators.";
    ["#use-sample", '[data-open="qr"]', '[data-open="drive"]'].forEach(function (s) { var el = $(s); if (el) el.hidden = true; });
    var alt = $(".alt-row"); if (alt) alt.hidden = true;
    if (app()) app().preview();
  }

  // ---------- sign in ----------
  function mountGoogle() {
    var btn = $("#google-btn");
    if (!H.config.google_client_id) { btn.hidden = true; var or = $(".or-line"); if (or) or.hidden = true; return; }
    var s = document.createElement("script");
    s.src = "/static/google-signin.js";
    s.onload = function () {
      if (!window.CRGoogle) return;
      var slot = document.createElement("div"); slot.id = "google-slot"; slot.style.justifyContent = "center";
      btn.parentNode.insertBefore(slot, btn);
      window.CRGoogle.mount(slot, { width: Math.min(400, btn.offsetWidth || 320), onSignedIn: signedIn,
        onError: function (m) { toast(m); }, onShown: function () { btn.hidden = true; } });
    };
    document.head.appendChild(s);
  }
  function signedIn() {
    return loadMe().then(function (me) {
      var A = app();
      if (!me.allowed) { toast("You're signed in. Real edits open soon: we'll email you."); A.go("step-1"); return; }
      showHostedBits();
      toast("Signed in as " + me.user.email + ".");
      A.go(A.S.make ? "step-5" : "step-1");
    });
  }
  H.google = function () {
    if (!H.hosted) return false;
    toast("Sign in with your email below."); $("#email").focus();
    return true;
  };
  H.email = function (email) {
    if (!H.hosted) return false;
    var msg = $("#email-msg"), btn = $("#email-btn");
    btn.disabled = true;
    api("/api/auth/send-code", { method: "POST", body: { email: email } }).then(function () {
      msg.className = "field-msg good"; msg.textContent = "We sent a 6-digit code to " + email + ".";
      var row = $("#code-row");
      if (!row) {
        row = document.createElement("div"); row.className = "code-row"; row.id = "code-row";
        row.innerHTML = '<label class="sr-only" for="code">Code</label><input class="text-input" id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456"><button type="button" class="btn" id="code-btn">Sign in</button>';
        msg.parentNode.insertBefore(row, msg.nextSibling);
        var go = function () {
          var code = $("#code").value.trim();
          if (!/^\d{4,8}$/.test(code)) { msg.className = "field-msg bad"; msg.textContent = "Enter the code from the email."; return; }
          $("#code-btn").disabled = true;
          api("/api/auth/verify", { method: "POST", body: { email: email, code: code } }).then(signedIn).catch(function (e) {
            msg.className = "field-msg bad"; msg.textContent = e.status === 400 ? "That code didn't work. Check it, or send a new one." : e.message;
          }).then(function () { $("#code-btn").disabled = false; });
        };
        $("#code-btn").addEventListener("click", go);
        $("#code").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); go(); } });
      }
      $("#code").focus();
      btn.textContent = "Send a new code";
    }).catch(function (e) { msg.className = "field-msg bad"; msg.textContent = e.message; })
      .then(function () { btn.disabled = false; });
    return true;
  };

  // ---------- step 5: your own B-roll ----------
  function drawOwn() {
    var A = app();
    $("#own-list").innerHTML = own.map(function (f, k) {
      return '<span class="pill">' + A.esc(f.name) + '<button type="button" data-own-rm="' + k + '" aria-label="Remove ' + A.esc(f.name) + '">×</button></span>';
    }).join("");
  }
  document.addEventListener("change", function (e) {
    if (e.target.id !== "own-file") return;
    var max = (H.config && H.config.max_clips) || 10;
    Array.prototype.slice.call(e.target.files).forEach(function (f) {
      if (own.length >= max) return;
      if (!/^video\//.test(f.type) && !/\.(mov|mp4|m4v|webm)$/i.test(f.name)) return;
      if (f.size > 500 * 1048576) { toast(f.name + " is over 500 MB. Trim it first."); return; }
      own.push(f);
    });
    e.target.value = ""; drawOwn();
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-own-rm]"); if (!b) return;
    own.splice(+b.dataset.ownRm, 1); drawOwn();
  });

  // ---------- the phone check before the free edits (Twilio Verify on the server; off until switched on) ----------
  function askPhone() {
    var A = app();
    A.openModal("phone", function (ov) {
      var num = $("#ph-num", ov), msg = $("#ph-msg", ov), send = $("#ph-send", ov), row = $("#ph-code-row", ov);
      function say(t, bad) { msg.className = "field-msg" + (bad ? " bad" : " good"); msg.textContent = t; }
      $("#ph-form", ov).addEventListener("submit", function (e) {
        e.preventDefault(); send.disabled = true;
        api("/api/creators/phone/start", { method: "POST", body: { phone: num.value } }).then(function () {
          row.hidden = false; say("We texted a code to " + num.value + "."); send.textContent = "Send a new code"; $("#ph-code", ov).focus();
        }).catch(function (err) { say(err.message, true); }).then(function () { send.disabled = false; });
      });
      $("#ph-check", ov).addEventListener("click", function () {
        api("/api/creators/phone/check", { method: "POST", body: { phone: num.value, code: $("#ph-code", ov).value } }).then(function () {
          H.me.phone_verified = true; A.closeModal(); toast("Thanks, you're verified."); H.start();
        }).catch(function (err) { say(err.message, true); });
      });
    });
  }

  // ---------- start a real edit ----------
  function lookFor(S) {
    var keys = (H.config.looks || []).map(function (l) { return l.key; });
    if (keys.indexOf(S.style) >= 0) return S.style;
    var energetic = { lifestyle: 1, fitness: 1, beauty: 1, podcast: 1, food: 1 };
    return energetic[S.topic] ? "energy" : "story";
  }
  // how long a real edit takes, measured on our machines (the server's typical_minutes), never the prototype's guess
  H.eta = function () { var m = (H.config && H.config.typical_minutes) || 7; return "About " + m + " minutes"; };
  function lookName(key) { var l = (H.config.looks || []).filter(function (x) { return x.key === key; })[0]; return l ? l.name : key; }
  function putFile(file, kind, onProgress) {
    return api("/api/creators/uploads", { method: "POST", body: { kind: kind, name: file.name, size: file.size, type: file.type || "video/mp4" } })
      .then(function (up) {
        return new Promise(function (resolve, reject) {
          var x = new XMLHttpRequest();
          x.open("PUT", up.url);
          Object.keys(up.headers || {}).forEach(function (h) { x.setRequestHeader(h, up.headers[h]); });
          x.upload.onprogress = function (ev) { if (ev.lengthComputable && onProgress) onProgress(ev.loaded, ev.total); };
          x.onload = function () { if (x.status >= 200 && x.status < 300) resolve(up.key); else reject(new Error("The upload didn't go through (" + x.status + "). Try again.")); };
          x.onerror = function () { reject(new Error("The upload stopped. Check your connection and try again.")); };
          x.send(file);
        });
      });
  }
  function upState(text, frac) {
    var box = $("#up-progress"); box.hidden = !text;
    $("#up-text").textContent = text || "";
    $("#up-progress .meterbar i").style.width = Math.round((frac || 0) * 100) + "%";
  }
  H.start = function () {
    if (!H.settled) { H.ready.then(function () { if (H.hosted) H.start(); else toast("Still loading. Try again in a moment."); }); return true; }
    if (!H.hosted) return false;
    var A = app(), S = A.S;
    if (!H.me || !H.me.user) { toast("Create your free account first: it takes a few seconds."); A.go("signup"); return true; }
    if (!H.me.allowed) { toast("Real edits open soon. We'll email you when they do."); return true; }
    if (uploading) return true;
    var file = A.file;
    if (!file || S.clip.sample) { toast("Pick your own clip to edit."); return true; }
    if (S.freeLeft <= 0) { A.openPlans("You've used your 2 free edits. Every plan keeps everything you've seen."); return true; }
    if (H.config.phone_required && !H.me.phone_verified && S.freeLeft > 0 && S.freeLeft < 99) { askPhone(); return true; }
    var look = lookFor(S);
    if (S.style && S.style !== look) toast("That look isn't ready for real edits yet, so this one is in " + lookName(look) + ".");
    uploading = true;
    var btn = $("#start-edit"); btn.disabled = true;
    var total = file.size + own.reduce(function (a, f) { return a + f.size; }, 0), done = 0;
    function prog(base) { return function (loaded) { var p = (base + loaded) / total; upState("Uploading… " + Math.round(p * 100) + "%", p); }; }
    upState("Uploading… 0%", 0);
    putFile(file, "take", prog(0)).then(function (takeKey) {
      done += file.size;
      var keys = [];
      return own.reduce(function (chain, f) {
        return chain.then(function () { return putFile(f, "broll", prog(done)).then(function (k) { keys.push(k); done += f.size; }); });
      }, Promise.resolve()).then(function () {
        upState("Starting your edit…", 1);
        return api("/api/creators/jobs", { method: "POST", body: { take_key: takeKey, take_name: file.name, look: look, broll_keys: keys, email: true } });
      });
    }).then(function (job) {
      H.job = job; saveJob(job.id); own = []; drawOwn();
      upState("", 0);
      if (job.free && S.freeLeft < 99) { S.freeLeft = Math.max(0, S.freeLeft - 1); A.save(); }
      S.version = 1; A.save();
      A.go("editing");
    }).catch(function (e) {
      upState("", 0); toast(e.message);
      if (e.status === 402) A.openPlans("You've used your 2 free edits. Every plan keeps everything you've seen.");
    }).then(function () { uploading = false; btn.disabled = false; A.startState(); });
    return true;
  };

  // ---------- editing: the job's real stages ----------
  function etaText(j) {
    if (j.in_line) return j.in_line > 1 ? "In line: " + (j.in_line - 1) + " ahead of you" : "Next in line";
    if (!j.started_at) return j.status === "queued" || j.status === "starting" ? "Starting an edit machine" : "Working";
    var elapsed = Date.now() / 1000 - j.started_at, p = j.progress || 0;
    var left = p > 0.12 ? elapsed / p - elapsed : ((H.config && H.config.typical_minutes) || 7) * 60 - elapsed;
    return left > 50 ? "About " + Math.max(1, Math.round(left / 60)) + " min left" : "Almost done";
  }
  function drawEditing(j) {
    var A = app();
    var lead = $("#ed-lead");
    lead.innerHTML = "In <b>" + A.esc(j.look_name) + "</b>. You can close this tab: we'll email you.";
    $("#stages").innerHTML = (j.stages || []).map(function (s) {
      return '<div class="stg ' + (s.state === "done" ? "done" : s.state === "active" ? "active" : "") + '"><span class="st">' + A.ICON("i-check") +
        "</span><div><b>" + A.esc(s.label) + '</b><div class="d">' + A.esc(s.detail || (s.state === "active" ? "Working on it…" : "")) + "</div></div></div>";
    }).join("");
    var pct = Math.round((j.progress || 0) * 100);
    $("#ed-bar i").style.width = pct + "%"; $("#ed-bar").setAttribute("aria-valuenow", pct); $("#ed-pct").textContent = pct + "%";
    $("#ed-eta").textContent = j.status === "done" ? "Done" : j.status === "failed" ? "Stopped" : etaText(j);
    var err = $("#ed-err");
    if (j.status === "failed" || j.status === "cancelled") {
      if (!err) { err = document.createElement("div"); err.id = "ed-err"; err.className = "ed-err"; $("#stages").parentNode.insertBefore(err, $("#stages").nextSibling); }
      err.innerHTML = A.esc(j.error || "This edit stopped.") + '<br><button type="button" class="btn btn-sm" id="ed-retry">Try again</button>';
      $("#ed-retry").addEventListener("click", function () { saveJob(""); A.go("step-5"); });
    } else if (err) err.remove();
  }
  H.editing = function () {
    var A = app();
    clearTimeout(pollTimer);
    var t = $(".transcript"); if (t) t.hidden = true;
    var demo = $("#ed-demo"); if (demo) demo.hidden = true;
    var v = $("#ed-video");
    if (A.S.clip && A.S.clip.url && !A.S.clip.sample) { v.removeAttribute("poster"); if (v.getAttribute("src") !== A.S.clip.url) v.src = A.S.clip.url; }
    else if (H.job && H.job.take_url) { v.removeAttribute("poster"); if (v.getAttribute("src") !== H.job.take_url) v.src = H.job.take_url; }
    $("#ed-tag").textContent = "Your raw take";
    A.play(v);
    var id = (H.job && H.job.id) || savedJob();
    if (!id) { A.go("step-5"); return; }
    if (H.job) drawEditing(H.job);
    var poll = function () {
      api("/api/creators/jobs/" + id).then(function (j) {
        H.job = j;
        if (A.current() !== "editing") return;
        drawEditing(j);
        if (j.status === "done") { setTimeout(function () { if (A.current() === "editing") A.go("result"); }, 900); return; }
        if (j.status === "failed" || j.status === "cancelled") return;
        pollTimer = setTimeout(poll, 2500);
      }).catch(function (e) {
        if (e.status === 404) { saveJob(""); A.go("step-5"); return; }
        pollTimer = setTimeout(poll, 5000);
      });
    };
    poll();
  };
  document.addEventListener("change", function (e) {
    if (e.target.id !== "ed-email" || !H.on || !H.job) return;
    api("/api/creators/jobs/" + H.job.id + "/notify", { method: "POST", body: { on: e.target.checked } }).catch(function () {});
  });

  // ---------- result: the real Reel ----------
  H.media = function (mode) {
    var j = H.job, r = j && j.result;
    if (!r) return null;
    var A = app();
    if (mode === "after") return { src: r.video_url, poster: r.poster_url || "", seconds: r.seconds || 0 };
    var url = A.S.clip && A.S.clip.url && !A.S.clip.sample ? A.S.clip.url : r.take_url;
    return { src: url, poster: "", seconds: (r.summary && r.summary.take_seconds) || 0 };
  };
  function shareable() {
    try { return !!(navigator.canShare && navigator.canShare({ files: [new File([new Blob()], "reel.mp4", { type: "video/mp4" })] })); }
    catch (e) { return false; }
  }
  H.result = function () {
    var A = app(), id = (H.job && H.job.id) || savedJob();
    if (!H.job || H.job.status !== "done") {
      if (!id) { A.go("step-5"); return; }
      api("/api/creators/jobs/" + id).then(function (j) { H.job = j; if (j.status === "done") H.result(); else A.go("editing"); })
        .catch(function () { A.go("step-5"); });
      return;
    }
    var j = H.job, r = j.result, s = r.summary || {}, rev = r.review || {}, fmt = A.fmt, esc = A.esc;
    $("#rs-free").textContent = A.S.freeLeft >= 99 ? "Team account" : A.S.freeLeft > 0 ? A.S.freeLeft + " of 2 free edits left" : "Free edits used";
    $("#rs-h").textContent = s.hook ? "“" + s.hook + "”" : s.title || "Your Reel is ready";
    $("#rs-meta").textContent = fmt(r.seconds || 0) + " Reel" + (s.take_seconds ? " from a " + fmt(s.take_seconds) + " raw take" : "") + " · 1080×1920 · " + j.look_name;
    var rv = $("#rs-video");
    rv.poster = r.poster_url || ""; rv.src = r.video_url; rv.currentTime = 0;
    $$("[data-ba]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.ba === "after")); });
    $("#rs-tag-t").textContent = "Your Reel · v1"; $("#rs-tag").classList.add("good");
    var dl = $("#rs-dl"); dl.href = r.download_url || r.video_url; dl.removeAttribute("download");
    var sh = $("#rs-share"); sh.hidden = !shareable();
    // the checks: loudness, and what Gemini said when it watched it back
    var loud = s.loudness || {}, checks = [];
    if (loud.lufs !== undefined) checks.push([loud.lufs.toFixed(1).replace("-", "−"), "LUFS, in range for TikTok and Reels"]);
    if (loud.true_peak !== undefined) checks.push([loud.true_peak.toFixed(1).replace("-", "−") + " dB", "peak, so phones never distort it"]);
    if (rev.score !== undefined) checks.push([rev.score + "/10", "when we watched it back" + (rev.verdict ? ": " + rev.verdict : "")]);
    checks.push([String(s.fixed || 0), (s.fixed === 1 ? "note" : "notes") + " fixed before you saw it"]);
    $("#rs-checks").innerHTML = checks.map(function (x) { return '<div class="ck"><b class="tnum"><i>' + A.ICON("i-check") + "</i>" + esc(x[0]) + "</b><span>" + esc(x[1]) + "</span></div>"; }).join("");
    // "Passed" only when the watch-back watched it and says it's ready to post; otherwise say what happened, plainly
    var watched = rev.score !== undefined && rev.score !== null;
    var ready = watched && (/^post$/i.test(rev.verdict || "") || rev.score >= 8);
    var panel = $("#rs-checks").closest(".panel"), head = panel.querySelector("h2 .pill");
    if (head) { head.textContent = !watched ? "Checked" : ready ? (s.fixed ? "Fixed and passed" : "Passed") : "Notes below"; head.classList.toggle("ok", ready || !watched); }
    var sub = panel.querySelector(".sub2");
    if (sub) sub.textContent = watched ? "We watched the finished Reel back next to what you said." : "We checked its sound and length. The watch-back didn't answer in time on this one.";
    var top = $(".res-head .pill"); if (top) top.lastChild.textContent = watched ? "Ready, and watched back" : "Ready";
    // what's in it
    $("#rs-sound-sub").textContent = s.music ? "Made for this video, mixed under your voice." : s.take_music ? "Your take's own music, kept." : "Your voice, levelled and mastered.";
    $("#rs-sound").innerHTML = [s.music ? "An original music bed made for this edit. It dips every time you talk." : s.take_music ? "Your take already had music under your voice, so we kept it." : "No music this time.",
      loud.lufs !== undefined ? "Your voice, levelled and mastered to " + loud.lufs.toFixed(1).replace("-", "−") + " LUFS." : "Your voice, levelled and mastered."]
      .map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("");
    var beats = s.beats || [];
    $("#rs-added").innerHTML = beats.map(function (b) { return '<li><button type="button" data-seek="' + b.t + '"><span class="tchip">' + fmt(b.t) + '</span><span class="kind">' + esc(b.kind) + '</span><span class="txt">' + esc(b.text) + "</span></button></li>"; }).join("");
    $("#rs-acount").textContent = beats.length;
    var sc = $("#rs-scrub"); $$(".mk", sc).forEach(function (m) { m.remove(); });
    beats.forEach(function (b) { if (!r.seconds) return; var m = document.createElement("span"); m.className = "mk"; m.style.left = (b.t / r.seconds * 100) + "%"; sc.appendChild(m); });
    $("#rs-changed-sub").textContent = s.take_seconds ? "From " + fmt(s.take_seconds) + " of raw footage to " + fmt(r.seconds || 0) + "." : "";
    var notes = [];
    if (s.punch_ins) notes.push(s.punch_ins + " punch-ins on the lines that land.");
    var by = (s.broll && s.broll.by_source) || {}, kinds = Object.keys(by);
    if (kinds.length) notes.push("B-roll on the words it shows: " + kinds.map(function (k) { return by[k] + " " + k; }).join(", ") + ".");
    (rev.best || []).slice(0, 3).forEach(function (b) { notes.push("Works: " + b); });
    if (!ready) (rev.issues || []).slice(0, 4).forEach(function (i) { notes.push("Noticed at " + i.t + ": " + i.what); });
    $("#rs-notes").innerHTML = notes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("");
    // prototype-only panels: the cutline needs the demo's word data, and the tweak chat isn't real yet
    [$("#rs-cutline"), $("#rs-form")].forEach(function (el) { var p = el && el.closest(".panel"); if (p) p.hidden = true; });
  };
  // ---------- review rounds (the creators-review player, review.js, calls these; REVIEW.md) ----------
  // applyNotes: the notes go up as one multipart request, a child job re-cuts the Reel, and the promise settles with
  // that job once it's done (result.changes has a line per note). Rejected on failure: the notes stay in the review.
  H.applyNotes = function (payload, opts) {
    opts = opts || {};
    var id = H.job && H.job.id;
    if (!id) return Promise.reject(new Error("There's no Reel to apply these notes to."));
    var body = opts.formData;
    if (!body) {
      body = new FormData();
      body.append("notes", JSON.stringify(payload.json));
      Object.keys(payload.files || {}).forEach(function (path) { if (payload.files[path]) body.append(path, payload.files[path]); });
    }
    return fetch("/api/creators/jobs/" + id + "/notes", { method: "POST", credentials: "same-origin", body: body })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Your notes didn't go through. Try again.");
          return d;
        });
      })
      .then(function (job) {
        return new Promise(function (resolve, reject) {
          (function poll() {
            api("/api/creators/jobs/" + job.id).then(function (j) {
              try { if (opts.onProgress) opts.onProgress(j); } catch (e) {}
              if (j.status === "done") return resolve(j);
              if (j.status === "failed" || j.status === "cancelled") return reject(new Error(j.error || "This round stopped. Your notes are still here."));
              setTimeout(poll, 2500);
            }).catch(function (e) { if (e.status === 404) reject(e); else setTimeout(poll, 5000); });
          })();
        });
      });
  };
  // useJob: Keep v2 makes that version the one the result screen shows (and the one the next notes apply to)
  H.useJob = function (id) {
    return api("/api/creators/jobs/" + id).then(function (j) {
      H.job = j; saveJob(j.id);
      var A = app(); if (A && A.current() === "result") H.result();
      return j;
    });
  };

  document.addEventListener("click", function (e) {
    if (!e.target.closest("#rs-share") || !H.job || !H.job.result) return;
    var r = H.job.result, b = $("#rs-share");
    b.disabled = true; toast("Getting your Reel ready to share…");
    fetch(r.video_url).then(function (res) { if (!res.ok) throw new Error(); return res.blob(); }).then(function (blob) {
      var f = new File([blob], "my-reel.mp4", { type: "video/mp4" });
      return navigator.share({ files: [f], title: "My Reel" });
    }).catch(function (err) { if (!err || err.name !== "AbortError") toast("Sharing didn't work here. Download it, then post it from your phone."); })
      .then(function () { b.disabled = false; });
  });
})();
