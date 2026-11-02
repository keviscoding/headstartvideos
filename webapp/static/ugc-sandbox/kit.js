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
  let D, T, S, R, tpl, path, method;

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
  function words(t, m) {
    const pw = D.pathways.find((x) => x.id === t.pathway);
    return pw ? { short: pw.short, search: pw.search, keyword: pw.keyword, idea: t.outputs[0].title, name: t.name }
      : { short: t.name.toLowerCase(), search: t.search || t.name, keyword: m.keyword, idea: t.name, name: t.name };
  }
  const top = (t) => (t.refs || [])[0] || (t.clips || []).map((c) => ({ channel: "a ranking channel", views: c.views }))[0];
  function fill(s, t, m = method) {
    const w = words(t, m), r = top(t);
    return String(s).replaceAll("{short}", w.short).replaceAll("{search}", w.search)
      .replaceAll("{channel}", r ? r.channel : "this channel").replaceAll("{views}", r ? short(r.views) : "millions of")
      .replaceAll("{topic}", w.idea).replaceAll("{keyword}", w.keyword).replaceAll("{n}", "N");
  }
  const shotsFor = (tp, m) => tp.shots.map((s) => s.what === "The walkthrough" && m.walk_say ? { ...s, say: m.walk_say } : s);
  const embed = (plat, id) => plat === "tt" ? `https://www.tiktok.com/embed/v2/${id}` : `https://www.instagram.com/reel/${id}/embed/`;
  const thumb = (plat, id) => `${MEDIA}ex_${plat === "tt" ? "tt" : "ig"}-${id}.jpg`;

  // The hook bank (pathways.json "hooks"): the format's families, the method's lane; each creator's browser rotates
  // through them ("↻ another hook") and remembers where it was, so creators don't all post the same words.
  const lane = (m = method) => (m.id === "clipping" ? "clipping" : m.id === "kids" ? "kids" : "any");
  function hooksFor(t) {
    const fams = t.hook_families || [];
    return (D.hooks || []).filter((h) => fams.includes(h.family) && (h.lanes.includes("any") || h.lanes.includes(lane())));
  }
  function pick(list, key) {
    if (!list.length) return null;
    let i = +(store.get(key) ?? NaN);
    if (!Number.isFinite(i)) { i = Math.floor(Math.random() * list.length); store.set(key, i); }
    return list[((i % list.length) + list.length) % list.length];
  }
  const feedOf = (t) => (method.id === "clipping" && t.react ? "clipping" : t.react);
  const clipsFor = (t) => (R && t.react && R.feeds[feedOf(t)] ? R.feeds[feedOf(t)].clips : []);
  // the bot opens the kit straight from this link and learns the format, hook and clip the creator used
  const botLink = (t, p, h, c) => `${BOT}?start=kit_walk-${p.pathway || p.id}__${t.id}` +
    (c ? `__${c.id}` : "") + (h ? `__h${(D.hooks || []).indexOf(h)}` : "");

  function pickToday() {
    const day = Math.floor(Date.now() / 864e5);
    const saved = new URLSearchParams(location.hash.slice(1));
    method = methods().find((m) => m.id === saved.get("m")) || methods()[day % 3 === 2 && methods()[1] ? 1 : 0];
    tpl = D.templates.find((t) => t.id === saved.get("t")) || D.templates[day % D.templates.length];
    path = ready().find((p) => p.id === saved.get("p")) || ready()[day % ready().length];
  }

  function player(ex) {
    const [plat, id, views, who] = ex;
    return `<div class="player" data-plat="${plat}" data-id="${esc(id)}"><img src="${thumb(plat, id)}" alt="">
      <button type="button" aria-label="Play the example">▶</button><small>${esc(views)} views · @${esc(who)}</small></div>`;
  }

  function renderToday() {
    const p = path, t = tpl, hooks = hooksFor(t), hook = pick(hooks, `hook:${t.id}`);
    const clips = clipsFor(t), clip = pick(clips, `clip:${feedOf(t)}`);
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
        ${hook ? `<div class="hook"><span>Your hook · ${hook.how === "screen" ? "we put it on screen" : t.hook_shot ? `say it in shot ${t.hook_shot + 1}` : "say it first"}</span>
          <b>${esc(fill(hook.line, p))}</b>${hooks.length > 1 ? `<button class="btn ghost small" type="button" id="next-h">↻ Another hook</button>` : ""}</div>` : ""}
        ${clip ? `<div class="react"><span>The clip you react to · we add it, you don't film it</span>
          <div class="yt"><iframe src="https://www.youtube.com/embed/${esc(clip.id)}?start=${clip.start || 0}&end=${clip.end || 8}&rel=0" allow="encrypted-media; fullscreen" allowfullscreen loading="lazy"></iframe></div>
          <small>${esc(clip.title)} · ${esc(clip.channel)} · ${short(clip.views)} views</small>
          ${clips.length > 1 ? `<button class="btn ghost small" type="button" id="next-c">↻ Another clip</button>` : ""}</div>` : ""}
        <ol class="shots">${shots.map((s, i) => s.kind === "added" ? `
          <li class="shot we-add"><span class="plus">＋</span>
            <div><div class="t">${i + 1}. ${esc(s.what)}<small>${esc(s.secs)}</small><span class="kind">we add this</span></div>
            <p class="how">${esc(s.how)}</p></div></li>` : `
          <li class="shot ${done.has(i) ? "done" : ""}"><input type="checkbox" data-shot="${i}" ${done.has(i) ? "checked" : ""} aria-label="Filmed">
            <div><div class="t">${i + 1}. ${esc(s.what)}<small>${esc(s.secs)}</small><span class="kind">${s.kind === "screen" ? "film your laptop" : "your face"}</span></div>
            <p class="how">${esc(s.how)}</p><div class="say">${esc(fill(s.say, p))}</div></div></li>`).join("")}</ol>
        <p class="added"><b>＋ The earnings proof: we add it for you.</b> Don't film it or edit it in. Your video comes back with it already in place.</p>
        <div class="actions">
          <a class="btn" href="studio#${esc(method.id)}/${esc(p.id)}" target="_blank" rel="noopener">Open the walkthrough →</a>
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
    $("#next-h")?.addEventListener("click", () => { store.set(`hook:${t.id}`, hooks.indexOf(hook) + 1); renderToday(); });
    $("#next-c")?.addEventListener("click", () => { store.set(`clip:${feedOf(t)}`, clips.indexOf(clip) + 1); renderToday(); });
    $("#next-t").addEventListener("click", () => choose(D.templates[(D.templates.indexOf(t) + 1) % D.templates.length], p));
    $("#next-p").addEventListener("click", () => { const r = ready(); choose(t, r[(r.indexOf(p) + 1) % r.length]); });
    history.replaceState(null, "", `#m=${method.id}&t=${t.id}&p=${p.id}`);
    document.querySelectorAll(".tcard").forEach((c) => c.classList.toggle("on", c.dataset.t === t.id));
    document.querySelectorAll(".pcard").forEach((c) => c.classList.toggle("on", c.dataset.p === p.id));
    renderViral();
  }

  function choose(t, p, scroll = true) {
    tpl = t; path = p; renderToday();
    if (scroll) $("#today").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderTemplates() {
    $("#templates").innerHTML = D.templates.map((t) => `
      <button class="tcard" type="button" data-t="${esc(t.id)}"><img src="${thumb(t.examples[0][0], t.examples[0][1])}" alt="" loading="lazy">
        <div><b>${esc(fill(t.name, path))}</b><small>${esc(t.examples[0][2])} views · ${esc(t.tag)}</small></div></button>`).join("");
    $("#templates").addEventListener("click", (e) => {
      const c = e.target.closest(".tcard");
      if (c) choose(D.templates.find((t) => t.id === c.dataset.t), path);
    });
  }

  function renderPaths() {
    const tabs = S.methods.map((m) => `<button class="btn ${m === method ? "" : "ghost"} small" type="button" data-m="${m.id}" ${m.soon ? "disabled" : ""}>${esc(m.name)}${m.soon ? " · soon" : ""}</button>`).join(" ");
    const cards = method.topics.map((p) => {
      const n = (p.outputs || []).length, tag = (D.pathways.find((x) => x.id === p.pathway) || {}).tag || "";
      return `<button class="pcard" type="button" data-p="${esc(p.id)}" ${n ? "" : "disabled"}>
        <div><small>${esc(tag)}</small><b>${esc(p.name)}</b><small class="state ${n ? "" : "wait"}">${n ? `${n} ready-made video${n > 1 ? "s" : ""}` : "Coming soon"}</small></div></button>`;
    }).join("");
    $("#paths").innerHTML = `<div style="grid-column:1/-1" class="switch">${tabs}</div>${cards}`;
    $("#paths").onclick = (e) => {
      const mb = e.target.closest("[data-m]");
      if (mb && !mb.disabled) { method = S.methods.find((m) => m.id === mb.dataset.m); choose(tpl, ready()[0]); renderPaths(); return; }
      const c = e.target.closest(".pcard");
      if (c && !c.disabled) choose(tpl, method.topics.find((p) => p.id === c.dataset.p));
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

  async function main() {
    D = await json("pathways.json");
    S = await json("strategies.json");
    R = await json("react.json");
    if (D.tutorial) { const t = $("#tutorial"); t.hidden = false; $("video", t).src = D.tutorial; $("video", t).poster = D.tutorial.replace(/\.mp4$/, ".jpg"); if (!store.get("seen-tutorial")) t.open = true; t.addEventListener("toggle", () => store.set("seen-tutorial", "1")); }
    pickToday();
    renderTemplates(); renderPaths(); renderInspo(); renderToday();
    document.querySelectorAll(".pcard").forEach((c) => c.classList.toggle("on", c.dataset.p === path.id));
  }
  main();
})();
