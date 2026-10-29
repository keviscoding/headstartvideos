/* "Where the raw take went": the real kept/cut spans of our sample edit (media/demo.json, from creators.timeline).
   The raw bar shows every cut; when it scrolls into view the kept pieces slide together into the Reel bar. */
(function () {
  function fmt(t) { t = Math.round(t); return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0"); }
  function render(el, d) {
    var raw = d.raw_seconds, kept = d.keep, gaps = d.gaps;
    var takeCut = gaps.filter(function (g) { return g[2] === "take"; }).reduce(function (a, g) { return a + g[1] - g[0]; }, 0);
    var pauseCut = gaps.filter(function (g) { return g[2] === "pause"; }).reduce(function (a, g) { return a + g[1] - g[0]; }, 0);
    var pct = function (t) { return (t / raw * 100).toFixed(3) + "%"; };
    var rawSegs = kept.map(function (k) { return '<i class="cl-k" style="left:' + pct(k[0]) + ";width:" + pct(k[1] - k[0]) + '"></i>'; }).join("") +
      gaps.map(function (g) { return '<i class="cl-' + (g[2] === "take" ? "x" : "p") + '" style="left:' + pct(g[0]) + ";width:" + pct(g[1] - g[0]) + '"></i>'; }).join("");
    var cum = 0;
    var reelSegs = kept.map(function (k, n) {
      var w = k[1] - k[0], s = '<i class="cl-k" data-from="' + pct(k[0]) + '" data-to="' + pct(cum) + '" style="left:' + pct(k[0]) + ";width:" + pct(w) + '"></i>';
      if (n) s += '<b class="cl-cut" data-to="' + pct(cum) + '" style="left:' + pct(k[0]) + '"></b>';
      cum += w; return s;
    }).join("");
    el.innerHTML =
      '<div class="cl-row"><span class="cl-l"><b>Raw take</b><span class="tnum">' + fmt(raw) + '</span></span><div class="cl-bar">' + rawSegs + "</div></div>" +
      '<div class="cl-row cl-notes" aria-hidden="true"><span></span><div class="cl-anno">' + (d.annotations || []).map(function (n) { return '<span style="left:' + pct(n.at) + '">' + n.text + "</span>"; }).join("") + "</div></div>" +
      '<div class="cl-row"><span class="cl-l"><b>Your Reel</b><span class="tnum">' + fmt(cum) + '</span></span><div class="cl-bar cl-reel">' + reelSegs + '<span class="cl-end" style="left:' + pct(cum) + '">' + d.jump_cuts + " jump cuts</span></div></div>" +
      '<div class="cl-legend"><span><i class="cl-k"></i>Kept · ' + fmt(cum) + '</span><span><i class="cl-x"></i>Retakes and extra takes · ' + fmt(takeCut) + '</span><span><i class="cl-p"></i>Dead air · ' + pauseCut.toFixed(1) + " s</span></div>";
    function pack() { el.querySelectorAll("[data-to]").forEach(function (s) { s.style.left = s.dataset.to; }); el.classList.add("packed"); }
    if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      var io = new IntersectionObserver(function (en) { if (en[0].isIntersecting) { setTimeout(pack, 350); io.disconnect(); } }, { threshold: 0.6 });
      io.observe(el);
    } else pack();
  }
  window.CRCutline = {
    mount: function (el) { return fetch("media/demo.json").then(function (r) { return r.json(); }).then(function (d) { render(el, d); return d; }); },
    render: render
  };
})();
