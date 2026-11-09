/* ChannelRecipe Studio, the creator sandbox v2 (owner, 2026-11-01: "the sandbox should be believable... visually eye
   candy... randomized so each time it shows a different result... zero bottlenecks"). A UGC creator films this on
   their laptop. Every step shows work happening, one button moves on, nothing waits on a render: the video at the end
   is a real ChannelRecipe output from the topic's pool, never the one this browser saw last.
   studio#clone/history, #ranking/rank-cats (?speed=fast or ?speed=0.7 for recordings). Data: strategies.json (sandbox/strategies.py). Esc starts over. */
(() => {
  "use strict";
  const BASE = "/ugc-sandbox/";
  const CDN = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/ugc-sandbox/";
  const SPEED = ((m) => !m ? 1 : m[1] === "fast" ? 0.4 : Math.min(2, Math.max(0.2, +m[1] || 1)))(location.search.match(/[?&]speed=(fast|[\d.]+)/));
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const wait = (ms) => new Promise((r) => setTimeout(r, ms * SPEED));
  const short = (n) => n >= 1e9 ? `${(n / 1e9).toFixed(1).replace(/\.0$/, "")}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(Math.round(n || 0));
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };
  const yt = (id) => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
  let D, M, T, OUT, run = 0;
  // One tap plays the whole B-roll (the owner, 2026-11-09: "even I am super confused… there's no guide"): the walkthrough
  // opens on one start card, then ChannelRecipe's own part plays by itself and ends on "Done": the creator never types,
  // picks or copies anything. It plays on their laptop and they film the screen with their phone. Google and YouTube
  // are never mocked here: creators film the real ones (the owner: "the psychology won't transfer").
  // ?manual=1 keeps the old click-through page.
  const AUTO = !/[?&]manual=1/.test(location.search);
  // in AUTO, does what the creator used to do, after a natural pause (never after the run changed)
  const pilot = (my, fn, ms = 900) => { if (AUTO) wait(ms).then(() => { if (my === run) fn(); }); };

  async function json(name) {
    for (const url of [CDN + name + `?h=${Math.floor(Date.now() / 6e5)}`, BASE + name]) {
      try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) { /* next */ }
    }
    return null;
  }

  function countUp(el, to, ms, fmt = short) {
    const t0 = performance.now(), dur = ms * SPEED;
    const tick = (t) => { const k = Math.min(1, (t - t0) / dur); el.textContent = fmt(to * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }
  async function typeText(el, text, cps = 38) {
    const start = el.textContent, t0 = performance.now(), dur = text.length * 1000 / cps * SPEED;
    for (;;) {
      const k = Math.min(1, (performance.now() - t0) / dur);
      el.textContent = start + text.slice(0, Math.round(text.length * k));
      if (k >= 1) return;
      await new Promise((r) => setTimeout(r, 16));
    }
  }
  // types into an input; "target" lets a click mid-typing still use the whole text
  function typer(input) {
    let my = 0;
    const fn = async (t) => {
      const me = ++my; fn.target = t; input.value = "";
      const t0 = performance.now(), dur = t.length * 32 * SPEED;
      for (;;) {
        if (me !== my) return;
        const k = Math.min(1, (performance.now() - t0) / dur);
        input.value = t.slice(0, Math.round(t.length * k));
        input.scrollLeft = input.scrollWidth;            // (the words being typed stay in view, not the link's start)
        if (k >= 1) return;
        await new Promise((r) => setTimeout(r, 16));
      }
    };
    fn.target = "";
    fn.value = () => { const v = input.value.trim(); return fn.target && fn.target.startsWith(v) ? fn.target : v; };
    input.addEventListener("input", () => { my += 1; fn.target = ""; });
    return fn;
  }
  function steps(n, total) { $("#steps").innerHTML = Array.from({ length: total }, (_, i) => `<i class="${i < n ? "on" : ""}"></i>`).join(""); }
  function screen(html) { const s = $("#screen"); s.style.animation = "none"; void s.offsetWidth; s.style.animation = ""; s.innerHTML = html; return s; }
  function button(label, id = "next") { return `<button class="go big" id="${id}" type="button">${esc(label)}</button>`; }
  const clicked = (id = "next") => new Promise((r) => $("#" + id).addEventListener("click", r, { once: true }));

  function pickOutput() {
    const pool = T.outputs || [];
    if (!pool.length) return null;
    const key = `last:${T.id}`, last = store.get(key);
    const choices = pool.length > 1 ? pool.filter((o) => o.video !== last) : pool;
    const out = choices[Math.floor(Math.random() * choices.length)];
    store.set(key, out.video);
    return out;
  }

  // Every play starts at a random second of the video (the hub, 2026-11-09: no two creators show the same clip from the
  // same second), late enough to leave 12 s to play; "Sound on" keeps that place. It never reaches the video's end (a
  // sourced video's end card): 4 s before it, it jumps to another random second.
  function playFrom(v) {
    const jump = () => { const room = (v.duration || 0) - 12; v.currentTime = room > 1 ? Math.random() * room : 0; };
    const go = () => { jump(); v.play().catch(() => {}); };
    v.loop = false;
    if (AUTO) v.removeAttribute("controls");             // (a phone showed its scrub bar and time over the B-roll)
    v.addEventListener("timeupdate", () => { if (v.duration && v.currentTime > v.duration - 4) jump(); });
    v.addEventListener("ended", () => { jump(); v.play().catch(() => {}); });
    if (v.readyState >= 1) go(); else v.addEventListener("loadedmetadata", go, { once: true });
  }

  function nav() {
    $("#methods").innerHTML = D.methods.map((m) => `<button type="button" data-m="${m.id}" class="${m === M ? "on" : ""}" ${m.soon ? "disabled" : ""}>
      <span class="ic">${{ clone: "⧉", claude: "✳︎", like: "🔗", ranking: "🏆", clipping: "✂︎", amazon: "📦" }[m.id] || "•"}</span>${esc(m.name)}${m.soon ? "<small>soon</small>" : ""}</button>`).join("");
    $("#methods").onclick = (e) => { const b = e.target.closest("button[data-m]"); if (b && !b.disabled) { location.hash = `${b.dataset.m}/`; } };
    // on a phone the methods are one row that scrolls sideways: show the open one
    const nv = $("#methods"), on = $("#methods button.on");
    if (on && nv.scrollWidth > nv.clientWidth) nv.scrollLeft = on.offsetLeft - nv.offsetLeft - 14;
  }

  /* ---------- copy a viral channel ---------- */
  async function clone(my) {
    const refs = T.refs || [];
    steps(1, 5);
    screen(`<h1>Copy any <em>viral channel</em></h1><p class="lead">Paste a YouTube channel that's blowing up. Studio studies its videos and learns its style, then makes your own video in it.</p>
      <div class="bigin"><input id="url" placeholder="Paste a YouTube channel or video link" autocomplete="off">${button("Analyze channel", "go")}</div>
      ${refs.length ? `<p class="label" style="margin-top:22px">Going viral in ${esc(T.name)}</p><div class="chips">${refs.slice(0, 4).map((r) => `<button class="chip" type="button" data-u="${esc(r.channel_url)}">${esc(r.channel)}<small>${short(r.views)} views</small></button>`).join("")}</div>` : ""}`);
    $(".chips")?.addEventListener("click", (e) => { const c = e.target.closest("[data-u]"); if (c) $("#url").value = c.dataset.u; });
    if (!AUTO) $("#url").focus();
    pilot(my, async () => { const c = $(".chips [data-u]"); if (c) c.click(); else $("#url").value = (refs[0] || {}).channel_url || "https://www.youtube.com/"; await wait(900); if (my === run) $("#go").click(); }, 1200);
    await new Promise((res) => { const go = () => { if ($("#url").value.trim()) res(); else $("#url").focus(); }; $("#go").onclick = go; $("#url").onkeydown = (e) => { if (e.key === "Enter") go(); }; });
    if (my !== run) return;
    const url = $("#url").value.trim().toLowerCase();
    const ref = refs.find((r) => url.includes(String(r.channel_url).toLowerCase().split("/").pop()) || url.includes(r.id.toLowerCase())) || refs[0] || {};
    const handle = (url.match(/youtube\.com\/(@[\w.\-]+)/) || [])[1];
    const name = ref.channel || handle || "This channel";
    const vids = ((D.channels || {})[ref.channel_url] || []).slice(0, 6);
    let shown = vids.length >= 3 ? vids : refs.slice(0, 6).map((r) => ({ id: r.id, views: r.views, title: r.title }));
    if (shown.length < 3) shown = (OUT.frames || []).map((f) => ({ img: f }));   // few refs today: the style's own frames
    // scan
    steps(2, 5);
    screen(`<div class="chan"><div class="avatar">${esc(name.replace("@", "")[0] || "C").toUpperCase()}</div><div><b>${esc(name)}</b><small>Studio is watching this channel's videos…</small></div></div>
      <div class="grid" id="g"><div class="scanline"></div>${shown.map((v) => `<div class="thumb"><img src="${v.img ? esc(v.img) : yt(v.id)}" alt="">${v.views ? `<span class="v" data-v="${v.views}">0</span>` : ""}</div>`).join("")}</div>
      <div class="stats"><div class="stat"><b id="s1">0</b><small>videos studied</small></div><div class="stat"><b id="s2">0</b><small>total views</small></div><div class="stat"><b id="s3">0</b><small>average per video</small></div></div>`);
    const tiles = [...document.querySelectorAll(".thumb")];
    for (const t of tiles) { t.classList.add("in"); const v = $(".v", t); if (v) countUp(v, +v.dataset.v, 900); await wait(380); }
    const total = shown.reduce((a, v) => a + (v.views || 0), 0);
    countUp($("#s1"), shown.length, 600, (n) => String(Math.round(n)));
    if (total) { countUp($("#s2"), total, 1200); countUp($("#s3"), total / Math.max(1, shown.length), 1200); }
    else document.querySelectorAll(".stat").forEach((st, i) => { if (i) st.remove(); });
    await wait(1600);
    $(".scanline")?.remove();
    // style DNA
    steps(3, 5);
    const tall = OUT.tall;
    screen(`<p class="label">Style learned from ${esc(name)}</p><h1>Its <em>style DNA</em></h1>
      <div class="dna"><div class="mosaic ${tall ? "tall" : ""}">${(OUT.frames || []).map((f) => `<img src="${esc(f)}" alt="">`).join("")}</div>
      <div><div class="ring" id="ring"><div><b id="pct">0%</b><small>style match</small></div></div><ul class="traits">${(T.look && T.look.length ? T.look : ["Its pacing and cuts", "Its look and colours", "Its voice and music"]).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
      <div class="row">${button("Use this style →")}</div></div></div>`);
    for (const img of document.querySelectorAll(".mosaic img")) { img.classList.add("in"); await wait(170); }
    const ring = $("#ring"); let p = 0;
    const target = 92 + Math.floor(Math.random() * 7);
    const iv = setInterval(() => { p = Math.min(target, p + 3); ring.style.setProperty("--p", p); $("#pct").textContent = p + "%"; if (p >= target) clearInterval(iv); }, 40 * SPEED);
    for (const li of document.querySelectorAll(".traits li")) { await wait(420); li.classList.add("in"); }
    pilot(my, () => $("#next")?.click(), 1400);
    await clicked();
    if (my !== run) return;
    // idea
    steps(4, 5);
    const ideas = (T.outputs || []).map((o) => o.title);
    screen(`<h1>What's <em>your</em> video about?</h1><p class="lead">Any idea in this niche. Studio writes it, voices it and makes every scene in ${esc(name)}'s style.</p>
      <div class="bigin"><input id="idea" placeholder="${esc(OUT.title)}" autocomplete="off">${button("Create video", "make")}</div>
      <div class="chips">${ideas.map((i) => `<button class="chip" type="button">${esc(i)}</button>`).join("")}</div>`);
    const input = $("#idea");
    const typeIdea = typer(input);
    $(".chips").onclick = (e) => { const c = e.target.closest(".chip"); if (c) { const o = T.outputs.find((x) => x.title === c.textContent); if (o) OUT = o; typeIdea(c.textContent); } };
    input.onkeydown = (e) => { if (e.key === "Tab" && !input.value) { e.preventDefault(); typeIdea(OUT.title); } if (e.key === "Enter") $("#make").click(); };
    if (!AUTO) input.focus();
    pilot(my, async () => { typeIdea(OUT.title); await wait(OUT.title.length * 32 + 900); if (my === run) $("#make").click(); }, 1000);
    await clicked("make");
    if (my !== run) return;
    await build(my, typeIdea.value() || OUT.title, "clone");
  }

  /* ---------- ranking shorts ---------- */
  async function ranking(my) {
    steps(1, 5);
    screen(`<h1>What should we <em>rank?</em></h1><p class="lead">Studio finds the most viral clips, watches each for its best moment, and counts them down with a narrator, booms and captions.</p>
      <div class="bigin"><input id="topic" placeholder="${esc(T.name)}" autocomplete="off">${button("Find the clips", "go")}</div>
      <div class="chips">${M.topics.filter((t) => t.outputs.length).map((t) => `<button class="chip" type="button" data-t="${t.id}">${esc(t.name)}</button>`).join("")}</div>`);
    const input = $("#topic");
    const typeIt = typer(input);
    $(".chips").onclick = (e) => { const c = e.target.closest("[data-t]"); if (c) { T = M.topics.find((t) => t.id === c.dataset.t); OUT = pickOutput(); typeIt(T.name); } };
    input.onkeydown = (e) => { if (e.key === "Tab" && !input.value) { e.preventDefault(); typeIt(T.name); } if (e.key === "Enter") $("#go").click(); };
    if (!AUTO) input.focus();
    pilot(my, async () => { typeIt(T.name); await wait(T.name.length * 32 + 900); if (my === run) $("#go").click(); }, 1000);
    await clicked("go");
    if (my !== run) return;
    const clips = (T.clips || []).slice(0, 12);
    steps(2, 5);
    screen(`<p class="label">Searching TikTok, YouTube Shorts and Reels</p><h1>Finding <em>viral clips</em></h1>
      <div class="grid four" id="g">${clips.map((c) => `<div class="thumb tall"><img src="${yt(c.id)}" alt=""><span class="v" data-v="${c.views}">0</span><div class="scrub"><i></i></div><span class="best"></span></div>`).join("")}</div>
      <div class="stats"><div class="stat"><b id="s1">0</b><small>clips found</small></div><div class="stat"><b id="s2">0</b><small>combined views</small></div></div>`);
    const tiles = [...document.querySelectorAll(".thumb")];
    for (const t of tiles) { t.classList.add("in"); countUp($(".v", t), +$(".v", t).dataset.v, 700); await wait(170); }
    countUp($("#s1"), tiles.length, 500, (n) => String(Math.round(n)));
    countUp($("#s2"), clips.reduce((a, c) => a + c.views, 0), 1100);
    await wait(900);
    // watching each for its best moment
    $("h1").innerHTML = "Watching each one for its <em>best moment</em>";
    tiles.forEach((t) => { $(".scrub i", t).style.width = "100%"; });
    await wait(1500);
    const picks = tiles.map((t, i) => [t, i]).sort(() => Math.random() - 0.5).slice(0, Math.min(5, tiles.length));
    tiles.forEach((t) => { const b = $(".best", t); b.textContent = `best 0:0${2 + Math.floor(Math.random() * 7)}`; b.classList.add("on"); });
    await wait(900);
    tiles.forEach((t) => { if (!picks.some(([p]) => p === t)) t.classList.add("dim"); });
    for (const [t] of picks) { t.classList.add("pick"); await wait(250); }
    await wait(800);
    // the countdown
    steps(3, 5);
    screen(`<p class="label">Ranked by reactions and replays</p><h1>Your <em>top 5</em></h1><ol class="rank-list">${picks.map(([, i], k) => `<li><b>${5 - k}</b><img src="${yt(clips[i].id)}" alt=""><span>${esc(clips[i].title).slice(0, 70)}</span></li>`).reverse().join("")}</ol><div class="row">${button("Make the Short →")}</div>`);
    const items = [...document.querySelectorAll(".rank-list li")].reverse();
    for (const li of items) { li.classList.add("in"); await wait(380); }
    pilot(my, () => $("#next")?.click(), 1400);
    await clicked();
    if (my !== run) return;
    await build(my, T.name, "ranking");
  }

  /* ---------- clipping: a stream in, its viral moments out as captioned Shorts (real clips cut from that stream) ---------- */
  const hms = (n) => { n = Math.round(n || 0); const h = Math.floor(n / 3600), m = Math.floor(n % 3600 / 60), x = String(n % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
  async function clipping(my) {
    const o = OUT, src = o.source || {}, clips = (o.clips || []).filter((c) => c.video);
    steps(1, 4);
    screen(`<h1>Clip any <em>stream</em></h1><p class="lead">Paste a stream or podcast. ChannelRecipe watches all of it, finds the moments that go viral and cuts them into captioned Shorts.</p>
      <div class="bigin"><input id="url" placeholder="Paste a YouTube video link" autocomplete="off">${button("Find the viral clips", "go")}</div>`);
    const input = $("#url"), typeUrl = typer(input);
    input.onkeydown = (e) => { if (e.key === "Tab" && !input.value) { e.preventDefault(); typeUrl(src.url || ""); } if (e.key === "Enter") $("#go").click(); };
    if (!AUTO) input.focus();
    pilot(my, async () => { typeUrl(src.url || ""); await wait((src.url || "").length * 32 + 900); if (my === run) $("#go").click(); }, 1000);
    await new Promise((res) => { $("#go").onclick = () => { if (input.value.trim()) res(); else input.focus(); }; });
    if (my !== run) return;
    // watching the whole stream: the playhead runs, each clip's real place lights up as it passes
    steps(2, 4);
    const len = src.seconds || 1;
    screen(`<div class="chan"><img class="srcthumb" src="${esc(src.thumb || "")}" alt=""><div><b>${esc(src.title || "")}</b><small>${esc(src.channel || "")} · ${hms(len)} · watching all of it…</small></div></div>
      <div class="timeline"><i id="tlbar"></i>${clips.map((c) => `<b style="left:${Math.min(99, (c.start || 0) / len * 100).toFixed(2)}%" data-at="${(c.start || 0) / len}"></b>`).join("")}</div>
      <div class="stats"><div class="stat"><b id="s1">0:00</b><small>watched</small></div><div class="stat"><b id="s2">0</b><small>viral moments found</small></div></div>`);
    const marks = [...document.querySelectorAll(".timeline b")];
    for (let k = 0; k <= 1.0001; k += 0.025) {
      $("#tlbar").style.width = `${k * 100}%`; $("#s1").textContent = hms(len * k);
      const lit = marks.filter((m) => +m.dataset.at <= k); lit.forEach((m) => m.classList.add("on")); $("#s2").textContent = String(lit.length);
      await wait(110);
      if (my !== run) return;
    }
    await wait(700);
    // the clips themselves
    steps(3, 4);
    screen(`<p class="label">Cut, captioned and vertical</p><h1><em>${clips.length}</em> viral clips</h1>
      <div class="grid four">${clips.map((c) => `<div class="thumb tall"><img src="${esc(c.poster)}" alt=""><span class="v">${hms(c.seconds)}</span></div>`).join("")}</div>`);
    for (const t of document.querySelectorAll(".thumb")) { t.classList.add("in"); await wait(160); }
    await wait(1600);
    if (my !== run) return;
    // one of them plays
    steps(4, 4);
    const c = clips[Math.floor(Math.random() * clips.length)] || {};
    screen(`<div class="reveal"><div class="player"><video id="v" src="${esc(c.video || "")}" poster="${esc(c.poster || "")}" playsinline muted loop controls></video><button class="unmute" id="um" type="button">🔊 Sound on</button></div>
      <div><span class="done-badge">✓ ${clips.length} clips ready</span><h1 style="font-size:28px">${esc(c.title || "")}</h1>
      <ul class="checks"><li>Its best moments, found by watching all of it</li><li>Cut vertical, captioned</li><li>Ready to post</li></ul></div></div>`);
    const v = $("#v");
    playFrom(v);
    $("#um").onclick = () => { v.muted = false; v.play(); $("#um").remove(); };
    finish(my);
  }

  /* ---------- ChannelRecipe in Claude (the corpus's biggest angle: "connect it to Claude") ---------- */
  async function claudeFlow(my) {
    const refs = T.refs || [], ref = refs[0] || {};
    const chan = ref.channel || "a viral channel";
    const poss = (n) => (/s$/i.test(n) ? `${n}'` : `${n}'s`);
    const like = M.ask === "like", link = T.link || (ref.id ? `https://www.youtube.com/shorts/${ref.id}` : "");
    const ask = like ? `${link} Make me a video like this with ChannelRecipe`
      : `Use ChannelRecipe: copy ${poss(chan)} style and make me a video about "${OUT.title}"`;
    steps(1, 4);
    screen(`<div class="cl"><div class="cl-top"><b>Claude</b><span class="conn"><i></i>ChannelRecipe connected</span></div>
      <div class="cl-chat" id="chat"><div class="cl-hello">How can I help you today?</div></div>
      <div class="cl-box"><input id="ask" placeholder="Reply to Claude…" autocomplete="off"><button class="go" id="send" type="button">↑</button></div>
      <p class="hint">Tab types the request · Enter sends it</p></div>`);
    const input = $("#ask"), typeAsk = typer(input);
    input.onkeydown = (e) => { if (e.key === "Tab" && !input.value) { e.preventDefault(); typeAsk(ask); } if (e.key === "Enter") $("#send").click(); };
    if (!AUTO) input.focus();
    pilot(my, async () => { typeAsk(ask); await wait(ask.length * 32 + 1000); if (my === run) $("#send").click(); }, 1000);
    await new Promise((res) => { $("#send").onclick = () => { if (input.value.trim()) res(); else input.focus(); }; });
    if (my !== run) return;
    const said = typeAsk.value() || input.value.trim() || ask;
    input.value = ""; input.disabled = true;
    const chat = $("#chat");
    const add = (cls, html) => { const d = document.createElement("div"); d.className = cls; d.innerHTML = html; chat.append(d); chat.scrollTop = chat.scrollHeight; return d; };
    $(".cl-hello")?.remove();
    add("msg me", esc(said));
    steps(2, 4);
    await wait(700);
    const intro = add("msg ai", "");
    await typeText(intro, like ? "I'll use ChannelRecipe to study that video and its channel, then make you one like it."
      : "I'll use ChannelRecipe to study the channel's style, then make your video in it.", 60);
    // tool 1: study the channel
    const t1 = add("tool", `<div class="th"><span class="ic">⧉</span><b>ChannelRecipe</b><code>clone_style</code><em>running…</em></div>
      <div class="tb"><small>${like ? `Studying the video and ${esc(poss(chan))} channel` : `Studying ${esc(poss(chan))} videos`}</small><div class="mini">${(refs.length >= 3 ? refs.slice(0, 6).map((r) => yt(r.id)) : (OUT.frames || [])).map((u) => `<img src="${esc(u)}" alt="">`).join("")}</div></div>`);
    for (const img of t1.querySelectorAll(".mini img")) { img.classList.add("in"); await wait(240); }
    await wait(500);
    $("em", t1).textContent = `✓ style learned · ${92 + Math.floor(Math.random() * 7)}% match`; t1.classList.add("done");
    // tool 2: make the video
    steps(3, 4);
    const t2 = add("tool", `<div class="th"><span class="ic">▶</span><b>ChannelRecipe</b><code>make_video</code><em>running…</em></div>
      <div class="tb"><small id="stage">Writing the script</small><div class="doc" id="doc"></div>
      <div class="wave small" id="wave" hidden>${"<i></i>".repeat(36)}</div>
      <div class="mini" id="scenes" hidden>${(OUT.frames || []).map((f) => `<img src="${esc(f)}" alt="">`).join("")}</div>
      <div class="bar" id="barw" hidden><i id="bar"></i></div></div>`);
    const lines = (OUT.script && OUT.script.length ? OUT.script : [OUT.title + "."]).slice(0, 3);
    for (const l of lines) { await typeText($("#doc", t2), l + " ", 60); chat.scrollTop = chat.scrollHeight; }
    $("#stage", t2).textContent = "Recording the voice"; $("#wave", t2).hidden = false;
    const bars = [...t2.querySelectorAll("#wave i")];
    const iv = setInterval(() => bars.forEach((b) => { b.style.height = `${12 + Math.random() * 86}%`; }), 110);
    await wait(1800); clearInterval(iv);
    $("#stage", t2).textContent = "Making every scene"; $("#scenes", t2).hidden = false; chat.scrollTop = chat.scrollHeight;
    for (const img of t2.querySelectorAll("#scenes img")) { img.classList.add("in"); await wait(240); }
    $("#stage", t2).textContent = "Music, captions & render"; $("#barw", t2).hidden = false; chat.scrollTop = chat.scrollHeight;
    for (let q = 0; q <= 100; q += 3 + Math.floor(Math.random() * 5)) { $("#bar", t2).style.width = q + "%"; await wait(60); }
    $("#bar", t2).style.width = "100%";
    $("em", t2).textContent = "✓ video ready"; t2.classList.add("done");
    if (my !== run) return;
    // the answer, with the real video in the chat
    steps(4, 4);
    const tall = OUT.tall;
    const out = add("msg ai", "");
    await typeText(out, like ? `Done! Here's one like it: "${OUT.title}"` : `Done! Here's "${OUT.title}" in ${poss(chan)} style:`, 60);
    add("cl-video", `<div class="player ${tall ? "" : "wide"}"><video id="v" src="${esc(OUT.video)}" poster="${esc(OUT.poster)}" playsinline muted loop controls></video><button class="unmute" id="um" type="button">🔊 Sound on</button></div>
      <div class="row"><button class="go" id="dl" type="button">Download</button><button class="ghost" id="again" type="button">Make another</button></div>`);
    chat.scrollTop = chat.scrollHeight;
    const v = $("#v");
    playFrom(v);
    // the video's height is only known once it loads: then the chat scrolls to show it whole
    v.addEventListener("loadedmetadata", () => { chat.scrollTop = chat.scrollHeight; }, { once: true });
    $("#um").onclick = () => { v.muted = false; v.play(); $("#um").remove(); };
    $("#again").onclick = () => start();
    $("#dl").onclick = (e) => { e.target.textContent = "✓ Saved to Downloads"; e.target.disabled = true; };
    finish(my);
  }

  /* ---------- building and the reveal (both flows) ---------- */
  async function build(my, title, kind) {
    steps(4, 5);
    const lines = (OUT.script && OUT.script.length ? OUT.script : [title + "."]).slice(0, 4);
    screen(`<p class="label">Making “${esc(title)}”</p><h1>Studio is <em>making your video</em></h1>
      <div class="build">
        <div class="stg" id="b1"><h4>${kind === "ranking" ? "Writing the narrator's lines" : "Writing the script"}<span>…</span></h4><div class="doc" id="doc"></div></div>
        <div class="stg" id="b2"><h4>Recording the voice<span>…</span></h4><div class="wave" id="wave">${"<i></i>".repeat(42)}</div></div>
        <div class="stg" id="b3"><h4>${kind === "ranking" ? "Cutting the clips" : "Making the scenes"}<span>…</span></h4><div class="tiles">${(OUT.frames || []).map((f) => `<div><img src="${esc(f)}" alt=""></div>`).join("")}</div></div>
        <div class="stg" id="b4"><h4>${kind === "ranking" ? "Booms, captions & render" : "Music, captions & render"}<span>…</span></h4><div class="bar"><i id="bar"></i></div><div class="pct" id="pct">0%</div></div>
      </div>`);
    const on = (id) => $("#" + id).classList.add("on");
    const done = (id) => { const s = $("#" + id); s.classList.remove("on"); s.classList.add("done"); $("h4 span", s).textContent = "✓ done"; };
    on("b1");
    for (const l of lines) { await typeText($("#doc"), l + " ", 55); }
    done("b1"); on("b2");
    const bars = [...document.querySelectorAll("#wave i")];
    const t0 = Date.now();
    const iv = setInterval(() => bars.forEach((b) => { b.style.height = `${12 + Math.random() * 86}%`; }), 110);
    await wait(2200);
    clearInterval(iv); bars.forEach((b) => { b.style.height = `${18 + Math.random() * 30}%`; });
    done("b2"); on("b3");
    for (const img of document.querySelectorAll(".tiles img")) { img.classList.add("in"); await wait(260); }
    done("b3"); on("b4");
    for (let p = 0; p <= 100; p += 2 + Math.floor(Math.random() * 4)) { $("#bar").style.width = p + "%"; $("#pct").textContent = p + "%"; await wait(70); }
    $("#bar").style.width = "100%"; $("#pct").textContent = "100%";
    done("b4");
    await wait(500);
    if (my !== run) return;
    reveal(title);
  }

  function reveal(title) {
    steps(5, 5);
    const wide = !OUT.tall;
    screen(`<div class="reveal ${wide ? "wide" : ""}"><div class="player ${wide ? "wide" : ""}"><video id="v" src="${esc(OUT.video)}" poster="${esc(OUT.poster)}" playsinline muted loop controls></video><button class="unmute" id="um" type="button">🔊 Sound on</button></div>
      <div><span class="done-badge">✓ Your video is ready</span><h1 style="font-size:32px">${esc(title)}</h1>
      <ul class="checks"><li>Script, voice and every scene</li><li>Captions and music</li><li>${M.id === "ranking" ? "Counted down #5 to #1" : "In the channel's style"}</li><li>Ready to post</li></ul>
      <div class="row"><button class="go" id="dl" type="button">Download</button><button class="ghost" id="again" type="button">Make another</button></div></div></div>`);
    const v = $("#v");
    playFrom(v);
    $("#um").onclick = () => { v.muted = false; v.play(); $("#um").remove(); };
    $("#again").onclick = () => start();
    $("#dl").onclick = (e) => { e.target.textContent = "✓ Saved to Downloads"; e.target.disabled = true; };
    finish(run);
  }

  /* ---------- the one-tap B-roll (AUTO) ---------- */
  function card(html) {
    $(".auto-card")?.remove();
    const c = document.createElement("div");
    c.className = "auto-card"; c.innerHTML = `<div class="auto-box">${html}</div>`;
    document.body.append(c);
    return c;
  }
  // the finished video plays a while, then one line tells them they're done
  function finish(my) {
    if (!AUTO) return;
    wait(7000).then(() => { if (my === run) card(`<div class="auto-ok">✓</div><p class="auto-line">Done ✅ Send the clip to the bot.</p>`); });
  }
  async function play(my) {
    $(".auto-card")?.remove();
    ({ ranking, claude: claudeFlow, clipping }[M.flow] || clone)(my);
  }

  function start() {
    run += 1;
    const [mid, tid] = location.hash.replace("#", "").split("/");
    M = D.methods.find((m) => m.id === mid && !m.soon) || D.methods.find((m) => !m.soon);
    const ready = M.topics.filter((t) => t.outputs && t.outputs.length);
    T = ready.find((t) => t.id === tid) || ready[Math.floor(Math.random() * ready.length)];
    OUT = pickOutput();
    $("#crumb").textContent = M.name;
    nav();
    if (!AUTO) { ({ ranking, claude: claudeFlow, clipping }[M.flow] || clone)(run); return; }
    const my = run;
    screen("");
    card(`<p class="auto-line">Open this on your laptop. Tap ▶ and film the screen with your phone.</p><button class="auto-play" id="autoplay" type="button" aria-label="Play">▶</button>`);
    $("#autoplay").onclick = () => play(my);
  }

  async function main() {
    if (AUTO) document.body.classList.add("auto");
    D = await json("strategies.json");
    window.addEventListener("hashchange", start);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") start(); });
    start();
  }
  main();
})();
