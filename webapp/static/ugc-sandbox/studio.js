/* The creator sandbox's Studio walkthrough: Studio's own "Clone a channel's style" page (studio.html is built from it),
   driven with a pathway's data so a UGC creator can film the whole method in a minute: paste a viral channel, see what
   Studio saw, type their idea, build, and the finished video plays (made ahead with ChannelRecipe, so nobody waits).
   studio.html#gta6 picks the pathway. Creator shortcuts: Tab in the idea box types the pathway's idea; Esc starts over. */
(() => {
  "use strict";
  const BASE = "/ugc-sandbox/";
  const CDN = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/ugc-sandbox/";
  const fresh = () => `?h=${Math.floor(Date.now() / 6e5)}`;   // the data changes daily; the CDN keeps it 10 min
  const fast = /[?&]speed=fast/.test(location.search);
  const T = (ms) => new Promise((r) => setTimeout(r, fast ? ms / 3 : ms));
  const $ = (s) => document.querySelector(s);
  const show = (el, on = true) => { if (el) el.hidden = !on; };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const views = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M views` : n >= 1e3 ? `${Math.round(n / 1e3)}K views` : `${n} views`;

  let P = null, refs = [], picked = null;

  async function load() {
    const id = location.hash.replace("#", "") || "gta6";
    const json = async (name) => {                     // the CDN on channelrecipe.com; the page's own folder in local preview
      for (const url of [CDN + name + fresh(), BASE + name]) {
        try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) { /* next */ }
      }
      return null;
    };
    const data = await json("pathways.json");
    let trend = null;
    trend = await json("trending.json");
    trend = trend || { pathways: {} };
    P = data.pathways.find((p) => p.id === id) || data.pathways[0];
    refs = (trend.pathways || {})[P.id] || [];
  }

  function stepTo(n) {
    // the tracker's steps in the order they're shown (their ids aren't in that order on Studio's page)
    const order = [...document.querySelectorAll(".step")].sort((a, b) => a.textContent.trim().localeCompare(b.textContent.trim()));
    order.forEach((el, i) => el.classList.toggle("active", i === n - 1));
    for (const [k, id] of [[1, "#reference-panel"], [2, "#look-panel"], [3, "#brief-panel"], [4, "#progress-panel"]]) show($(id), k === n);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function handleOf(url) {
    const m = String(url).match(/youtube\.com\/(@[\w.\-]+)/i);
    return m ? m[1] : "";
  }

  function match(url) {
    const u = String(url).toLowerCase();
    const h = handleOf(url).toLowerCase();
    return refs.find((r) => (r.channel_url && u.includes(r.channel_url.toLowerCase().split("/").pop())) ||
      (r.id && u.includes(r.id.toLowerCase())) || (h && r.channel.toLowerCase().replace(/\s+/g, "") === h.slice(1)));
  }

  // 01: preview the pasted channel
  async function preview(e) {
    e.preventDefault();
    const url = $("#reference-url").value.trim();
    if (!url) { $("#reference-url").focus(); return; }
    const btn = $("#preview-button");
    btn.disabled = true; btn.textContent = "Reading the channel…";
    await T(1400);
    picked = match(url) || refs[0] || { channel: handleOf(url) || "this channel", title: "", views: 0 };
    const name = handleOf(url) || picked.channel;
    const cards = [picked, ...refs.filter((r) => r !== picked)].slice(0, 3);
    $("#candidate-list").innerHTML = cards.map((r, i) => `
      <label class="reference"><input type="checkbox" ${i === 0 ? "checked" : ""}>
        <img src="https://i.ytimg.com/vi/${esc(r.id)}/mqdefault.jpg" alt="" width="128" height="72" style="border-radius:8px;object-fit:cover;flex-shrink:0" onerror="this.remove()">
        <div><strong>${esc(r.title || name)}</strong><small>${esc(i === 0 ? name : r.channel)}${r.views ? " · " + views(r.views) : ""}</small></div></label>`).join("");
    $("#selection-summary").textContent = "1 video selected. Studio learns what stays the same across the videos you pick.";
    show($("#references"));
    $("#next-button").disabled = false;
    btn.disabled = false; btn.textContent = "Preview references";
    $("#next-button").focus();
  }

  // 02: what Studio saw
  async function look() {
    stepTo(2);
    show($("#look-working")); show($("#look-result"), false); show($("#look-failed-actions"), false);
    const bar = $("#look-progress"), phase = $("#look-phase"), msg = $("#look-message");
    const steps = ["Downloading a few minutes of each video", "Measuring cuts, pace and colour", "Gemini is watching the videos", "Reading the frames"];
    for (let i = 0; i < steps.length; i++) {
      phase.textContent = steps[i]; msg.textContent = "";
      if (bar) { bar.max = 100; bar.value = Math.round((i + 1) * 100 / steps.length); }
      await T(900);
    }
    show($("#look-working"), false);
    $("#look-heading").textContent = `What Studio saw in ${picked.channel}`;
    $("#look-base").textContent = `Recommended recipe: ${P.recipe}`;
    $("#look-base-why").textContent = "Its workflow fits what Studio saw. The learned style replaces its pictures, pace, look and sound.";
    $("#look-recognisable").innerHTML = (P.look || []).map((l) => `<li>${esc(l)}</li>`).join("");
    $("#look-cost").textContent = "Free: nothing has run on your Claude account and nothing is charged.";
    for (const id of ["#similar-panel", "#look-missing", "#look-unmeasured", "#look-route"]) show($(id), false);
    if ($("#look-questions")) $("#look-questions").innerHTML = "";
    if ($("#look-settings")) $("#look-settings").innerHTML = "";
    const frames = (P.result && P.result.frames) || [];
    $("#look-sheets").innerHTML = frames.map((f) => `<img src="${esc(f)}" alt="A frame Studio looked at" loading="lazy">`).join("");
    show($("#look-result"));
    $("#look-sheets").closest("details")?.setAttribute("open", "");
    $("#look-next").disabled = false;
  }

  // 03: make it yours
  function brief() {
    stepTo(3);
    $("#style-label").value = `${picked.channel} style`;
    const t = $("#example-title");
    t.value = ""; t.placeholder = P.ideas[0] || P.topic;
    $("#title-idea-list").innerHTML = (P.ideas || []).map((i) => `<button type="button">${esc(i)}</button>`).join("");
    show($("#title-ideas"));
    const sel = $("#base-style");
    sel.innerHTML = `<option selected>${esc(P.recipe)}</option>`;
    $("#base-style-note").textContent = "Studio recommends the recipe whose workflow fits what it saw.";
    show($("#medium-gap"), false);
    if ($("#visual-mode-row")) show($("#visual-mode-row"), false);
    if ($("#likeness-cost")) $("#likeness-cost").textContent = "";
    $("#start-button").disabled = false;
    t.focus();
  }

  async function typeInto(el, text) {
    el.value = "";
    for (const ch of text) { el.value += ch; await new Promise((r) => setTimeout(r, 28 + Math.random() * 40)); }
  }

  // 04: study & build, then the video
  async function build(e) {
    e.preventDefault();
    const title = $("#example-title").value.trim() || P.topic;
    stepTo(4);
    show($("#saved-result"), false);
    for (const id of ["#metadata-recovery-note", "#metadata-recover-button", "#sync-button", "#recovery-link", "#prepared-recovery", "#keep-open-note"]) show($(id), false);
    const bar = $("#study-progress"), label = $("#phase-label"), msg = $("#progress-message"), det = $("#progress-details");
    const steps = [["Studying the references", "Claude looks at the frames Studio saved."], ["Writing the style recipe", "Pictures, pacing, colour, captions and sound."],
      ["Writing your example", `“${title}”`], ["Recording the narration", "One voice, the channel's pace."],
      ["Making the pictures", "Every scene in the learned style."], ["Rendering your example", "Putting it together."]];
    $("#status-dot").className = "dot spinner";
    for (let i = 0; i < steps.length; i++) {
      label.textContent = steps[i][0]; msg.textContent = steps[i][1]; det.textContent = `Step ${i + 1} of ${steps.length}`;
      if (bar) { bar.max = 100; bar.value = Math.round((i + 0.5) * 100 / steps.length); }
      await T(1900);
    }
    if (bar) bar.value = 100;
    $("#status-dot").className = "dot done";
    label.textContent = "Done"; msg.textContent = ""; det.textContent = "";
    $("#progress-heading").textContent = "Your style is ready";
    $("#saved-label").textContent = title;
    $("#used-reference").textContent = `Learned from ${picked.channel}. Saved to your Style library as “${$("#style-label").value}”.`;
    $("#caption-evidence").textContent = "";
    let v = $("#sb-video");
    if (!v) {
      v = document.createElement("video");
      v.id = "sb-video"; v.controls = true; v.playsInline = true;
      v.style.cssText = "display:block;width:100%;max-height:72vh;margin:16px 0;border-radius:14px;background:#000";
      $("#saved-label").after(v);
    }
    if (P.result) { v.src = P.result.video; v.poster = P.result.poster; }
    const use = $("#use-style");
    if (use) { use.textContent = "Make a video with it →"; use.href = "#"; }
    for (const id of ["#edit-style", "#board-button", "#style-board", "#likeness-panel", "#renderer-panel", "#graphics-panel", "#improve-panel"]) show($(id), false);
    show($("#saved-result"));
    v.scrollIntoView({ behavior: "smooth", block: "center" });
    try { await v.play(); } catch (err) { v.muted = true; v.play().catch(() => {}); }
  }

  function restart() {
    $("#reference-url").value = ""; show($("#references"), false);
    $("#progress-heading").textContent = "Your style is taking shape";
    const v = $("#sb-video"); if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
    stepTo(1); $("#reference-url").focus();
  }

  async function main() {
    await load();
    document.title = "Learn a channel style · ChannelRecipe";
    show($("#notice"), false);
    if (!P.result) {
      const n = $("#notice");
      if (n) { n.textContent = "This pathway's video is still being made. Pick another one in your creator kit."; show(n); }
    }
    $("#reference-form").addEventListener("submit", preview);
    $("#preview-button").addEventListener("click", preview);
    $("#next-button").addEventListener("click", (e) => { e.preventDefault(); look(); });
    $("#look-next").addEventListener("click", (e) => { e.preventDefault(); brief(); });
    $("#look-back").addEventListener("click", (e) => { e.preventDefault(); stepTo(1); });
    $("#back-button").addEventListener("click", (e) => { e.preventDefault(); stepTo(1); });
    $("#study-form").addEventListener("submit", build);
    $("#start-button").addEventListener("click", build);
    $("#title-idea-list").addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") typeInto($("#example-title"), e.target.textContent); });
    $("#example-title").addEventListener("keydown", (e) => {
      if (e.key === "Tab" && !e.target.value) { e.preventDefault(); typeInto(e.target, P.ideas[0] || P.topic); }
    });
    for (const id of ["#usage-approved", "#accept-medium"]) { const c = $(id); if (c) c.checked = true; }
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") restart(); });
    window.addEventListener("hashchange", () => location.reload());
    stepTo(1);
  }

  main();
})();
