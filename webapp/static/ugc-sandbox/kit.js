/* The creator kit (owner, 2026-11-01: "even a grandma could do it"): today's video is already picked, a real winning
   example plays right there, and the shots are listed with exactly what to say, filled in for today's topic. Creators
   can switch to another video to remake or another topic; the viral channels for the topic sit below, then places to
   find more inspiration. Data: the CDN's pathways.json + trending.json (sandbox/pathways.py, scout.py). */
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
  let D, T, tpl, path;

  const toast = (t) => { const d = document.createElement("div"); d.className = "toast"; d.textContent = t; document.body.append(d); setTimeout(() => d.remove(), 1500); };
  async function copy(text) { try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { prompt("Copy this:", text); } }

  async function json(name) {
    for (const url of [CDN + name + fresh(), BASE + name]) {
      try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) { /* next */ }
    }
    return null;
  }

  const ready = () => D.pathways.filter((p) => p.result);
  const top = (p) => ((T.pathways || {})[p.id] || [])[0];
  function fill(s, p) {
    const t = top(p);
    return String(s).replaceAll("{short}", p.short).replaceAll("{search}", p.search)
      .replaceAll("{channel}", t ? t.channel : "this channel").replaceAll("{views}", t ? short(t.views) : "millions of")
      .replaceAll("{topic}", p.ideas[0] || p.topic).replaceAll("{keyword}", p.keyword).replaceAll("{n}", "N");
  }
  const embed = (plat, id) => plat === "tt" ? `https://www.tiktok.com/embed/v2/${id}` : `https://www.instagram.com/reel/${id}/embed/`;
  const thumb = (plat, id) => `${MEDIA}ex_${plat === "tt" ? "tt" : "ig"}-${id}.jpg`;

  function pickToday() {
    const day = Math.floor(Date.now() / 864e5);
    const saved = new URLSearchParams(location.hash.slice(1));
    tpl = D.templates.find((t) => t.id === saved.get("t")) || D.templates[day % D.templates.length];
    path = ready().find((p) => p.id === saved.get("p")) || ready()[day % ready().length];
  }

  function player(ex) {
    const [plat, id, views, who] = ex;
    return `<div class="player" data-plat="${plat}" data-id="${esc(id)}"><img src="${thumb(plat, id)}" alt="">
      <button type="button" aria-label="Play the example">▶</button><small>${esc(views)} views · @${esc(who)}</small></div>`;
  }

  function renderToday() {
    const p = path, t = tpl;
    const key = `done:${new Date().toDateString()}:${t.id}:${p.id}`;
    const done = new Set(JSON.parse(store.get(key) || "[]"));
    const says = t.shots.map((s) => fill(s.say, p)).filter((s) => !s.startsWith("("));
    $("#today").innerHTML = `
      <div>${player(t.examples[0])}
        ${t.examples.length > 1 ? `<div class="others">${t.examples.map((e, i) => `<button type="button" data-ex="${i}">Example ${i + 1} · ${esc(e[2])}</button>`).join("")}</div>` : ""}
      </div>
      <div>
        <p class="label">Today's video</p>
        <h3>${esc(fill(t.name, p))}</h3>
        <div class="chips"><span class="chip">About: ${esc(p.name)}</span><span class="chip plain">${esc(t.length)}</span><span class="chip plain">${esc(t.tag)}</span></div>
        <p class="muted">${esc(t.why)} Watch the example first, then film these shots, in any order:</p>
        <ol class="shots">${t.shots.map((s, i) => `
          <li class="shot ${done.has(i) ? "done" : ""}"><input type="checkbox" data-shot="${i}" ${done.has(i) ? "checked" : ""} aria-label="Filmed">
            <div><div class="t">${i + 1}. ${esc(s.what)}<small>${esc(s.secs)}</small><span class="kind">${s.kind === "screen" ? "film your laptop" : "your face"}</span></div>
            <p class="how">${esc(s.how)}</p><div class="say">${esc(fill(s.say, p))}</div></div></li>`).join("")}</ol>
        <div class="actions">
          <a class="btn" href="studio#${esc(p.id)}" target="_blank" rel="noopener">Open the walkthrough →</a>
          ${t.id === "dayn" ? `<a class="btn ghost" href="${MEDIA}wheel_${esc(p.id)}.mp4" target="_blank" rel="noopener">Open the wheel</a>` : ""}
          ${says.length ? `<button class="btn ghost" type="button" id="copy-says">Copy what to say</button>` : ""}
          <a class="btn ghost" href="${BOT}" target="_blank" rel="noopener">Send my clips to the bot</a>
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
      if (done.size === t.shots.length) toast("All filmed: send your clips to the bot!");
    }));
    $("#copy-says")?.addEventListener("click", () => copy(says.join("\n\n")));
    $("#next-t").addEventListener("click", () => choose(D.templates[(D.templates.indexOf(t) + 1) % D.templates.length], p));
    $("#next-p").addEventListener("click", () => { const r = ready(); choose(t, r[(r.indexOf(p) + 1) % r.length]); });
    history.replaceState(null, "", `#t=${t.id}&p=${p.id}`);
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
    $("#paths").innerHTML = D.pathways.map((p) => `
      <button class="pcard" type="button" data-p="${esc(p.id)}" ${p.result ? "" : "disabled"}>
        <div><small>${esc(p.tag)}</small><b>${esc(p.name)}</b><small class="state ${p.result ? "" : "wait"}">${p.result ? "Ready" : "Coming soon"}</small></div></button>`).join("");
    $("#paths").addEventListener("click", (e) => {
      const c = e.target.closest(".pcard");
      if (c && !c.disabled) choose(tpl, D.pathways.find((p) => p.id === c.dataset.p));
    });
  }

  function renderViral() {
    const refs = (T.pathways || {})[path.id] || [];
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
    T = (await json("trending.json")) || { pathways: {} };
    if (D.tutorial) { const t = $("#tutorial"); t.hidden = false; $("video", t).src = D.tutorial; if (!store.get("seen-tutorial")) t.open = true; t.addEventListener("toggle", () => store.set("seen-tutorial", "1")); }
    pickToday();
    renderTemplates(); renderPaths(); renderInspo(); renderToday();
  }
  main();
})();
