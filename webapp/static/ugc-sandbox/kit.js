/* The creator kit (not filmed): what's going viral per pathway, the channels to copy, the hooks, the walkthrough
   script with the real channel and numbers filled in, and the button that opens the Studio walkthrough to film. */
(() => {
  "use strict";
  const BASE = "/ugc-sandbox/";
  const CDN_TRENDING = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/ugc-sandbox/trending.json";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const views = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n || 0);
  let data, trend;

  function toast(t) {
    const d = document.createElement("div");
    d.className = "toast"; d.textContent = t; document.body.append(d);
    setTimeout(() => d.remove(), 1400);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { prompt("Copy this:", text); }
  }

  function fill(s, p, top) {
    return s.replaceAll("{search}", p.search).replaceAll("{channel}", top ? top.channel : "this channel")
      .replaceAll("{views}", top ? views(top.views) : "millions of").replaceAll("{topic}", p.ideas[0] || p.topic)
      .replaceAll("{keyword}", p.keyword).replaceAll("{n}", "1");
  }

  function cards() {
    $("#paths").innerHTML = data.pathways.map((p) => `
      <button class="path" type="button" data-id="${esc(p.id)}">
        <span class="tag">${esc(p.tag)}</span><b>${esc(p.name)}</b><p>${esc(p.why)}</p>
        <span class="state ${p.result ? "" : "wait"}">${p.result ? "Ready to film" : "Video being made"}</span>
      </button>`).join("");
    $("#paths").addEventListener("click", (e) => {
      const b = e.target.closest(".path");
      if (b) open(b.dataset.id, true);
    });
  }

  function open(id, scroll) {
    const p = data.pathways.find((x) => x.id === id);
    if (!p) return;
    document.querySelectorAll(".path").forEach((b) => b.classList.toggle("on", b.dataset.id === id));
    const refs = (trend.pathways || {})[id] || [];
    const top = refs[0];
    const d = $("#detail");
    d.innerHTML = `
      <h2>${esc(p.name)}</h2><p class="muted">${esc(p.why)}</p>
      <div class="cols">
        <div>
          <h3>1. What's going viral right now</h3>
          <p class="muted">Search <code>${esc(p.search)}</code> on YouTube, or open one of these. Copy the channel's link for the walkthrough.</p>
          <ul class="refs" id="refs"></ul>
          <h3>2. Your hook (pick one, say it your way)</h3>
          <ul class="hooks">${p.hooks.map((h) => `<li><div>${esc(fill(h.line, p, top))}<small>${esc(h.evidence)}</small></div>
            <button class="btn ghost" type="button" data-copy="${esc(fill(h.line, p, top))}">Copy</button></li>`).join("")}</ul>
        </div>
        <div>
          <h3>3. The walkthrough, step by step</h3>
          <ol class="steps">${p.steps.map((s, i) => `<li><span>${i + 1}</span><div>${esc(fill(s.say, p, top))}</div></li>`).join("")}</ol>
          <div class="callout">
            <p><b>Film it:</b> open the walkthrough on your laptop, film the screen with your phone and point at it.</p>
            <p>In the idea box, press <code>Tab</code> and it types <i>${esc(p.ideas[0] || p.topic)}</i> for you. <code>Esc</code> starts over.</p>
            <p class="row">${p.result ? `<a class="btn big" href="studio#${esc(p.id)}" target="_blank" rel="noopener">Open the walkthrough →</a>`
              : `<button class="btn big" type="button" disabled>Video being made: back soon</button>`}</p>
          </div>
          <h3>4. Close and caption</h3>
          <p>Say: <b>“Comment ${esc(p.keyword)} and I'll send you the full guide.”</b></p>
          <p class="muted">Then send your hook, your screen recording and your close to the bot in Telegram, tap Done, and post what comes back.</p>
        </div>
      </div>`;
    const list = $("#refs", d), tpl = $("#t-ref");
    if (!refs.length) list.innerHTML = `<li class="muted">Search <code>${esc(p.search)}</code> on YouTube and sort by view count.</li>`;
    for (const r of refs) {
      const li = tpl.content.firstElementChild.cloneNode(true);
      li.querySelector("img").src = `https://i.ytimg.com/vi/${r.id}/mqdefault.jpg`;
      li.querySelector("strong").textContent = r.title;
      li.querySelector("small").textContent = `${r.channel} · ${views(r.views)} views${r.window === "month" ? " · this month" : ""}`;
      li.querySelector("a").href = r.url;
      li.querySelector(".copy-chan").addEventListener("click", () => copy(r.channel_url));
      list.append(li);
    }
    d.addEventListener("click", (e) => { const b = e.target.closest("[data-copy]"); if (b) copy(b.dataset.copy); });
    d.hidden = false;
    history.replaceState(null, "", "#" + id);
    if (scroll) d.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function main() {
    data = await fetch(BASE + "pathways.json").then((r) => r.json());
    try { trend = await fetch(`${CDN_TRENDING}?h=${Math.floor(Date.now() / 36e5)}`).then((r) => r.ok ? r.json() : null); } catch (e) { trend = null; }
    if (!trend) trend = await fetch(BASE + "trending.json").then((r) => r.json()).catch(() => ({ pathways: {} }));
    $("#updated").textContent = trend.updated ? `What's viral, updated ${trend.updated}` : "";
    cards();
    open(location.hash.replace("#", "") || data.pathways[0].id, false);
    window.addEventListener("hashchange", () => open(location.hash.replace("#", ""), true));
  }
  main();
})();
