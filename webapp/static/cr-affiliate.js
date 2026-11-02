/**
 * ChannelRecipe affiliate tracking (Rewardful). Does nothing until the owner connects Rewardful
 * (/api/config "affiliate"): then it loads Rewardful's tracker, which remembers the affiliate link a visitor came
 * from, and window.crReferral() gives checkout the referral id. Never throws, never blocks the page.
 *
 * The id is Rewardful's own: its tracker (once loaded on this page) or the rewardful.referral cookie it saved on the
 * visit through the link. A checkout pressed before the tracker loaded on this page (two network trips; quick on a
 * phone after signing up) went out without it (yami's test, 2026-11-02). Nothing here invents a referral: no cookie,
 * no referral.
 */
(function (global) {
  'use strict';
  var ID = /^[A-Za-z0-9-]{8,64}$/;            // the server's _REFERRAL
  function fromCookie() {
    try {
      var m = (global.document && global.document.cookie || '').match(/(?:^|;\s*)rewardful\.referral=([^;]+)/);
      var id = m && JSON.parse(decodeURIComponent(m[1])).id;
      return typeof id === 'string' && ID.test(id) ? id : '';
    } catch (_) { return ''; }
  }
  global.crReferral = function () {
    try {
      var live = global.Rewardful && global.Rewardful.referral;
      return (typeof live === 'string' && ID.test(live) && live) || fromCookie();
    } catch (_) { return fromCookie(); }
  };
  // Checkout's referral: at once when known; after arriving through an affiliate link in this tab, waits up to `ms`
  // for the tracker to save it (an organic visitor, or a browser that blocks the tracker, waits for nothing)
  global.crReferralReady = function (ms) {
    return new Promise(function (resolve) {
      var now = global.crReferral();
      var linked = false;
      try { linked = /[?&]via=/.test(global.location.search) || global.sessionStorage.getItem('cr-via') === '1'; } catch (_) {}
      if (now || !linked) { resolve(now); return; }
      var until = Date.now() + (ms || 3000);
      (function poll() {
        var id = global.crReferral();
        if (id || Date.now() >= until) { resolve(id); return; }
        setTimeout(poll, 150);
      })();
    });
  };
  try { if (/[?&]via=/.test(global.location.search)) global.sessionStorage.setItem('cr-via', '1'); } catch (_) {}
  function load(key) {
    if (!key || global._rwq) return;
    global._rwq = 'rewardful';
    global.rewardful = global.rewardful || function () { (global.rewardful.q = global.rewardful.q || []).push(arguments); };
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://r.wdfl.co/rw.js';
    s.setAttribute('data-rewardful', key);
    document.head.appendChild(s);
  }
  try {
    fetch('/api/config', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (c) { var a = c && c.affiliate; if (a && a.provider === 'rewardful') load(a.key); })
      .catch(function () {});
  } catch (_) {}
})(window);
