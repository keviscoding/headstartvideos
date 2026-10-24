/**
 * ChannelRecipe affiliate tracking (Rewardful). Does nothing until the owner connects Rewardful
 * (/api/config "affiliate"): then it loads Rewardful's tracker, which remembers the affiliate link a visitor came
 * from, and window.crReferral() gives checkout the referral id. Never throws, never blocks the page.
 */
(function (global) {
  'use strict';
  global.crReferral = function () {
    try { return (global.Rewardful && global.Rewardful.referral) || ''; } catch (_) { return ''; }
  };
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
