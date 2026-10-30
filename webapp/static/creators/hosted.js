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
      if (H.on) H.loadProjects().catch(function () {});   // the home's list, fetched while the rest of the page loads
      return me;
    });
  }
  H.refreshMe = loadMe;
  // ---------- the home's projects (home.js): one per take, its notes rounds as versions ----------
  var listing = null;
  H.loadProjects = function () {
    if (!H.on) return Promise.resolve([]);
    if (!listing) listing = api("/api/creators/projects").then(function (d) { listing = null; H.list = d.projects || []; return H.list; },
      function (e) { listing = null; throw e; });
    return listing;
  };
  // has this creator made a Reel? (then they land on the home; first-timers go through onboarding)
  H.hasReels = function () { return !!((H.me && H.me.jobs && H.me.jobs.length) || (H.list && H.list.length)); };
  H.api = api;
  // open one version of a project on a screen ("result", "review" or "editing"), as the creator's current edit
  H.openJob = function (id, screen, version) {
    var A = app();
    return api("/api/creators/jobs/" + id).then(function (j) {
      H.job = j; saveJob(j.id);
      A.S.version = version || 1; A.S.cut = 1; A.save();
      A.go(screen);
    }).catch(function (e) { toast(e.message); });
  };
  H.forget = function (ids) { if (ids.indexOf(savedJob()) >= 0) { saveJob(""); H.job = null; } };
  H.logout = function () {
    return api("/api/auth/logout", { method: "POST" }).catch(function () {}).then(function () {
      saveJob("");
      try { sessionStorage.clear(); } catch (e) {}
      location.hash = "signin";
      location.reload();
    });
  };
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
  // A signed-in creator is routed by app.js once this settles: the home when they've made a Reel (a running edit
  // shows there, as "Now editing"), else onboarding. Never the sign-up.
  function setup() {
    mountGoogle();
    var A = app();
    if (!A) return;
    var running = (H.me.jobs || []).filter(function (j) { return ["queued", "starting", "running"].indexOf(j.status) >= 0; })[0];
    if (H.on) {
      showHostedBits();
      if (running && location.hash === "#editing") { H.job = running; saveJob(running.id); }
      A.startState();
    } else if (H.me.user && !H.me.allowed) {
      toast("Real edits open soon. You can look around in the meantime.");
    }
  }
  function showHostedBits() {
    $("#own-clips").hidden = false;
    restoreProfile();
    var home = $("#ed-home"); if (home) home.hidden = false;
    $$('a.brand[href="index.html"]').forEach(function (a) { a.setAttribute("href", "#home"); });   // signed in: the logo is home
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
      if (H.hasReels()) return A.replace("home");          // replace: Back never returns to the sign-in
      A.replace(A.S.make ? "step-5" : "step-1");
    });
  }
  H.google = function () {
    if (!H.hosted) return false;
    toast("Sign in with your email below."); $("#email").focus();
    return true;
  };
  // The emailed code (owner's Loom 2026-10-30, P1-01/02: the big purple button was "Send a new code", he tapped it
  // and the code he was typing stopped working). Once a code is out: the email is fixed, ONE primary full-width
  // "Sign in" (Enter, or the sixth digit, submits too), and "Send a new code" is a quiet link that waits out a
  // cooldown and asks before it replaces a code that's being typed (a new code ends the old one).
  var sent = null, cool = null;
  var RESEND_S = window.CR_RESEND_S || 30;
  function codeMsg(t, kind) { var m = $("#email-msg"); m.className = "field-msg" + (kind ? " " + kind : ""); m.textContent = t; }
  function sendCode(email) {
    var btn = $("#email-btn");
    btn.disabled = true;
    return api("/api/auth/send-code", { method: "POST", body: { email: email } }).then(function () {
      sent = email; showCodeStep(email); return true;
    }).catch(function (e) { codeMsg(e.message, "bad"); return false; })
      .then(function (ok) { btn.disabled = false; return ok; });
  }
  function cooldown() {
    var link = $("#code-resend"), left = RESEND_S;
    clearInterval(cool);
    var tick = function () {
      if (!link.isConnected) return clearInterval(cool);
      link.disabled = left > 0; delete link.dataset.armed;
      link.textContent = left > 0 ? "Send a new code in 0:" + String(left).padStart(2, "0") : "Send a new code";
      if (left-- <= 0) clearInterval(cool);
    };
    tick(); cool = setInterval(tick, 1000);
  }
  function showCodeStep(email) {
    var input = $("#email"), form = $("#email-form");
    input.readOnly = true; input.classList.add("is-sent");
    $("#email-btn").hidden = true;
    codeMsg("We sent a 6-digit code to " + email + ".", "good");
    var box = $("#code-box");
    if (!box) {
      box = document.createElement("div"); box.className = "code-box"; box.id = "code-box";
      box.innerHTML = '<label class="sr-only" for="code">The 6-digit code from the email</label>' +
        '<input class="text-input code-input" id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456" enterkeyhint="go">' +
        '<button type="button" class="btn btn-lg btn-block" id="code-btn">Sign in</button>' +
        '<div class="code-links"><button type="button" class="btn-text muted" id="code-resend" disabled>Send a new code</button>' +
        '<button type="button" class="btn-text muted" id="code-change">Use another email</button></div>';
      form.appendChild(box);
      var code = $("#code");
      code.addEventListener("input", function () {
        var v = code.value.replace(/\D/g, "").slice(0, 6);
        if (v !== code.value) code.value = v;
        if (v.length === 6 && !$("#code-btn").disabled) verify();     // the sixth digit signs in, as on every phone
      });
      code.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); verify(); } });
      $("#code-btn").addEventListener("click", verify);
      $("#code-resend").addEventListener("click", function () {
        var link = this;
        if ($("#code").value && !link.dataset.armed) {               // a code is being typed: ask first
          link.dataset.armed = "1"; link.textContent = "Tap again to send a new code";
          codeMsg("A new code replaces the one in your email. Tap again to send it.", "");
          setTimeout(function () { if (link.dataset.armed && !link.disabled) { delete link.dataset.armed; link.textContent = "Send a new code"; } }, 6000);
          return;
        }
        link.disabled = true;
        sendCode(sent).then(function (ok) {
          if (!ok) { link.disabled = false; return; }
          $("#code").value = ""; codeMsg("We sent a new code to " + sent + ". Use the newest one.", "good");
          $("#code").focus(); cooldown();
        });
      });
      $("#code-change").addEventListener("click", function () {
        sent = null; clearInterval(cool); box.remove();
        input.readOnly = false; input.classList.remove("is-sent"); $("#email-btn").hidden = false;
        codeMsg("", ""); input.focus(); input.select();
      });
    }
    cooldown();
    $("#code").focus();
  }
  function verify() {
    var code = $("#code").value.trim(), btn = $("#code-btn");
    if (!/^\d{4,8}$/.test(code)) { codeMsg("Enter the 6-digit code from the email.", "bad"); $("#code").focus(); return; }
    if (btn.disabled) return;
    btn.disabled = true; btn.textContent = "Signing in…";
    api("/api/auth/verify", { method: "POST", body: { email: sent, code: code } }).then(signedIn).catch(function (e) {
      codeMsg(e.status === 400 ? "That code didn't work. Check the newest email from us and try again." : e.message, "bad");
      $("#code").select();
    }).then(function () { btn.disabled = false; btn.textContent = "Sign in"; });
  }
  H.email = function (email) {
    if (!H.hosted) return false;
    if (sent && sent === email && $("#code")) { verify(); return true; }   // Enter in the (fixed) email field signs in
    sendCode(email);
    return true;
  };

  // ---------- step 5: B-roll is our job (owner's Loom 2026-10-30, P1-23 / P2-01) ----------
  // We find it (stock on their words); their profile adds shots from their latest posts; their own clips stay
  // optional. Our edit machines open TikTok profiles themselves; Instagram turns them away (tested 2026-10-30), so
  // Instagram profiles work only through Scrape Creators (config.profiles says whether it's switched on), and the
  // field says so instead of taking a link that would quietly do nothing.
  var PROFILE_KEY = "cr-creators-profile";
  function profileOf(raw) {
    var v = String(raw || "").trim();
    if (!v) return { kind: "" };
    var m = v.match(/^(?:https?:\/\/)?(?:(?:www|m)\.)?tiktok\.com\/@([A-Za-z0-9._]{2,40})\/?(?:\?\S*)?$/i);
    if (m) return { kind: "tiktok", url: "https://www.tiktok.com/@" + m[1], handle: "@" + m[1] };
    if (/tiktok\.com\/@[\w.]+\/(video|photo)\//i.test(v) || /instagram\.com\/(?:[\w.]+\/)?(reel|reels|p)\//i.test(v)) return { kind: "video" };
    m = v.match(/^(?:https?:\/\/)?(?:www\.)?instagram\.com\/([A-Za-z0-9._]{1,30})\/?(?:\?\S*)?$/i);
    if (m) return { kind: "instagram", url: "https://www.instagram.com/" + m[1], handle: "@" + m[1] };
    if (/^@?[A-Za-z0-9._]{2,30}$/.test(v)) return { kind: "handle" };
    return { kind: "bad" };
  }
  function profileState() {
    var input = $("#profile-input"); if (!input) return { kind: "" };
    var r = profileOf(input.value), msg = $("#profile-msg"), field = $("#profile-field"), ig = igOn();
    if (r.kind === "instagram" && ig) r.ok = true;
    if (r.kind === "tiktok") r.ok = true;
    input.placeholder = ig ? "tiktok.com/@you or instagram.com/you" : "tiktok.com/@yourname";
    var looking = ["good", "We'll look through " + (r.handle || "your") + "'s latest posts for shots that fit your words."];
    var say = {
      "": ["", ig ? "We cut in shots from your latest TikTok or Instagram posts where they fit." : "TikTok works now: we cut shots from your latest posts. Instagram profiles are coming soon."],
      tiktok: looking,
      instagram: ig ? looking : ["warn", "Instagram profiles are coming soon: Instagram doesn't let our edit machines open them yet. Paste your TikTok, or add clips below."],
      video: ["bad", "That's one video. Paste your profile instead, like tiktok.com/@yourname."],
      handle: ["bad", ig ? "Paste the whole link, like tiktok.com/@yourname or instagram.com/yourname." : "Paste the whole link, like tiktok.com/@yourname, so we know it's your TikTok."],
      bad: ["bad", ig ? "That isn't a TikTok or Instagram profile link." : "That isn't a TikTok profile link. It looks like tiktok.com/@yourname."]
    }[r.kind];
    msg.className = "field-msg" + (say[0] ? " " + say[0] : ""); msg.textContent = say[1];
    field.classList.toggle("good", !!r.ok); field.classList.toggle("bad", ["video", "handle", "bad"].indexOf(r.kind) >= 0);
    return r;
  }
  document.addEventListener("input", function (e) { if (e.target.id === "profile-input") profileState(); });
  document.addEventListener("change", function (e) {
    if (e.target.id !== "profile-input") return;
    var r = profileState();
    try { if (r.ok) localStorage.setItem(PROFILE_KEY, r.url); else if (!e.target.value.trim()) localStorage.removeItem(PROFILE_KEY); } catch (err) {}
  });
  function igOn() { return !!(H.config && (H.config.profiles || []).indexOf("instagram") >= 0); }
  function restoreProfile() {
    var input = $("#profile-input"); if (!input || input.value) return;
    try { input.value = localStorage.getItem(PROFILE_KEY) || ""; } catch (e) {}
    profileState();
  }

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
  // Pasted Reels: the edit machine learns their style first. It can open TikTok and Instagram links; YouTube turns
  // cloud machines away (tested 2026-10-30), so from YouTube only the example Short works (learned once, cached).
  H.refProblem = function (r) {
    var A = app();
    return H.on && r && r.kind === "youtube" && !(A && r.url === A.example)
      ? "YouTube doesn't let our editors open Shorts yet. Paste a TikTok or an Instagram Reel of theirs instead." : "";
  };
  function lookFor(S) {
    var keys = (H.config.looks || []).map(function (l) { return l.key; });
    if (keys.indexOf(S.style) >= 0) return S.style;
    var energetic = { lifestyle: 1, fitness: 1, beauty: 1, podcast: 1, food: 1 };
    return energetic[S.topic] ? "energy" : "story";
  }
  // how long a real edit takes, measured on our machines (the server's typical_minutes), never the prototype's guess
  H.eta = function () {
    var m = (H.config && H.config.typical_minutes) || 7, S = app() && app().S;
    if (S && S.refs && S.refs.length && !(S.refs[0].url === app().example)) m += 1.5;   // learning their style first
    return "About " + Math.round(m) + " minutes";
  };
  // ---------- a pasted Reel's picture and creator (GET /api/creators/reel-preview), asked once per link ----------
  var previews = {};
  H.preview = function (url) {
    if (!H.on || !url) return Promise.resolve(null);
    if (!previews[url]) previews[url] = api("/api/creators/reel-preview?url=" + encodeURIComponent(url)).catch(function () { delete previews[url]; return null; });
    return previews[url];
  };
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
    var file = A.file, reuse = S.clip && S.clip.reuse;            // reuse: "Try another look" on a take already up
    if (!reuse && (!file || S.clip.sample)) { toast("Pick your own clip to edit."); return true; }
    if (S.freeLeft <= 0) { A.openPlans("You've used your 2 free edits. Every plan keeps everything you've seen."); return true; }
    if (H.config.phone_required && !H.me.phone_verified && S.freeLeft > 0 && S.freeLeft < 99) { askPhone(); return true; }
    var prof = profileState();
    if (["video", "handle", "bad"].indexOf(prof.kind) >= 0) { toast("Fix the profile link, or clear it: it's optional."); $("#profile-input").focus(); return true; }
    var look = lookFor(S);
    if (S.style && S.style !== look) toast("That look isn't ready for real edits yet, so this one is in " + lookName(look) + ".");
    uploading = true;
    var btn = $("#start-edit"); btn.disabled = true;
    var total = Math.max(1, (reuse ? 0 : file.size) + own.reduce(function (a, f) { return a + f.size; }, 0)), done = 0;
    var takeName = reuse ? S.clip.name : file.name;
    H.upload = { name: takeName, frac: 0 };
    function homeTick() { if (window.CRHome) window.CRHome.upload(); }
    function prog(base) { return function (loaded) { var p = (base + loaded) / total; upState("Uploading… " + Math.round(p * 100) + "%", p); if (H.upload) { H.upload.frac = p; homeTick(); } }; }
    upState("Uploading… 0%", 0);
    (reuse ? Promise.resolve(reuse) : putFile(file, "take", prog(0))).then(function (takeKey) {
      done += reuse ? 0 : file.size;
      var keys = [];
      return own.reduce(function (chain, f) {
        return chain.then(function () { return putFile(f, "broll", prog(done)).then(function (k) { keys.push(k); done += f.size; }); });
      }, Promise.resolve()).then(function () {
        upState("Starting your edit…", 1);
        var refs = (S.refs || []).map(function (r) { return r.url; }).slice(0, 3);
        return api("/api/creators/jobs", { method: "POST", body: { take_key: takeKey, take_name: takeName, look: look, broll_keys: keys, refs: refs,
          profile: prof.ok ? prof.url : "", email: true } });
      });
    }).then(function (job) {
      H.job = job; saveJob(job.id); own = []; drawOwn();
      upState("", 0); H.upload = null;
      if (job.free && S.freeLeft < 99) { S.freeLeft = Math.max(0, S.freeLeft - 1); A.save(); }
      if (H.me && typeof H.me.free_left === "number" && job.free) H.me.free_left = Math.max(0, H.me.free_left - 1);
      if (H.me && H.me.jobs) H.me.jobs.unshift(job);
      S.version = 1; S.flow = null; S.lastLook = look; A.save();
      if (A.current() === "onboard") A.go("editing");     // left for the home while it uploaded: stay there
      else if (window.CRHome) window.CRHome.refresh();
    }).catch(function (e) {
      upState("", 0); H.upload = null; homeTick(); toast(e.message);
      if (e.status === 402) A.openPlans("You've used your 2 free edits. Every plan keeps everything you've seen.");
    }).then(function () { uploading = false; btn.disabled = false; A.startState(); });
    return true;
  };

  // ---------- editing: the job's real stages ----------
  // Honest progress (owner's Loom 2026-10-30, P2-03/04: the badge went 7 … 10 … 2, 3, 4 min and he asked whether it
  // was rendering on his laptop). The minutes come from the usual time on our machines (plus learning a style), the
  // job's own pace only when it's ahead, and they never go back up: past the usual time it says "Almost done", then
  // "Taking a little longer than usual". The page says plainly that it runs on our servers.
  var etaHeld = { id: null, min: Infinity, note: "" };
  function etaText(j) {
    if (j.in_line) return j.in_line > 1 ? "In line: " + (j.in_line - 1) + " ahead of you" : "Next in line";
    if (!j.started_at) return j.status === "queued" || j.status === "starting" ? "Starting an edit machine" : "Working";
    if (etaHeld.id !== j.id) etaHeld = { id: j.id, min: Infinity, note: "" };
    var total = j.expected_s || ((H.config && H.config.typical_minutes) || 7) * 60;
    var elapsed = Math.max(0, Date.now() / 1000 - j.started_at), p = j.progress || 0;
    var left = total - elapsed;
    if (p >= 0.6) left = Math.min(left, elapsed / p - elapsed);   // the pace, only once the render is well under way
    var note = left > 50 ? "" : elapsed < total * 1.25 ? "almost" : "late";
    if (etaHeld.note === "late" || (etaHeld.note === "almost" && note === "")) note = etaHeld.note === "late" ? "late" : "almost";
    if (note) { etaHeld.note = note; return note === "late" ? "Taking a little longer than usual" : "Almost done"; }
    etaHeld.min = Math.min(etaHeld.min, Math.max(1, Math.round(left / 60)));   // never more minutes than we said
    return "About " + etaHeld.min + " min left";
  }
  function clock(sec) { sec = Math.max(0, Math.round(sec)); return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0"); }
  // The style this edit is in (P2-02: it said "In Cinematic story" while it learned the pasted Reel's style).
  function refInfo(j) {
    var A = app(), url = (j.refs || [])[0];
    var mine = url && ((A.S.refs || []).filter(function (r) { return r.url === url; })[0]);
    return mine && (mine.author || mine.thumb) ? mine : (url && H.previewed && H.previewed[url]) || null;
  }
  function learning(j) {
    var w = (j.stages || [])[0] || {};
    return !!((j.refs || []).length && !j.style_name && !j.style_error && ["queued", "starting", "running"].indexOf(j.status) >= 0 &&
              w.state !== "done" && (!w.state || w.state === "todo" || w.detail === "Learning their style"));
  }
  function profileOf(url) {                         // a pasted profile, not a Reel: {handle, ...} or null
    var p = url && window.CRReel && window.CRReel.parse(url);
    return p && p.profile ? p : null;
  }
  function drawRef(j) {
    var A = app(), box = $("#ed-ref"), url = (j.refs || [])[0];
    if (!url) { box.hidden = true; return; }
    var prof = profileOf(url);
    var r = refInfo(j) || {}, name = r.author || (r.handle ? r.handle.replace(/^@?/, "@") : "") || (prof ? prof.handle : "");
    var kind = /tiktok/.test(url) ? "tiktok" : /instagram/.test(url) ? "instagram" : "youtube";
    box.hidden = false;
    box.innerHTML = '<span class="rthumb ' + kind + '">' + (r.thumb ? '<img src="' + A.esc(r.thumb) + '" alt="" referrerpolicy="no-referrer">' : A.ICON("p-" + kind)) + "</span>" +
      '<span class="rtx"><b>' + A.esc(j.style_name || (name ? name + "'s style" : "The Reel you pasted")) + "</b>" +
      "<span>" + A.esc(j.style_name ? "Learned from " + (name || "the Reel you pasted") + (r.platform ? "'s " + r.platform : "") :
        j.style_error ? (/open/.test(j.style_error) ? "We couldn't open it" : "We couldn't learn it this time") + ", so this one is in " + j.look_name :
        prof ? "From their most watched recent Reels: their cuts, captions and sound" :
        "From " + (r.platform ? "their " + r.platform : "the Reel you pasted") + ": its cuts, captions and sound") + "</span></span>";
    if (!refInfo(j) && H.on && !prof) H.preview(url).then(function (d) { if (!d) return; H.previewed = H.previewed || {}; H.previewed[url] = d; if (H.job && H.job.id === j.id && A.current() === "editing") drawRef(H.job); });
  }
  function drawEditing(j) {
    var A = app();
    var lead = $("#ed-lead"), refs = (j.refs || []).length, r = refInfo(j) || {}, prof = profileOf((j.refs || [])[0]);
    var who = r.author || (r.handle ? r.handle.replace(/^@?/, "@") : "") || (prof ? prof.handle : "");
    lead.innerHTML = refs && learning(j) ? "Learning " + (who ? "<b>" + A.esc(who) + "</b>'s style" : "the style of the Reel you pasted") + ", then cutting yours the same way."
      : refs && j.style_name ? "In <b>" + A.esc(j.style_name) + "</b>, learned from the Reel you pasted."
      : refs && j.style_error ? "In <b>" + A.esc(j.look_name) + "</b>: " + (/open/.test(j.style_error) ? "we couldn't open the Reel you pasted." : "we couldn't learn that style this time.")
      : "In <b>" + A.esc(j.look_name) + "</b>.";
    drawRef(j);
    var now = Date.now() / 1000, stages = (j.stages || []).slice();
    if (refs) {                                     // learning their style is a step of its own, first
      var lrn = learning(j), started = !!j.started_at;
      stages = stages.map(function (s, k) { return k === 0 && lrn ? Object.assign({}, s, { state: "todo", detail: "" }) : s; });
      stages.unshift({ key: "style", label: "Learning their style",
        state: lrn ? (started ? "active" : "todo") : "done", at: lrn ? (stages[0] && stages[0].at) || j.started_at : null,
        detail: lrn ? (who ? "From " + who + "'s Reel: its cuts, captions and sound" : "Its cuts, captions and sound")
          : j.style_name ? "Learned: " + j.style_name : j.style_error ? "Couldn't open it: editing in " + j.look_name : "Learned" });
    }
    $("#stages").innerHTML = stages.map(function (s) {
      var active = s.state === "active", d = s.detail || (active ? "Working on it…" : "");
      var t = active && s.at && now - s.at > 15 ? '<span class="clk tnum"> · ' + clock(now - s.at) + "</span>" : "";
      return '<div class="stg ' + (s.state === "done" ? "done" : active ? "active" : "") + '" data-key="' + A.esc(s.key || "") + '"><span class="st">' + A.ICON("i-check") +
        "</span><div><b>" + A.esc(s.label) + '</b><div class="d">' + A.esc(d) + t + "</div></div></div>";
    }).join("");
    var pct = Math.round((j.progress || 0) * 100);
    $("#ed-bar i").style.width = pct + "%"; $("#ed-bar").setAttribute("aria-valuenow", pct); $("#ed-pct").textContent = pct + "%";
    $("#ed-eta").textContent = j.status === "done" ? "Done" : j.status === "failed" ? "Stopped" : etaText(j);
    var err = $("#ed-err");
    if (j.status === "failed" || j.status === "cancelled") {
      if (!err) { err = document.createElement("div"); err.id = "ed-err"; err.className = "ed-err"; $("#stages").parentNode.insertBefore(err, $("#stages").nextSibling); }
      err.innerHTML = A.esc(j.error || "This edit stopped.") + '<br><button type="button" class="btn btn-sm" id="ed-retry">Try again</button>';
      $("#ed-retry").addEventListener("click", function () { saveJob(""); A.newReel({ look: j.look, step: 5 }); });
    } else if (err) err.remove();
  }
  // the creator's own take under "Your raw take": the file they just picked, else the job's copy once it loads. After a
  // reload the job comes a moment later, and until then the page's sample clip (someone else's Reel) must not play there.
  function showTake(v) {
    var A = app(), url = A.S.clip && A.S.clip.url && !A.S.clip.sample ? A.S.clip.url : H.job && H.job.take_url;
    if (url) { v.removeAttribute("poster"); if (v.getAttribute("src") !== url) { v.src = url; A.play(v); } }
    else if (/e_raw/.test(v.getAttribute("src") || "")) { v.removeAttribute("poster"); v.removeAttribute("src"); v.load(); }
  }
  H.editing = function () {
    var A = app();
    clearTimeout(pollTimer);
    var t = $(".transcript"); if (t) t.hidden = true;
    var demo = $("#ed-demo"); if (demo) demo.hidden = true;
    var v = $("#ed-video");
    showTake(v);
    $("#ed-tag").textContent = "Your raw take";
    var where = $("#ed-where"); if (where) where.hidden = false;
    A.play(v);
    var id = (H.job && H.job.id) || savedJob();
    if (!id) { A.go("step-5"); return; }
    if (H.job) drawEditing(H.job);
    var poll = function () {
      api("/api/creators/jobs/" + id).then(function (j) {
        H.job = j;
        if (A.current() !== "editing") return;
        drawEditing(j);
        showTake($("#ed-video"));
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
  // A transcript span as a title (section H: "This 12 -minute video costs about $12 to make and"): whisper's split
  // words joined, a dangling fragment after the last full stop dropped, and no trailing "and", "to", "the"…
  // (webapp/creators_worker.py tidy_line does the same for new edits; this covers the ones made before it.)
  var DANGLING = { and: 1, or: 1, but: 1, so: 1, because: 1, the: 1, a: 1, an: 1, of: 1, to: 1, "with": 1, my: 1, your: 1, our: 1, their: 1, his: 1, her: 1, its: 1, "if": 1, than: 1 };
  H.tidy = function (text) {
    var t = String(text || "").replace(/\s+/g, " ").trim().replace(/(\w) -([A-Za-z])/g, "$1-$2").replace(/\s+([,.!?;:])/g, "$1");
    var m, last = -1, re = /[.!?](?=\s)/g;
    while ((m = re.exec(t))) last = m.index + 1;
    if (last > 0 && !/[.!?]$/.test(t) && t.slice(last).trim().split(" ").length < 4 && t.slice(0, last).split(" ").length >= 2) t = t.slice(0, last);
    var w = t.split(" ");
    while (w.length > 3 && DANGLING[w[w.length - 1].toLowerCase().replace(/[,;:—-]+$/, "")]) w.pop();
    return w.join(" ").replace(/[,;:—\- ]+$/, "");
  };
  function secs(t) { var p = String(t || "0").split(":"); return p.length > 1 ? (+p[0] || 0) * 60 + (+p[1] || 0) : +p[0] || 0; }
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
    var hook = H.tidy(s.hook), title = H.tidy(s.title);
    $("#rs-h").textContent = hook && hook.split(" ").length >= 3 ? "“" + hook + "”" : title || (hook ? "“" + hook + "”" : "Your Reel is ready");
    $("#rs-meta").textContent = fmt(r.seconds || 0) + " Reel" + (s.take_seconds ? " from a " + fmt(s.take_seconds) + " raw take" : "") + " · 1080×1920 · " + j.look_name;
    var rv = $("#rs-video");
    rv.poster = r.poster_url || ""; rv.src = r.video_url; rv.currentTime = 0;
    $$("[data-ba]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.ba === "after")); });
    $("#rs-tag-t").textContent = "Your Reel · v" + (A.S.version || 1); $("#rs-tag").classList.add("good");
    var dl = $("#rs-dl"); dl.href = r.download_url || r.video_url; dl.removeAttribute("download");
    var sh = $("#rs-share"); sh.hidden = !shareable();
    // The checks, and an honest head (owner's Loom 2026-10-30, section H: "Ready, and watched back ✓" with green
    // ticks sat over a 4/10 watch-back). Green only when the watch-back says it's ready to post; under 7 (or "redo")
    // the page says the score, what it noticed, and offers notes or another look.
    // The watch-back scores the first cut; when it had notes, one fix pass re-cut the Reel and nobody watched that
    // version again. So the score is said as the first cut's, with how many things were changed since.
    var watched = rev.score !== undefined && rev.score !== null, fixedN = s.fixed || 0;
    var ready = watched && (/^post$/i.test(rev.verdict || "") || rev.score >= 8);
    var low = watched && (rev.score < 7 || /redo/i.test(rev.verdict || ""));
    var loud = s.loudness || {}, checks = [];
    if (loud.lufs !== undefined) checks.push([loud.lufs.toFixed(1).replace("-", "−"), "LUFS, in range for TikTok and Reels", "ok"]);
    if (loud.true_peak !== undefined) checks.push([loud.true_peak.toFixed(1).replace("-", "−") + " dB", "peak, so phones never distort it", "ok"]);
    if (watched) checks.push([rev.score + "/10", (fixedN && !ready ? "the first cut, " : "") + "when we watched it back" + (rev.verdict ? ": " + rev.verdict : ""), ready ? "ok" : low ? "bad" : "warn"]);
    checks.push([String(s.fixed || 0), (s.fixed === 1 ? "note" : "notes") + " fixed before you saw it", "ok"]);
    $("#rs-checks").innerHTML = checks.map(function (x) {
      return '<div class="ck ' + x[2] + '"><b class="tnum"><i>' + A.ICON(x[2] === "ok" ? "i-check" : "i-alert") + "</i>" + esc(x[0]) + "</b><span>" + esc(x[1]) + "</span></div>";
    }).join("");
    var panel = $("#rs-checks").closest(".panel"), head = panel.querySelector("h2 .pill");
    if (head) { head.textContent = !watched ? "Checked" : ready ? (s.fixed ? "Fixed and passed" : "Passed") : low ? "Needs work" : "Worth a look"; head.className = "pill " + (ready || !watched ? "ok" : low ? "bad" : "warn"); }
    var sub = panel.querySelector(".sub2");
    if (sub) sub.textContent = watched ? "We watched the finished Reel back next to what you said." : "We checked its sound and length. The watch-back didn't answer in time on this one.";
    var issues = ready ? [] : (rev.issues || []).filter(function (i) { return i && i.what; });
    var il = $("#rs-issues");
    il.hidden = !issues.length;
    il.innerHTML = issues.length ? "<h3>" + (fixedN ? "What it pointed at in the first cut" : "What it noticed") + '</h3><ul class="notes">' + issues.slice(0, 4).map(function (i) {
      return '<li><button type="button" class="tlink" data-seek="' + secs(i.t) + '"><span class="tchip">' + esc(i.t || "") + "</span>" + esc(i.what) + "</button></li>";
    }).join("") + "</ul>" : "";
    var top = $(".res-head .pill");
    if (top) {
      top.className = "pill " + (low && !fixedN ? "bad" : watched && !ready ? "warn" : "ok");
      top.innerHTML = A.ICON(watched && !ready ? "i-alert" : "i-check") + "<span>" +
        esc(!watched ? "Ready" : ready ? "Ready, and watched back" : low && fixedN ? "First cut scored " + rev.score + "/10 · " + fixedN + " fixed since"
          : low ? "Watched back: " + rev.score + "/10, it needs work" : "Ready, with notes from the watch-back") + "</span>";
      var ic = top.querySelector("svg"); if (ic) { ic.setAttribute("width", "14"); ic.setAttribute("height", "14"); }
    }
    var honest = $("#rs-honest");
    honest.hidden = !low;
    honest.classList.toggle("warn", !!fixedN);
    if (low) {
      $("#rs-honest-h").textContent = fixedN ? "Our watch-back scored the first cut " + rev.score + "/10" : "Our watch-back scored this " + rev.score + "/10";
      $("#rs-honest-p").textContent = fixedN
        ? "We changed " + fixedN + " thing" + (fixedN === 1 ? "" : "s") + " it pointed at before you saw this version, but we haven't watched it again. Check it before you post: if it isn't right, give notes or try another look."
        : "It's yours to download, but we wouldn't post it as it is." + (issues.length ? " " + (issues.length > 1 ? "The biggest things it noticed are below." : "Here's what it noticed.") : "");
    }
    // what's in it
    $("#rs-sound-sub").textContent = s.music ? "Made for this video, mixed under your voice." : s.take_music ? "Your take's own music, kept." : "Your voice, levelled and mastered.";
    $("#rs-sound").innerHTML = [s.music ? "An original music bed made for this edit. It dips every time you talk." : s.take_music ? "Your take already had music under your voice, so we kept it. Want ours instead? Say so in a note." : "No music this time. Want some? Say so in a note.",
      loud.lufs !== undefined ? "Your voice, levelled and mastered to " + loud.lufs.toFixed(1).replace("-", "−") + " LUFS." : "Your voice, levelled and mastered."]
      .map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("");
    var beats = s.beats || [];
    $("#rs-added").innerHTML = beats.map(function (b) { return '<li><button type="button" data-seek="' + b.t + '"><span class="tchip">' + fmt(b.t) + '</span><span class="kind">' + esc(b.kind) + '</span><span class="txt">' + esc(b.kind === "Hook" ? H.tidy(b.text) : b.text) + "</span></button></li>"; }).join("");
    $("#rs-acount").textContent = beats.length;
    var sc = $("#rs-scrub"); $$(".mk", sc).forEach(function (m) { m.remove(); });
    beats.forEach(function (b) { if (!r.seconds) return; var m = document.createElement("span"); m.className = "mk"; m.style.left = (b.t / r.seconds * 100) + "%"; sc.appendChild(m); });
    $("#rs-changed-sub").textContent = s.take_seconds ? "From " + fmt(s.take_seconds) + " of raw footage to " + fmt(r.seconds || 0) + "." : "";
    var notes = [];
    if (s.punch_ins) notes.push(s.punch_ins + " punch-ins on the lines that land.");
    var by = (s.broll && s.broll.by_source) || {}, kinds = Object.keys(by);
    if (kinds.length) notes.push("B-roll on the words it shows: " + kinds.map(function (k) { return by[k] + " " + k; }).join(", ") + ".");
    (rev.best || []).slice(0, 3).forEach(function (b) { notes.push("Works: " + b); });   // what it noticed is in the check above
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
