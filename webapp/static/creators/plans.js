/* Plan cards for the landing page and the app's upgrade dialog. Prices and edit counts are DRAFTS from
   big-frontier/PLAN.md (trial converts to the existing $49 plan); the owner sets the real numbers. */
(function () {
  var PLANS = [
    { id: "free", name: "Free", for: "See it on your own footage", month: 0, edits: "2 edits", editsNote: "no card, no time limit",
      cta: "Start free", feats: ["Everything in every edit", "Learn 1 creator style", "Tweak by chat", "1080×1920 download"] },
    { id: "creator", name: "Creator", for: "For posting most days", month: 49, edits: "30 edits", editsNote: "a month, about one a day",
      cta: "Choose Creator", pop: true, feats: ["Everything in every edit", "Learn up to 10 creator styles", "Unlimited tweaks and re-cuts", "Faceless Studio included"] },
    { id: "pro", name: "Pro", for: "For several accounts or clients", month: 79, edits: "100 edits", editsNote: "a month",
      cta: "Choose Pro", feats: ["Everything in Creator", "Unlimited creator styles", "Up to 10-minute raw takes", "Priority edits"] }
  ];
  var tick = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M8 12.5l2.7 2.7L16.5 9"/></svg>';

  function card(p, period, onChoose) {
    var el = document.createElement("article");
    el.className = "plan" + (p.pop ? " pop" : "");
    // Yearly = 10 months' price ("2 months free"), shown per month.
    var price = period === "year" ? Math.round(p.month * 10 / 12) : p.month;
    var billed = p.month === 0 ? "Free forever" : period === "year" ? "$" + p.month * 10 + " billed yearly" : "Billed monthly";
    el.innerHTML =
      (p.pop ? '<span class="badge">Most popular</span>' : "") +
      "<h3>" + p.name + '</h3><p class="for">' + p.for + "</p>" +
      '<div class="price"><b class="tnum">$' + price + "</b>" +
      (period === "year" && p.month ? "<s>$" + p.month + "</s>" : "") + "<span>/ month</span></div>" +
      '<p class="billed">' + billed + "</p>" +
      '<div class="edits"><b class="tnum">' + p.edits + "</b><small>" + p.editsNote + "</small></div>" +
      '<button type="button" class="btn ' + (p.pop ? "" : "btn-ghost ") + 'btn-block" data-plan="' + p.id + '">' + p.cta + "</button>" +
      "<ul>" + p.feats.map(function (f) { return "<li>" + tick + "<span>" + f + "</span></li>"; }).join("") + "</ul>";
    el.querySelector("button").addEventListener("click", function () { onChoose(p, period); });
    return el;
  }

  function mount(root, opts) {
    var holder = root.querySelector("[data-plans]");
    var toggle = root.querySelector("[data-bill]");
    var period = "year";
    var onChoose = (opts && opts.onChoose) || function () {};
    function draw() {
      holder.innerHTML = "";
      PLANS.forEach(function (p) { holder.appendChild(card(p, period, onChoose)); });
      if (toggle) toggle.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.period === period)); });
    }
    if (toggle) toggle.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-period]");
      if (b) { period = b.dataset.period; draw(); }
    });
    draw();
  }
  window.CRPlans = { mount: mount, list: PLANS };
})();
