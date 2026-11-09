/* The creator kit (owner, 2026-11-01: "even a grandma could do it"): today's video is already picked, a real winning
   example plays right there, and the shots are listed with exactly what to say, filled in for today's topic. Creators
   can switch to another video to remake or another topic; the viral channels for the topic sit below, then places to
   find more inspiration. Data: the CDN's pathways.json (formats, words) + strategies.json (methods, topics, ready-made
   videos, viral refs; sandbox/pathways.py, strategies.py). */
(() => {
  "use strict";
  const BASE = "/ugc-sandbox/";
  const CDN = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/ugc-sandbox/";
  const MEDIA = CDN + "v1/";
  const BOT = "https://t.me/CRCreatorsBot";
  const fresh = () => `?h=${Math.floor(Date.now() / 6e5)}`;
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const short = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n || 0);
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };
  let D, T, S, R, O, tpl, path, method;

  const toast = (t) => { const d = document.createElement("div"); d.className = "toast"; d.textContent = t; document.body.append(d); setTimeout(() => d.remove(), 1500); };
  async function copy(text) { try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { prompt("Copy this:", text); } }

  async function json(name) {
    for (const url of [CDN + name + fresh(), BASE + name]) {
      try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) { /* next */ }
    }
    return null;
  }

  // a "path" here is a topic of a method (strategies.json), dressed with its pathway's words (pathways.json)
  const methods = () => S.methods.filter((m) => !m.soon);
  const ready = (m = method) => m.topics.filter((t) => t.outputs && t.outputs.length);
  // A format can be locked to the one method/topic its hook sells (pathways.json templates "method", "topic"): the GTA 6
  // countdown demos a GTA channel or clipping, never medieval history. Until that demo has a ready-made video the
  // format shows "coming soon", so nobody films a hook the walkthrough can't keep.
  function lockOf(t) {
    if (!t.method && !t.topic) return null;
    const m = S.methods.find((x) => x.id === (t.method || method.id));
    // a method-only lock (the new formats film the Claude connector) keeps the creator's topic when that method has it
    const own = path && ready(m || method).find((x) => x.id === path.id);
    return { m, p: m && !m.soon ? (t.topic ? ready(m).find((x) => x.id === t.topic) : own || ready(m)[0]) || null : null };
  }
  const usable = (t) => { const l = lockOf(t); return !l || !!l.p; };
  const free = () => D.templates.find((t) => !lockOf(t));
  function words(t, m) {
    const pw = D.pathways.find((x) => x.id === t.pathway);
    return pw ? { short: pw.short, search: pw.search, keyword: pw.keyword, idea: t.outputs[0].title, name: t.name }
      : { short: t.name.toLowerCase(), search: t.search || t.name, keyword: m.keyword, idea: t.name, name: t.name };
  }
  const top = (t) => (t.refs || [])[0] || (t.clips || []).map((c) => ({ channel: "a ranking channel", views: c.views }))[0];
  // "so did {clip_views} people" is about the clip the viewer just watched, so it says that clip's real number
  // ({views} is today's top trending video). The kit reads it aloud, so it's in words: "39 million".
  let clipNow = null;
  const spokenViews = (n) => (n >= 1e6 ? `${+(n / 1e6).toFixed(1)} million` : n >= 1e3 ? `${Math.round(n / 1e3)} thousand` : String(n));
  function fill(s, t, m = method) {
    const w = words(t, m), r = top(t);
    return String(s).replaceAll("{short}", w.short).replaceAll("{search}", w.search)
      .replaceAll("{channel}", r ? r.channel : "this channel").replaceAll("{views}", r ? short(r.views) : "millions of")
      .replaceAll("{clip_views}", clipNow && clipNow.views ? spokenViews(clipNow.views) : "millions of")
      .replaceAll("{topic}", w.idea).replaceAll("{keyword}", w.keyword).replaceAll("{n}", "N")
      // the "like" walkthrough's pasted link: the topic's own viral video (strategies.json "link")
      .replaceAll("{link}", t.link || (r && r.id ? `https://www.youtube.com/shorts/${r.id}` : "the video's link"));
  }
  // The Claude walkthrough films real Claude with ChannelRecipe connected: each creator's own sandboxed connector
  // (site webapp/ugc_mcp.py), set up once, then the one prompt the kit fills in for the topic.
  const MCP = `${location.origin}/ugc-mcp/${location.pathname.split("/")[2] || ""}/mcp`;
  // Every promise in a script needs its asset in the kit (owner 2026-11-03: "how do the UGC people know what prompt to
  // paste?"): the prompt sits inside the walkthrough shot, and "comment KEYWORD" comes with the reply to send.
  const promptBox = (p) => `<div class="hook prompt-inline"><span>Paste this prompt in Claude</span><b>${esc(fill(method.prompt, p))}</b>
      <button class="btn ghost small" type="button" data-copy="${esc(fill(method.prompt, p))}">Copy the prompt</button></div>`;
  function dmBox(p) {
    // the reply backs every close's promise ("the prompt I use", "my guide", "step-by-step"): the method's own steps
    const kw = fill("{keyword}", p), steps = (method.guide || []).map((g, i) => `${i + 1}) ${fill(g, p)}`);
    const msg = steps.length ? `Here's my step-by-step guide 🙌 ${steps.join(" ")}`
      : `Here you go 🙌 This is the tool I use: channelrecipe.com.`;
    return `<div class="hook dm-box"><span>When people comment ${esc(kw)}, send them this</span><b>${esc(msg)}</b>
      <button class="btn ghost small" type="button" data-copy="${esc(msg)}">Copy the reply</button></div>`;
  }
  // Connecting ChannelRecipe to Claude, as close to one button as Claude allows (owner 2026-11-03: creators got lost):
  // three numbered buttons at the TOP of the video, before any shot; once done it folds to one line.
  const setupDone = () => store.get("claude-setup") === "done";
  const claudeBox = (p) => {
    const pr = fill(method.prompt || "", p);
    if (setupDone()) return `<div class="setup done"><b>✓ ChannelRecipe is connected to Claude</b>
      <button class="btn ghost small" type="button" id="setup-again">Show the setup again</button></div>`;
    return `<div class="setup"><span>Before you film · connect ChannelRecipe to Claude (1 minute, only once)</span>
      <ol>
        <li><button class="btn" type="button" data-copy="${esc(MCP)}">1 · Copy your ChannelRecipe link</button></li>
        <li><a class="btn" href="https://claude.ai/settings/connectors" target="_blank" rel="noopener">2 · Open Claude's connectors ↗</a>
          <small>Click <b>Add custom connector</b>, name it <b>ChannelRecipe</b>, paste the link, click <b>Add</b>.
          (In the Claude app: Settings → Connectors.)</small></li>
        <li><a class="btn" href="https://claude.ai/new" target="_blank" rel="noopener" data-copy="${esc(pr)}">3 · Copy the prompt and open Claude ↗</a>
          <small>Paste it in the new chat: <b>${esc(pr)}</b>. If Claude asks, choose <b>Always allow</b>. The video plays right in the chat.</small></li>
      </ol>
      <button class="btn ghost small" type="button" id="setup-done">It worked, hide this</button></div>`;
  };
  const shotsFor = (tp, m) => tp.shots.map((s) => s.what === "The walkthrough" && m.walk_say ? { ...s, say: m.walk_say } : s);
  const embed = (plat, id) => plat === "tt" ? `https://www.tiktok.com/embed/v2/${id}` : `https://www.instagram.com/reel/${id}/embed/`;
  const thumb = (plat, id) => `${MEDIA}ex_${plat === "tt" ? "tt" : "ig"}-${id}.jpg`;

  // The hook bank (pathways.json "hooks"): the format's families, the method's lane; each creator's browser rotates
  // through them ("↻ another hook") and remembers where it was, so creators don't all post the same words.
  const lane = (m = method) => (m.id === "clipping" ? "clipping" : m.id === "kids" ? "kids" : "any");
  function hooksFor(t) {
    const fams = t.hook_families || [];
    const afterClip = (t.shots || []).some((s, i) => s.kind === "added" && i < t.hook_shot);   // the hook answers the clip
    return (D.hooks || []).filter((h) => fams.includes(h.family) && (h.lanes.includes("any") || h.lanes.includes(lane()))
      && (h.slot !== "after_clip" || afterClip)        // an answering line never opens a video
      && (h.clip !== "person" || ["claim", "hustle"].includes(t.react))   // "she's living in 2099" needs a person
      && (!h.method || h.method === method.id));        // a line naming Claude only over the Claude walkthrough
  }
  function pick(list, key) {
    if (!list.length) return null;
    let i = +(store.get(key) ?? NaN);
    if (!Number.isFinite(i)) { i = Math.floor(Math.random() * list.length); store.set(key, i); }
    return list[((i % list.length) + list.length) % list.length];
  }
  // a remake from the outliers library keeps its example's theme (outliers.json "react_feed", e.g. a Zack D Films-style
  // 3D Short): its react clip comes from that feed, never a random one (owner, 2026-11-03)
  let feedPin = null;
  const feedOf = (t) => (feedPin && t.react && R && R.feeds[feedPin] ? feedPin : method.id === "clipping" && t.react ? "clipping" : t.react);
  const clipsFor = (t) => (R && t.react && R.feeds[feedOf(t)] ? R.feeds[feedOf(t)].clips : []);
  // the bot opens the kit straight from this link; RICH_LINK adds the format, clip and hook ("__" parts), which the bot
  // pins to the creator's next video (bot 509f57b, 2026-11-02). false falls back to the plain kit link.
  const RICH_LINK = true;
  const botKit = (p) => (D.pathways || []).some((x) => x.id === p.pathway && x.result);   // the bot's walk-<pathway> kits
  const botLink = (t, p, h, c) => !botKit(p) ? BOT : `${BOT}?start=kit_walk-${p.pathway}` + (!RICH_LINK ? "" : `__${t.id}` +
    (c ? `__${c.id}` : "") + (h ? `__h${(D.hooks || []).indexOf(h)}` : ""));

  function pickToday() {
    const day = Math.floor(Date.now() / 864e5);
    const saved = new URLSearchParams(location.hash.slice(1));
    const def = D.default || {};               // what the kit opens on when the link carries no choice (owner: GTA 6 first)
    method = methods().find((m) => m.id === (saved.get("m") || def.m)) || methods()[day % 3 === 2 && methods()[1] ? 1 : 0];
    tpl = D.templates.find((t) => t.id === (saved.get("t") || def.t)) || D.templates[day % D.templates.length];
    path = ready().find((p) => p.id === (saved.get("p") || def.p)) || ready()[day % ready().length];
    if (!usable(tpl)) { const us = D.templates.filter(usable); tpl = us[day % us.length]; }
    // A link's method/topic always wins over a format's lock (the hub, 2026-11-09: the bot's sheets link #m=like&p=highway,
    // and the kit's default GTA 6 countdown, locked to claude + gta6, used to undo it): a format whose lock agrees with
    // the link is kept or found, else an unlocked one
    const wantM = saved.get("m"), wantP = saved.get("p");
    const agrees = (t) => { const l = lockOf(t); return !l || ((!wantM || (l.m && l.m.id === wantM)) && (!wantP || !t.topic || (l.p && l.p.id === wantP))); };
    if ((wantM || wantP) && !agrees(tpl)) {
      const us = D.templates.filter((t) => usable(t) && agrees(t));
      tpl = us.find((t) => lockOf(t)) || us[0] || free() || tpl;
    }
    const l = lockOf(tpl);
    if (l) { method = l.m; path = (wantP && !tpl.topic && ready(l.m).find((x) => x.id === wantP)) || l.p; }
  }

  function player(ex) {
    const [plat, id, views, who] = ex;
    return `<div class="player" data-plat="${plat}" data-id="${esc(id)}"><img src="${thumb(plat, id)}" alt="">
      <button type="button" aria-label="Play the example">▶</button><small>${esc(views)} views · @${esc(who)}</small></div>`;
  }

  function renderToday() {
    const p = path, t = tpl, hooks = hooksFor(t), hook = pick(hooks, `hook:${t.id}`);
    const clips = clipsFor(t), clip = pick(clips, `clip:${feedOf(t)}`);
    clipNow = clip;
    const shots = shotsFor(t, method).map((s) => ({ ...s }));
    if (hook && hook.how === "spoken" && Number.isInteger(t.hook_shot) && shots[t.hook_shot]) shots[t.hook_shot].say = hook.line;
    const added = shots.findIndex((s) => s.kind === "added");
    if (clip && clip.react && added >= 0 && added + 1 !== t.hook_shot && shots[added + 1] && shots[added + 1].kind === "face") shots[added + 1].say = clip.react;
    const filmable = shots.filter((s) => s.kind !== "added").length;
    const key = `done:${new Date().toDateString()}:${t.id}:${p.id}`;
    const done = new Set(JSON.parse(store.get(key) || "[]"));
    const says = shots.map((s) => fill(s.say, p)).filter((s) => !s.startsWith("("));
    $("#today").innerHTML = `
      <div>${player(t.examples[0])}
        ${t.examples.length > 1 ? `<div class="others">${t.examples.map((e, i) => `<button type="button" data-ex="${i}">Example ${i + 1} · ${esc(e[2])}</button>`).join("")}</div>` : ""}
      </div>
      <div>
        <p class="label">Today's video</p>
        <h3>${esc(fill(t.name, p))}</h3>
        <div class="chips"><span class="chip">${esc(method.name)}: ${esc(p.name)}</span><span class="chip plain">${esc(t.length)}</span><span class="chip plain">${esc(t.tag)}</span></div>
        <p class="muted">${esc(t.why)} Watch the example first, then film these shots, in any order:</p>
        ${method.prompt && shots.some((s) => s.kind === "screen") ? claudeBox(p) : ""}
        ${hook ? `<div class="hook"><span>Your hook · ${hook.how === "screen" ? "we put it on screen" : t.hook_shot ? `say it in shot ${t.hook_shot + 1}` : "say it first"}</span>
          <b>${esc(fill(hook.line, p))}</b>${hooks.length > 1 ? `<button class="btn ghost small" type="button" id="next-h">↻ Another hook</button>` : ""}</div>` : ""}
        ${clip ? `<div class="react"><span>The clip you react to · we add it, you don't film it</span>
          <div class="yt ${clip.kind === "cr" && !clip.tall ? "wide" : ""}">${clip.kind === "cr"
            ? `<video src="${esc(clip.url)}" poster="${esc(clip.poster || "")}" muted autoplay loop playsinline controls></video>`
            : `<iframe src="https://www.youtube.com/embed/${esc(clip.id)}?start=${clip.start || 0}${t.clip_whole ? "" : `&end=${clip.end || 8}`}&rel=0" allow="encrypted-media; fullscreen" allowfullscreen loading="lazy"></iframe>`}</div>
          <small>${esc(clip.title)} · ${clip.kind === "cr" ? "made with ChannelRecipe" : `${esc(clip.channel)} · ${short(clip.views)} views`}</small>
          ${clips.length > 1 ? `<button class="btn ghost small" type="button" id="next-c">↻ Another clip</button>` : ""}</div>` : ""}
        <ol class="shots">${shots.map((s, i) => s.kind === "added" ? `
          <li class="shot we-add"><span class="plus">＋</span>
            <div><div class="t">${i + 1}. ${esc(s.what)}<small>${esc(s.secs)}</small><span class="kind">we add this</span></div>
            <p class="how">${esc(s.how)}</p></div></li>` : `
          <li class="shot ${done.has(i) ? "done" : ""}"><input type="checkbox" data-shot="${i}" ${done.has(i) ? "checked" : ""} aria-label="Filmed">
            <div><div class="t">${i + 1}. ${esc(s.what)}<small>${esc(s.secs)}</small><span class="kind">${s.kind === "screen" ? "film your laptop" : "your face"}</span></div>
            <p class="how">${esc(s.how)}</p><div class="say">${esc(fill(s.say, p))}</div>${s.kind === "screen" && method.prompt && !s.no_prompt ? promptBox(p) : ""}</div></li>`).join("")}</ol>
        ${dmBox(p)}
        <p class="added"><b>＋ The earnings proof: we add it for you.</b> Don't film it or edit it in. Your video comes back with it already in place.</p>
        <div class="actions">
          ${method.prompt ? `<a class="btn" href="https://claude.ai/new" target="_blank" rel="noopener">Open Claude →</a>
          <a class="btn ghost" href="studio#${esc(method.id)}/${esc(p.id)}" target="_blank" rel="noopener">Practice on the website</a>`
            : `<a class="btn" href="studio#${esc(method.id)}/${esc(p.id)}" target="_blank" rel="noopener">Open the walkthrough →</a>`}
          ${t.id === "dayn" && p.pathway ? `<a class="btn ghost" href="${MEDIA}wheel_${esc(p.pathway)}.mp4" target="_blank" rel="noopener">Open the wheel</a>` : ""}
          ${says.length ? `<button class="btn ghost" type="button" id="copy-says">Copy what to say</button>` : ""}
          <a class="btn ghost" href="${botLink(t, p, hook, clip)}" target="_blank" rel="noopener">Send my clips to the bot</a>
        </div>
        <div class="switch"><button class="btn ghost small" type="button" id="next-t">↻ Different video</button>
          <button class="btn ghost small" type="button" id="next-p">↻ Different topic</button></div>
      </div>`;
    $("#today").querySelectorAll("[data-ex]").forEach((b) => b.addEventListener("click", () => {
      $("#today .player").outerHTML = player(t.examples[+b.dataset.ex]);
    }));
    $("#today").querySelectorAll("[data-shot]").forEach((c) => c.addEventListener("change", () => {
      c.checked ? done.add(+c.dataset.shot) : done.delete(+c.dataset.shot);
      store.set(key, JSON.stringify([...done]));
      c.closest(".shot").classList.toggle("done", c.checked);
      if (done.size === filmable) toast("All filmed: send your clips to the bot!");
    }));
    $("#copy-says")?.addEventListener("click", () => copy(says.join("\n\n")));
    $("#setup-done")?.addEventListener("click", () => { store.set("claude-setup", "done"); renderToday(); });
    $("#setup-again")?.addEventListener("click", () => { store.set("claude-setup", ""); renderToday(); });
    $("#next-h")?.addEventListener("click", () => { store.set(`hook:${t.id}`, hooks.indexOf(hook) + 1); renderToday(); });
    $("#next-c")?.addEventListener("click", () => { store.set(`clip:${feedOf(t)}`, clips.indexOf(clip) + 1); renderToday(); });
    $("#next-t").addEventListener("click", () => { const us = D.templates.filter(usable); choose(us[(us.indexOf(t) + 1) % us.length], p); });
    $("#next-p").addEventListener("click", () => { const r = ready(); choose(t, r[(r.indexOf(p) + 1) % r.length]); });
    history.replaceState(null, "", `#m=${method.id}&t=${t.id}&p=${p.id}`);
    document.querySelectorAll(".tcard").forEach((c) => c.classList.toggle("on", c.dataset.t === t.id));
    document.querySelectorAll(".pcard").forEach((c) => c.classList.toggle("on", c.dataset.p === p.id));
    renderViral();
  }

  function choose(t, p, scroll = true, feed = null) {
    feedPin = feed;
    const l = lockOf(t);
    if (l && l.p) { p = t.topic ? l.p : ready(l.m).find((x) => x.id === (p && p.id)) || l.p; if (l.m !== method) { method = l.m; renderPaths(); } }
    tpl = t; path = p; renderToday();
    // (a phone jumps: a long smooth scroll was cut short by the re-render, leaving the creator at the old picker)
    if (scroll) $("#today").scrollIntoView({ behavior: PHONE() ? "auto" : "smooth", block: "start" });
  }

  function renderTemplates() {
    $("#templates").innerHTML = D.templates.map((t) => `
      <button class="tcard" type="button" data-t="${esc(t.id)}" ${usable(t) ? "" : "disabled"}><img src="${thumb(t.examples[0][0], t.examples[0][1])}" alt="" loading="lazy">
        <div><b>${esc(fill(t.name, path))}</b><small>${usable(t) ? `${esc(t.examples[0][2])} views · ${esc(t.tag)}` : "Coming soon: its demo video is being made"}</small></div></button>`).join("");
    $("#templates").addEventListener("click", (e) => {
      const c = e.target.closest(".tcard");
      if (c && !c.disabled) choose(D.templates.find((t) => t.id === c.dataset.t), path);
    });
  }

  function renderPaths() {
    const tabs = S.methods.map((m) => `<button class="btn ${m === method ? "" : "ghost"} small" type="button" data-m="${m.id}" ${m.soon ? "disabled" : ""}>${esc(m.name)}${m.soon ? " · soon" : ""}</button>`).join(" ");
    const cards = method.topics.map((p) => {
      const n = (p.outputs || []).length, tag = (D.pathways.find((x) => x.id === p.pathway) || {}).tag || "";
      return `<button class="pcard" type="button" data-p="${esc(p.id)}" ${n ? "" : "disabled"}>
        <div><small>${esc(tag)}</small><b>${esc(p.name)}</b><small class="state ${n ? "" : "wait"}">${n ? `${n} ready-made video${n > 1 ? "s" : ""}` : "Coming soon"}</small></div></button>`;
    }).join("");
    $("#paths").innerHTML = `<div style="grid-column:1/-1" class="switch">${tabs}</div><div class="pcards">${cards}</div>`;
    // the rows scroll sideways on a phone: show the chosen method and topic, not the start of the row
    for (const el of $("#paths").querySelectorAll(".switch .btn:not(.ghost), .pcard.on, .pcard[data-p=\"" + (path && path.id) + "\"]")) {
      const row = el.parentElement; if (row.scrollWidth > row.clientWidth) row.scrollLeft = el.offsetLeft - row.offsetLeft - 16;
    }
    $("#paths").onclick = (e) => {
      const mb = e.target.closest("[data-m]");
      // picking another method or topic leaves a locked format for a free one (the lock would undo the pick)
      const keep = (m, p) => { const l = lockOf(tpl); return !l || (l.m === m && (!tpl.topic || l.p === p)) ? tpl : free(); };
      if (mb && !mb.disabled) { method = S.methods.find((m) => m.id === mb.dataset.m); choose(keep(method, ready()[0]), ready()[0]); renderPaths(); return; }
      const c = e.target.closest(".pcard");
      if (c && !c.disabled) { const p = method.topics.find((x) => x.id === c.dataset.p); choose(keep(method, p), p); }
    };
  }

  function renderViral() {
    const refs = path.refs || [], clips = path.clips || [];
    if (!refs.length && clips.length) {   // ranking: the viral clips themselves, the kind the walkthrough ranks
      $("#viral").innerHTML = `<p class="muted"><b>${esc(path.name)}</b>: the clips going viral right now (the walkthrough ranks clips like these)</p>
        <ul>${clips.slice(0, 8).map((c) => `<li><img src="https://i.ytimg.com/vi/${esc(c.id)}/mqdefault.jpg" alt="" loading="lazy">
          <div><b>${esc(c.title)}</b><small>${short(c.views)} views</small>
          <div class="others"><a class="btn ghost small" href="https://www.youtube.com/shorts/${esc(c.id)}" target="_blank" rel="noopener">Open</a></div></div></li>`).join("")}</ul>`;
      return;
    }
    $("#viral").innerHTML = refs.length ? `<p class="muted"><b>${esc(path.name)}</b>: going viral this month (copy one of these channels in the walkthrough)</p>
      <ul>${refs.map((r) => `<li><img src="https://i.ytimg.com/vi/${esc(r.id)}/mqdefault.jpg" alt="" loading="lazy">
        <div><b>${esc(r.title)}</b><small>${esc(r.channel)} · ${short(r.views)} views</small>
        <div class="others"><a class="btn ghost small" href="${esc(r.url)}" target="_blank" rel="noopener">Open</a>
        <button class="btn ghost small" type="button" data-copy="${esc(r.channel_url)}">Copy channel link</button></div></div></li>`).join("")}</ul>` : "";
  }

  /* ---------- the outliers library (outliers.json: sandbox/outliers.py) ---------- */
  const ago = (iso) => { const d = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 864e5)); return d === 0 ? "today" : d === 1 ? "yesterday" : d < 60 ? `${d} days ago` : `${Math.round(d / 30)} months ago`; };
  const FORMAT_NAME = { watch: "Watch this", school: "Why don't they teach this", formula: "X + Shorts = 💰", lost: "If I lost everything",
    millionaire: "First millionaire", confess: "Embarrassing side hustle", tier: "Tier list", dayn: "Day N", pov: "POV", cook: "Watch me cook", none: "Other", "": "Other" };
  const ol = { sort: "x", format: "", plat: "", shown: 24 };
  function renderOutliers() {
    if (!O || !O.videos || !O.videos.length) return;
    $("#outliers-sec").hidden = false;
    const sel = $("#ol-format");
    if (sel.options.length === 1) {
      const counts = {}; O.videos.forEach((v) => { const f = v.format || "none"; counts[f] = (counts[f] || 0) + 1; });
      Object.entries(counts).sort((a, b) => b[1] - a[1]).forEach(([f, n]) => sel.add(new Option(`${FORMAT_NAME[f] || f} (${n})`, f)));
    }
    let list = O.videos.filter((v) => (!ol.format || (v.format || "none") === ol.format) && (!ol.plat || v.plat === ol.plat));
    list = list.slice().sort(ol.sort === "views" ? (a, b) => b.views - a.views : ol.sort === "new" ? (a, b) => b.posted.localeCompare(a.posted) : (a, b) => b.x - a.x);
    $("#outliers").innerHTML = list.slice(0, ol.shown).map((v, i) => `
      <div class="ol-card"><button type="button" class="ol-thumb" data-ol="${O.videos.indexOf(v)}"><img src="${esc(v.thumb)}" alt="" loading="lazy">
        <span class="ol-x">${v.x >= 2 ? `${v.x}× their usual` : "steady"}</span><span class="ol-play">▶</span></button>
        <div class="ol-meta"><b>${short(v.views)} views</b><small>${ago(v.posted)} · ${v.plat === "tt" ? "TikTok" : "Instagram"} · @${esc(v.handle)}</small>
        ${v.hook ? `<p>“${esc(v.hook.replace(/\s+/g, " ").slice(0, 90))}”</p>` : ""}
        <div class="ol-acts"><span class="chip plain">${esc(FORMAT_NAME[v.format || "none"] || v.format)}</span>
        ${D.templates.some((t) => t.id === v.format) ? `<button type="button" class="btn ghost small" data-remake="${esc(v.format)}" data-feed="${esc(v.react_feed || "")}">Remake this →</button>` : ""}</div></div></div>`).join("");
    $("#ol-more").hidden = list.length <= ol.shown;
  }
  function wireOutliers() {
    $("#ol-sort").addEventListener("click", (e) => { const b = e.target.closest("[data-s]"); if (!b) return; ol.sort = b.dataset.s; ol.shown = 24;
      $("#ol-sort").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); renderOutliers(); });
    $("#ol-format").addEventListener("change", (e) => { ol.format = e.target.value; ol.shown = 24; renderOutliers(); });
    $("#ol-plat").addEventListener("change", (e) => { ol.plat = e.target.value; ol.shown = 24; renderOutliers(); });
    $("#ol-more").addEventListener("click", () => { ol.shown += 24; renderOutliers(); });
    $("#outliers").addEventListener("click", (e) => {
      const play = e.target.closest("[data-ol]");
      if (play) { const v = O.videos[+play.dataset.ol];
        $("#ol-play").innerHTML = `<iframe src="${embed(v.plat, v.id)}" allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>
          <a class="btn ghost small" href="${esc(v.url)}" target="_blank" rel="noopener">Open on ${v.plat === "tt" ? "TikTok" : "Instagram"} ↗</a>`;
        $("#ol-modal").hidden = false; }
      const rm = e.target.closest("[data-remake]");
      if (rm) choose(D.templates.find((t) => t.id === rm.dataset.remake), path, true, rm.dataset.feed || null);
    });
    $("#ol-modal").addEventListener("click", (e) => { if (e.target.id === "ol-modal" || e.target.closest(".ol-x")) { $("#ol-modal").hidden = true; $("#ol-play").innerHTML = ""; } });
  }

  function renderInspo() {
    $("#inspo").innerHTML = (D.inspiration || []).map((i) => `<li><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.name)} ↗</a><p>${esc(i.what)}</p></li>`).join("");
  }

  document.addEventListener("click", (e) => {
    const play = e.target.closest(".player button");
    if (play) {
      const box = play.closest(".player");
      box.innerHTML = `<iframe src="${embed(box.dataset.plat, box.dataset.id)}" allow="autoplay; encrypted-media; fullscreen" allowfullscreen loading="lazy"></iframe>`;
    }
    const c = e.target.closest("[data-copy]");
    if (c) copy(c.dataset.copy);
  });

  // On a phone the method and topic picker sits right above today's video, as two rows that scroll sideways (the hub,
  // 2026-11-09: it was ~18 screens down, under the viral list); the viral list stays where it was.
  const PHONE = () => window.matchMedia("(max-width:700px)").matches;
  function pickerUp() {
    if (!PHONE() || $(".picker-top")) return;
    const sec = document.createElement("section");
    sec.className = "block picker-top";
    sec.innerHTML = `<h2>Your walkthrough and its topic</h2><p class="muted">Tap one: the shots below change to it.</p>`;
    sec.append($("#paths"));
    $("#today").before(sec);
  }

  async function main() {
    D = await json("pathways.json");
    S = await json("strategies.json");
    R = await json("react.json");
    O = await json("outliers.json");
    if (D.tutorial) { const t = $("#tutorial"); t.hidden = false; $("video", t).src = D.tutorial; $("video", t).poster = D.tutorial.replace(/\.mp4$/, ".jpg"); if (!store.get("seen-tutorial")) t.open = true; t.addEventListener("toggle", () => store.set("seen-tutorial", "1")); }
    pickToday();
    pickerUp();
    renderTemplates(); renderPaths(); renderInspo(); renderToday();
    if (O) { wireOutliers(); renderOutliers(); }
    document.querySelectorAll(".pcard").forEach((c) => c.classList.toggle("on", c.dataset.p === path.id));
  }
  main();
})();
