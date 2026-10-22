/**
 * ChannelRecipe client telemetry helpers (PostHog).
 * Loaded before app.js / landing scripts. Safe no-op if PostHog never inits.
 */
(function (global) {
  'use strict';

  var ATTR_KEYS = [
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
    'gclid', 'fbclid', 'msclkid', 'ttclid', 'ref',
  ];
  var STORAGE_KEY = 'cr_attr_v1';

  function _qs() {
    try { return new URLSearchParams(global.location.search || ''); } catch (_) { return new URLSearchParams(); }
  }

  function _referrerHost() {
    try {
      if (!document.referrer) return '';
      return new URL(document.referrer).hostname || '';
    } catch (_) { return ''; }
  }

  function _readStore() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return { first: {}, last: {} };
      var parsed = JSON.parse(raw);
      return {
        first: parsed && parsed.first && typeof parsed.first === 'object' ? parsed.first : {},
        last: parsed && parsed.last && typeof parsed.last === 'object' ? parsed.last : {},
      };
    } catch (_) {
      return { first: {}, last: {} };
    }
  }

  function _writeStore(store) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(store)); } catch (_) {}
  }

  var CLICK_KEYS = ['gclid', 'fbclid', 'msclkid', 'ttclid'];

  // A UTM or an ad click id: a deliberate, tagged visit (a bare outside referrer is weaker than this).
  function _isTagged(t) {
    if (!t || typeof t !== 'object') return false;
    return Object.keys(t).some(function (k) {
      return (k.indexOf('utm_') === 0 || CLICK_KEYS.indexOf(k) >= 0) && t[k];
    });
  }

  // A stored touch that names a source (older builds also saved bare landing pages, which name nobody).
  function _isTouch(t) {
    if (!t || typeof t !== 'object') return false;
    return Object.keys(t).some(function (k) {
      return (k.indexOf('utm_') === 0 || k === 'ref' || CLICK_KEYS.indexOf(k) >= 0) && t[k];
    });
  }

  function captureAttributionFromUrl() {
    var store = _readStore();
    try {
      var qs = _qs();
      var hit = {};
      ATTR_KEYS.forEach(function (k) {
        if (k === 'ref') return;
        var v = (qs.get(k) || '').trim();
        if (v) hit[k] = v.slice(0, 200);
      });
      var refHost = _referrerHost();
      if (refHost && !/channelrecipe\.com$/i.test(refHost) && !/localhost/i.test(refHost)) {
        hit.ref = refHost.slice(0, 200);
      }
      // A visit only counts as a touch when it carries a UTM, an ad click id or an outside referrer. A plain
      // page view (internal navigation, a bookmark) never overwrites who sent them.
      if (_isTouch(hit)) {
        try { hit.landing_path = (global.location.pathname || '/').slice(0, 200); } catch (_) {}
        hit.captured_at = Date.now();
        hit.v = 2;
        // replace, never merge: last touch is this visit only. A referrer-only visit (say, a Google search later
        // on) never replaces a tagged last touch such as a /v or /r link.
        if (_isTagged(hit) || !_isTagged(store.last)) store.last = hit;
        if (!_isTouch(store.first)) {
          store.first = Object.assign({}, hit);            // first touch is written once
        }
        store.v = 2;
        _writeStore(store);
      }
    } catch (_) {}
    return store;
  }

  /** {v:2, first, last} for the sales-attribution hooks (sign-in, checkout), plus the flat attributionProps() keys
   * so the server's existing sign-in analytics read exactly what they read before. Never throws. */
  function attributionPayload() {
    try {
      var store = getAttribution();
      return Object.assign({}, attributionProps(), { v: 2, first: store.first || {}, last: store.last || {} });
    } catch (_) {
      return null;
    }
  }

  function getAttribution() {
    captureAttributionFromUrl();
    return _readStore();
  }

  function attributionProps() {
    var store = getAttribution();
    var out = {};
    var first = store.first || {};
    var last = store.last || {};
    ATTR_KEYS.forEach(function (k) {
      if (first[k]) out['initial_' + k] = first[k];
      if (last[k]) out[k] = last[k];
    });
    if (first.landing_path) out.initial_landing_path = first.landing_path;
    if (last.landing_path) out.landing_path = last.landing_path;
    return out;
  }

  function applyPostHogAttribution(ph) {
    if (!ph) return;
    var props = attributionProps();
    try {
      if (Object.keys(props).length) ph.register(props);
    } catch (_) {}
    try {
      var once = {};
      Object.keys(props).forEach(function (k) {
        if (k.indexOf('initial_') === 0) once[k] = props[k];
      });
      if (Object.keys(once).length && ph.people && ph.people.set_once) {
        ph.people.set_once(once);
      }
    } catch (_) {}
  }

  function initPostHog(apiKey, host, extra) {
    if (!apiKey || !global.posthog || typeof global.posthog.init !== 'function') return null;
    var opts = Object.assign({
      api_host: host || 'https://us.i.posthog.com',
      capture_pageview: true,
      capture_pageleave: true,
      autocapture: true,
      persistence: 'localStorage+cookie',
      // Session replay — mask secrets; enable in project settings too.
      disable_session_recording: false,
      session_recording: {
        maskAllInputs: true,
        maskTextSelector: 'input[type="password"], #mcp-api-key, #heygen-user-key, #atlas-user-key, #key-gemini, #key-claude, #key-youtube, #key-atlascloud, #key-heygen, #key-pexels, #key-downsub',
      },
    }, extra || {});
    try {
      global.posthog.init(apiKey, opts);
      applyPostHogAttribution(global.posthog);
      return global.posthog;
    } catch (_) {
      return null;
    }
  }

  function identifyUser(user) {
    if (!user || !global.posthog || !user.id) return;
    var attrs = attributionProps();
    var props = Object.assign({
      email: user.email || '',
      plan: user.plan || 'free',
      credits: user.credits != null ? user.credits : undefined,
      trial_used: !!user.trial_used,
    }, attrs);
    try {
      global.posthog.identify(String(user.id), props);
      applyPostHogAttribution(global.posthog);
    } catch (_) {}
  }

  global.CRTelemetry = {
    ATTR_KEYS: ATTR_KEYS,
    captureAttributionFromUrl: captureAttributionFromUrl,
    getAttribution: getAttribution,
    attributionPayload: attributionPayload,
    attributionProps: attributionProps,
    applyPostHogAttribution: applyPostHogAttribution,
    initPostHog: initPostHog,
    identifyUser: identifyUser,
  };
})(typeof window !== 'undefined' ? window : this);
