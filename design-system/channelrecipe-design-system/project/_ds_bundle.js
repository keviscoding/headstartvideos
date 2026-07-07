/* @ds-bundle: {"format":4,"namespace":"ChannelRecipeDesignSystem_51526c","components":[{"name":"ProgressStages","sourcePath":"components/app/ProgressStages.jsx"},{"name":"Stepper","sourcePath":"components/app/Stepper.jsx"},{"name":"VoiceOption","sourcePath":"components/app/VoiceOption.jsx"},{"name":"Logo","sourcePath":"components/brand/Logo.jsx"},{"name":"BrowserFrame","sourcePath":"components/display/BrowserFrame.jsx"},{"name":"CreditsChip","sourcePath":"components/display/CreditsChip.jsx"},{"name":"PillBadge","sourcePath":"components/display/PillBadge.jsx"},{"name":"StatusChip","sourcePath":"components/display/StatusChip.jsx"},{"name":"Button","sourcePath":"components/forms/Button.jsx"},{"name":"Input","sourcePath":"components/forms/Input.jsx"},{"name":"RecipeCard","sourcePath":"components/recipe/RecipeCard.jsx"}],"sourceHashes":{"components/app/ProgressStages.jsx":"2069c51ac745","components/app/Stepper.jsx":"78d1d67a0d66","components/app/VoiceOption.jsx":"12381a084f82","components/brand/Logo.jsx":"ff441170a6b2","components/display/BrowserFrame.jsx":"43e48f28e543","components/display/CreditsChip.jsx":"c0244936a9e4","components/display/PillBadge.jsx":"d10a00dda7c3","components/display/StatusChip.jsx":"6b486fdcd672","components/forms/Button.jsx":"ecaadef60ea2","components/forms/Input.jsx":"fb7beb709a1f","components/recipe/RecipeCard.jsx":"754778ae5718","ui_kits/app/AppShell.jsx":"ad0e7d992678","ui_kits/app/AppSteps.jsx":"78317c76bce1","ui_kits/common.jsx":"a9a761e84e8a","ui_kits/marketing/Demo.jsx":"afdc9eeaec8b","ui_kits/marketing/MarketingPage.jsx":"5d1595caa901"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.ChannelRecipeDesignSystem_51526c = window.ChannelRecipeDesignSystem_51526c || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/app/ProgressStages.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * ProgressStages — honest, narrated cooking progress.
 * Shows a real progress bar + named stages. Occupied time feels half as long.
 * stages: [{ label, state }] where state = "done" | "active" | "todo".
 */
function ProgressStages({
  stages = [{
    label: "Writing script",
    state: "done"
  }, {
    label: "Generating voiceover",
    state: "active"
  }, {
    label: "Assembling b-roll",
    state: "todo"
  }, {
    label: "Rendering",
    state: "todo"
  }],
  percent = 42,
  eta = "about 3 minutes",
  theme = "dark",
  style,
  ...rest
}) {
  const dark = theme !== "light";
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      height: "8px",
      borderRadius: "var(--radius-pill)",
      background: dark ? "var(--app-surface-2)" : "var(--canvas-2)",
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: `${percent}%`,
      height: "100%",
      borderRadius: "var(--radius-pill)",
      background: "var(--accent)",
      transition: "width var(--dur-slow) var(--ease-standard)"
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      marginTop: "8px",
      fontFamily: "var(--font-mono)",
      fontSize: "var(--fs-mono-s)",
      letterSpacing: "var(--tracking-mono)",
      color: dark ? "var(--app-text-muted)" : "var(--text-muted)"
    }
  }, /*#__PURE__*/React.createElement("span", null, percent, "%"), /*#__PURE__*/React.createElement("span", null, eta))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "10px"
    }
  }, stages.map(s => {
    const done = s.state === "done";
    const active = s.state === "active";
    return /*#__PURE__*/React.createElement("div", {
      key: s.label,
      style: {
        display: "flex",
        alignItems: "center",
        gap: "11px"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: "20px",
        height: "20px",
        flex: "none",
        borderRadius: "50%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: done ? "var(--accent)" : active ? "transparent" : "transparent",
        border: done ? "none" : `1.5px solid ${active ? "var(--accent)" : dark ? "var(--app-border)" : "var(--hairline)"}`,
        color: "var(--accent-ink)"
      }
    }, done ? /*#__PURE__*/React.createElement("svg", {
      width: "11",
      height: "11",
      viewBox: "0 0 14 14",
      "aria-hidden": "true"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M2.5 7.5 L6 11 L11.5 3.5",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "2",
      strokeLinecap: "round",
      strokeLinejoin: "round"
    })) : active ? /*#__PURE__*/React.createElement(Spinner, null) : null), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: "var(--fs-body)",
        fontWeight: active ? "var(--fw-semibold)" : "var(--fw-regular)",
        color: active ? dark ? "var(--app-text-strong)" : "var(--ink)" : done ? dark ? "var(--app-text-body)" : "var(--text-body)" : dark ? "var(--app-text-muted)" : "var(--text-muted)"
      }
    }, s.label, active && "…"));
  })));
}
function Spinner() {
  return /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 16 16",
    "aria-hidden": "true",
    style: {
      animation: "cr-spin 0.8s linear infinite"
    }
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "8",
    cy: "8",
    r: "6",
    fill: "none",
    stroke: "var(--accent)",
    strokeWidth: "2",
    strokeOpacity: "0.25"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M8 2 a6 6 0 0 1 6 6",
    fill: "none",
    stroke: "var(--accent)",
    strokeWidth: "2",
    strokeLinecap: "round"
  }), /*#__PURE__*/React.createElement("style", null, `@keyframes cr-spin { to { transform: rotate(360deg); } }`));
}
Object.assign(__ds_scope, { ProgressStages });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/app/ProgressStages.jsx", error: String((e && e.message) || e) }); }

// components/app/Stepper.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Stepper — the app's linear 6-step pipeline indicator.
 * Recipe -> Title -> Script -> Voice -> Thumbnail -> Cook.
 * Endowed progress: step 1 completes the moment a recipe is picked.
 */
function Stepper({
  steps = ["Recipe", "Title", "Script", "Voice", "Thumbnail", "Cook"],
  current = 0,
  theme = "dark",
  style,
  ...rest
}) {
  const dark = theme !== "light";
  const line = dark ? "var(--app-border)" : "var(--hairline)";
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      alignItems: "center",
      gap: "0",
      width: "100%",
      ...style
    }
  }, rest), steps.map((label, i) => {
    const done = i < current;
    const active = i === current;
    const dotBg = done ? "var(--accent)" : active ? "var(--accent)" : dark ? "var(--app-surface-2)" : "var(--white)";
    const dotBorder = done || active ? "var(--accent)" : line;
    const dotFg = done || active ? "var(--accent-ink)" : dark ? "var(--app-text-muted)" : "var(--text-muted)";
    return /*#__PURE__*/React.createElement(React.Fragment, {
      key: label
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "7px",
        flex: "none"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: "30px",
        height: "30px",
        borderRadius: "50%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: dotBg,
        border: `1.5px solid ${dotBorder}`,
        color: dotFg,
        fontFamily: "var(--font-mono)",
        fontSize: "var(--fs-mono-s)",
        fontWeight: "var(--fw-medium)",
        boxShadow: active ? "0 0 0 4px color-mix(in srgb, var(--accent) 22%, transparent)" : "none",
        transition: "all var(--dur-base) var(--ease-standard)"
      }
    }, done ? /*#__PURE__*/React.createElement("svg", {
      width: "13",
      height: "13",
      viewBox: "0 0 14 14",
      "aria-hidden": "true"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M2.5 7.5 L6 11 L11.5 3.5",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "2",
      strokeLinecap: "round",
      strokeLinejoin: "round"
    })) : i + 1), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: "var(--fs-caption)",
        fontWeight: active ? "var(--fw-semibold)" : "var(--fw-regular)",
        color: active ? dark ? "var(--app-text-strong)" : "var(--ink)" : done ? "var(--accent)" : dark ? "var(--app-text-muted)" : "var(--text-muted)",
        whiteSpace: "nowrap"
      }
    }, label)), i < steps.length - 1 && /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        height: "2px",
        margin: "0 6px",
        marginBottom: "22px",
        background: i < current ? "var(--accent)" : line,
        borderRadius: "2px",
        transition: "background var(--dur-base)"
      }
    }));
  }));
}
Object.assign(__ds_scope, { Stepper });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/app/Stepper.jsx", error: String((e && e.message) || e) }); }

// components/app/VoiceOption.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * VoiceOption — selectable voice row with a play button.
 * Six curated voices; best pre-selected. Never more choices than necessary.
 */
function VoiceOption({
  name = "Ava",
  descriptor = "Warm US female",
  selected = false,
  playing = false,
  recommended = false,
  onSelect,
  onPlay,
  theme = "dark",
  style,
  ...rest
}) {
  const dark = theme !== "light";
  return /*#__PURE__*/React.createElement("div", _extends({
    onClick: onSelect,
    role: "radio",
    "aria-checked": selected,
    tabIndex: 0,
    style: {
      display: "flex",
      alignItems: "center",
      gap: "12px",
      padding: "12px 14px",
      borderRadius: "var(--radius-md)",
      background: selected ? dark ? "var(--accent-soft-dark)" : "var(--accent-soft)" : dark ? "var(--app-surface-2)" : "var(--white)",
      border: `1.5px solid ${selected ? "var(--accent)" : dark ? "var(--app-border)" : "var(--hairline)"}`,
      cursor: "pointer",
      transition: "border-color var(--dur-fast), background var(--dur-fast)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("button", {
    type: "button",
    "aria-label": playing ? "Pause preview" : "Play preview",
    onClick: e => {
      e.stopPropagation();
      onPlay && onPlay();
    },
    style: {
      width: "38px",
      height: "38px",
      flex: "none",
      borderRadius: "50%",
      border: "none",
      cursor: "pointer",
      background: "var(--accent)",
      color: "var(--accent-ink)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center"
    }
  }, playing ? /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 14 14",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "3",
    y: "2.5",
    width: "3",
    height: "9",
    rx: "1",
    fill: "currentColor"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "8",
    y: "2.5",
    width: "3",
    height: "9",
    rx: "1",
    fill: "currentColor"
  })) : /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 14 14",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M4 2.5 L4 11.5 L11 7 Z",
    fill: "currentColor"
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "8px",
      fontFamily: "var(--font-body)",
      fontWeight: "var(--fw-semibold)",
      fontSize: "var(--fs-body)",
      color: dark ? "var(--app-text-strong)" : "var(--ink)"
    }
  }, name, recommended && /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: "10px",
      letterSpacing: "var(--tracking-eyebrow)",
      textTransform: "uppercase",
      color: "var(--accent)",
      background: dark ? "var(--accent-soft-dark)" : "var(--accent-soft)",
      borderRadius: "var(--radius-pill)",
      padding: "2px 7px"
    }
  }, "Best pick")), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-body)",
      fontSize: "var(--fs-caption)",
      color: dark ? "var(--app-text-muted)" : "var(--text-muted)"
    }
  }, descriptor)), /*#__PURE__*/React.createElement("span", {
    style: {
      width: "20px",
      height: "20px",
      flex: "none",
      borderRadius: "50%",
      border: `2px solid ${selected ? "var(--accent)" : dark ? "var(--app-border)" : "var(--hairline)"}`,
      display: "flex",
      alignItems: "center",
      justifyContent: "center"
    }
  }, selected && /*#__PURE__*/React.createElement("span", {
    style: {
      width: "10px",
      height: "10px",
      borderRadius: "50%",
      background: "var(--accent)"
    }
  })));
}
Object.assign(__ds_scope, { VoiceOption });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/app/VoiceOption.jsx", error: String((e && e.message) || e) }); }

// components/brand/Logo.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Logo — glyph (recipe card + folded-corner play triangle) with optional wordmark.
 * "Recipe" is set in violet, "Channel" in ink (camel-case lockup).
 */
function Logo({
  variant = "horizontal",
  // "horizontal" | "glyph" | "wordmark"
  theme = "light",
  // "light" | "dark"
  size = 28,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const ink = dark ? "var(--app-text-strong)" : "var(--ink)";
  const glyph = /*#__PURE__*/React.createElement("svg", {
    width: size,
    height: size,
    viewBox: "0 0 100 100",
    fill: "none",
    "aria-hidden": variant !== "glyph"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M18 6 H62 L82 26 V82 A12 12 0 0 1 70 94 H30 A12 12 0 0 1 18 82 V18 A12 12 0 0 1 30 6 Z",
    fill: ink
  }), /*#__PURE__*/React.createElement("path", {
    d: "M62 6 L82 26 H66 A4 4 0 0 1 62 22 Z",
    fill: dark ? "var(--app-bg)" : "var(--canvas)"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M42 40 L42 68 L66 54 Z",
    fill: "var(--accent)"
  }));
  const wordmark = /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-display)",
      fontWeight: "var(--fw-heavy)",
      fontSize: size * 0.72,
      letterSpacing: "var(--tracking-tight)",
      lineHeight: 1,
      color: ink
    }
  }, "Channel", /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--accent)"
    }
  }, "Recipe"));
  if (variant === "glyph") {
    return /*#__PURE__*/React.createElement("span", _extends({
      role: "img",
      "aria-label": "ChannelRecipe",
      style: {
        display: "inline-flex",
        ...style
      }
    }, rest), glyph);
  }
  if (variant === "wordmark") {
    return /*#__PURE__*/React.createElement("span", _extends({
      role: "img",
      "aria-label": "ChannelRecipe",
      style: {
        display: "inline-flex",
        ...style
      }
    }, rest), wordmark);
  }
  return /*#__PURE__*/React.createElement("span", _extends({
    role: "img",
    "aria-label": "ChannelRecipe",
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: size * 0.32,
      ...style
    }
  }, rest), glyph, wordmark);
}
Object.assign(__ds_scope, { Logo });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/Logo.jsx", error: String((e && e.message) || e) }); }

// components/display/BrowserFrame.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * BrowserFrame — browser-chrome mockup. The dark product framed on the light
 * marketing page IS the visual identity. Holds screenshots or the demo loop.
 */
function BrowserFrame({
  url = "app.channelrecipe.com",
  theme = "dark",
  // frame chrome tone
  children,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const chrome = dark ? "var(--app-surface)" : "var(--white)";
  const chromeBorder = dark ? "var(--app-border)" : "var(--hairline)";
  const barText = dark ? "var(--app-text-muted)" : "var(--text-muted)";
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      borderRadius: "var(--radius-lg)",
      overflow: "hidden",
      border: `1px solid ${chromeBorder}`,
      boxShadow: "var(--shadow-lg)",
      background: chrome,
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "12px",
      padding: "11px 14px",
      background: chrome,
      borderBottom: `1px solid ${chromeBorder}`
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: "7px"
    }
  }, ["#E5484D", "#E8A13C", "#14B87A"].map(c => /*#__PURE__*/React.createElement("span", {
    key: c,
    style: {
      width: "11px",
      height: "11px",
      borderRadius: "50%",
      background: c,
      opacity: 0.85
    }
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      display: "flex",
      alignItems: "center",
      gap: "7px",
      justifyContent: "center",
      maxWidth: "60%",
      margin: "0 auto",
      background: dark ? "var(--app-bg)" : "var(--canvas)",
      borderRadius: "var(--radius-pill)",
      padding: "5px 14px",
      fontFamily: "var(--font-mono)",
      fontSize: "var(--fs-mono-s)",
      color: barText
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "10",
    height: "10",
    viewBox: "0 0 12 12",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M3.5 5.5V4a2.5 2.5 0 015 0v1.5",
    fill: "none",
    stroke: barText,
    strokeWidth: "1.2"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "2.5",
    y: "5.5",
    width: "7",
    height: "5",
    rx: "1.2",
    fill: barText,
    opacity: "0.85"
  })), url)), /*#__PURE__*/React.createElement("div", {
    style: {
      background: dark ? "var(--app-bg)" : "var(--canvas)"
    }
  }, children));
}
Object.assign(__ds_scope, { BrowserFrame });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/BrowserFrame.jsx", error: String((e && e.message) || e) }); }

// components/display/CreditsChip.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * CreditsChip — always-visible mono credit counter for the app top corner.
 * e.g. "11 credits · resets Jul 14". Compact per-cook variant too.
 */
function CreditsChip({
  credits = 11,
  resets = "Jul 14",
  compact = false,
  theme = "dark",
  style,
  ...rest
}) {
  const dark = theme === "dark";
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: "8px",
      fontFamily: "var(--font-mono)",
      fontSize: "var(--fs-mono-s)",
      letterSpacing: "var(--tracking-mono)",
      color: dark ? "var(--app-text-body)" : "var(--text-body)",
      background: dark ? "var(--app-surface-2)" : "var(--white)",
      border: `1px solid ${dark ? "var(--app-border)" : "var(--hairline)"}`,
      borderRadius: "var(--radius-pill)",
      padding: "6px 12px",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    style: {
      width: "7px",
      height: "7px",
      borderRadius: "50%",
      background: "var(--accent)",
      flex: "none"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      color: dark ? "var(--app-text-strong)" : "var(--ink)",
      fontWeight: "var(--fw-medium)"
    }
  }, credits, " credits"), !compact && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
    style: {
      opacity: 0.45
    }
  }, "\xB7"), /*#__PURE__*/React.createElement("span", null, "resets ", resets)));
}
Object.assign(__ds_scope, { CreditsChip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/CreditsChip.jsx", error: String((e && e.message) || e) }); }

// components/display/PillBadge.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * PillBadge — the rounded eyebrow pill above headlines.
 * Claim-free announcements: "New: this week's recipe just dropped".
 * A small violet dot signals the weekly drop.
 */
function PillBadge({
  children,
  theme = "light",
  dot = true,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: "8px",
      background: dark ? "var(--app-surface-2)" : "var(--white)",
      border: `1px solid ${dark ? "var(--app-border)" : "var(--hairline)"}`,
      borderRadius: "var(--radius-pill)",
      padding: "7px 14px 7px 12px",
      fontFamily: "var(--font-body)",
      fontSize: "var(--fs-small)",
      fontWeight: "var(--fw-medium)",
      color: dark ? "var(--app-text-body)" : "var(--text-body)",
      boxShadow: dark ? "none" : "var(--shadow-xs)",
      ...style
    }
  }, rest), dot && /*#__PURE__*/React.createElement("span", {
    style: {
      width: "8px",
      height: "8px",
      borderRadius: "50%",
      background: "var(--accent)",
      boxShadow: "0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent)",
      flex: "none"
    }
  }), children);
}
Object.assign(__ds_scope, { PillBadge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/PillBadge.jsx", error: String((e && e.message) || e) }); }

// components/display/StatusChip.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * StatusChip — small status semantic chip.
 * kind: "new" (violet, weekly drop) | "proven" (green + spark) |
 *       "monetized" (green) | "warn" | "error" | "neutral".
 * Mono label, uppercase, receipt texture.
 */
function StatusChip({
  kind = "new",
  children,
  theme = "light",
  style,
  ...rest
}) {
  const map = {
    new: {
      fg: "var(--accent-ink)",
      bg: "var(--accent)",
      bd: "transparent",
      spark: false
    },
    proven: {
      fg: "var(--success)",
      bg: "var(--success-soft)",
      bd: "color-mix(in srgb, var(--success) 32%, transparent)",
      spark: true
    },
    monetized: {
      fg: "var(--success)",
      bg: "var(--success-soft)",
      bd: "color-mix(in srgb, var(--success) 32%, transparent)",
      spark: false
    },
    warn: {
      fg: "#8a5a12",
      bg: "var(--warn-soft)",
      bd: "color-mix(in srgb, var(--warn) 40%, transparent)",
      spark: false
    },
    error: {
      fg: "var(--error)",
      bg: "var(--error-soft)",
      bd: "color-mix(in srgb, var(--error) 34%, transparent)",
      spark: false
    },
    neutral: {
      fg: "var(--text-muted)",
      bg: "var(--canvas-2)",
      bd: "var(--hairline)",
      spark: false
    }
  }[kind];
  const label = children || kind;
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: "5px",
      fontFamily: "var(--font-mono)",
      fontSize: "var(--fs-mono-s)",
      letterSpacing: "var(--tracking-eyebrow)",
      textTransform: "uppercase",
      color: map.fg,
      background: map.bg,
      border: `1px solid ${map.bd}`,
      borderRadius: "var(--radius-pill)",
      padding: "3px 10px",
      ...style
    }
  }, rest), map.spark && /*#__PURE__*/React.createElement("svg", {
    width: "11",
    height: "11",
    viewBox: "0 0 12 12",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M1 9 L4 5 L6.5 7 L11 1",
    fill: "none",
    stroke: "var(--success)",
    strokeWidth: "1.6",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  })), label);
}
Object.assign(__ds_scope, { StatusChip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/display/StatusChip.jsx", error: String((e && e.message) || e) }); }

// components/forms/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Button — rounded CTA. Buttons say exactly what they do ("Cook video").
 * variants: primary (violet), secondary (ink outline), ghost, subtle.
 */
function Button({
  variant = "primary",
  size = "md",
  // "sm" | "md" | "lg"
  theme = "light",
  full = false,
  disabled = false,
  leftIcon,
  rightIcon,
  children,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const pads = {
    sm: {
      padding: "8px 14px",
      fontSize: "var(--fs-small)",
      radius: "var(--radius-sm)"
    },
    md: {
      padding: "12px 20px",
      fontSize: "var(--fs-body)",
      radius: "var(--radius-md)"
    },
    lg: {
      padding: "16px 28px",
      fontSize: "var(--fs-lead)",
      radius: "var(--radius-md)"
    }
  }[size];
  const base = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "9px",
    width: full ? "100%" : "auto",
    fontFamily: "var(--font-body)",
    fontWeight: "var(--fw-semibold)",
    fontSize: pads.fontSize,
    lineHeight: 1.1,
    letterSpacing: "var(--tracking-tight)",
    padding: pads.padding,
    borderRadius: pads.radius,
    border: "1px solid transparent",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: "background var(--dur-fast), border-color var(--dur-fast), transform var(--dur-fast), box-shadow var(--dur-base)",
    whiteSpace: "nowrap"
  };
  const looks = {
    primary: {
      background: "var(--accent)",
      color: "var(--accent-ink)",
      boxShadow: "var(--shadow-accent)"
    },
    secondary: {
      background: "transparent",
      color: dark ? "var(--app-text-strong)" : "var(--ink)",
      borderColor: dark ? "var(--app-border)" : "var(--ink)"
    },
    ghost: {
      background: "transparent",
      color: dark ? "var(--app-text-body)" : "var(--ink-2)"
    },
    subtle: {
      background: dark ? "var(--app-surface-2)" : "var(--accent-soft)",
      color: dark ? "var(--app-text-strong)" : "var(--accent)"
    }
  }[variant];
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    disabled: disabled,
    style: {
      ...base,
      ...looks,
      ...style
    },
    onMouseEnter: e => {
      if (disabled) return;
      if (variant === "primary") e.currentTarget.style.background = "var(--accent-hover)";else if (variant === "secondary") e.currentTarget.style.background = dark ? "var(--app-surface-2)" : "var(--canvas-2)";else if (variant === "ghost") e.currentTarget.style.background = dark ? "var(--app-surface-2)" : "var(--canvas-2)";else {
        e.currentTarget.style.background = dark ? "var(--accent-soft-dark)" : "var(--accent)";
        if (!dark) e.currentTarget.style.color = "var(--accent-ink)";
      }
    },
    onMouseLeave: e => {
      if (disabled) return;
      e.currentTarget.style.background = looks.background;
      e.currentTarget.style.color = looks.color;
    }
  }, rest), leftIcon, children, rightIcon);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Button.jsx", error: String((e && e.message) || e) }); }

// components/forms/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** Input — text/email field. Calm, generous, cream or dark surface. */
function Input({
  label,
  hint,
  error,
  theme = "light",
  leftIcon,
  id,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const inputId = id || (label ? `in-${label.replace(/\s+/g, "-").toLowerCase()}` : undefined);
  return /*#__PURE__*/React.createElement("label", {
    htmlFor: inputId,
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "6px",
      ...style
    }
  }, label && /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-body)",
      fontSize: "var(--fs-small)",
      fontWeight: "var(--fw-medium)",
      color: dark ? "var(--app-text-body)" : "var(--text-body)"
    }
  }, label), /*#__PURE__*/React.createElement("span", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "10px",
      background: dark ? "var(--app-surface-2)" : "var(--white)",
      border: `1px solid ${error ? "var(--error)" : dark ? "var(--app-border)" : "var(--hairline)"}`,
      borderRadius: "var(--radius-md)",
      padding: "12px 14px"
    }
  }, leftIcon, /*#__PURE__*/React.createElement("input", _extends({
    id: inputId,
    style: {
      flex: 1,
      border: "none",
      outline: "none",
      background: "transparent",
      fontFamily: "var(--font-body)",
      fontSize: "var(--fs-body)",
      color: dark ? "var(--app-text-strong)" : "var(--ink)",
      minWidth: 0
    }
  }, rest))), (hint || error) && /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-body)",
      fontSize: "var(--fs-caption)",
      color: error ? "var(--error)" : dark ? "var(--app-text-muted)" : "var(--text-muted)"
    }
  }, error || hint));
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Input.jsx", error: String((e && e.message) || e) }); }

// components/recipe/RecipeCard.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * RecipeCard — the atomic brand object.
 * A rounded card with a folded top-right corner that reads as a play triangle
 * (recipe + video in one shape). Anatomy: niche name -> one-line promise ->
 * mono meta row (cook time / credit / RPM band) -> status chip.
 */
function RecipeCard({
  name = "Recipe name",
  promise = "One-line promise about the niche.",
  cookTime = "~15 min",
  credits = "1 credit",
  rpm = "RPM $4–8",
  status = "none",
  // "new" | "proven" | "none"
  theme = "light",
  // "light" | "dark"
  selected = false,
  onClick,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const surface = dark ? "var(--app-surface-card)" : "var(--surface-card)";
  const border = selected ? "var(--accent)" : dark ? "var(--app-border-card)" : "var(--border-card)";
  const strong = dark ? "var(--app-text-strong)" : "var(--text-strong)";
  const body = dark ? "var(--app-text-body)" : "var(--text-body)";
  const muted = dark ? "var(--app-text-muted)" : "var(--text-muted)";
  const clickable = typeof onClick === "function";
  return /*#__PURE__*/React.createElement("div", _extends({
    onClick: onClick,
    role: clickable ? "button" : undefined,
    tabIndex: clickable ? 0 : undefined,
    style: {
      position: "relative",
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-3)",
      background: surface,
      border: `${selected ? "2px" : "1px"} solid ${border}`,
      borderRadius: "var(--radius-card)",
      padding: "var(--space-5)",
      paddingRight: "var(--space-6)",
      boxShadow: dark ? "var(--shadow-dark)" : "var(--shadow-card)",
      cursor: clickable ? "pointer" : "default",
      transition: "transform var(--dur-base) var(--ease-standard), border-color var(--dur-base), box-shadow var(--dur-base)",
      overflow: "hidden",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement(FoldPlay, null), status !== "none" && /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: "var(--space-1)"
    }
  }, /*#__PURE__*/React.createElement(StatusPill, {
    status: status
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-display)",
      fontWeight: "var(--fw-heavy)",
      fontSize: "var(--fs-title)",
      lineHeight: "var(--lh-title)",
      letterSpacing: "var(--tracking-tight)",
      color: strong,
      maxWidth: "88%"
    }
  }, name), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-body)",
      fontSize: "var(--fs-small)",
      lineHeight: "var(--lh-small)",
      color: body
    }
  }, promise), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexWrap: "wrap",
      gap: "6px 10px",
      alignItems: "center",
      marginTop: "auto",
      paddingTop: "var(--space-2)",
      fontFamily: "var(--font-mono)",
      fontSize: "var(--fs-mono-s)",
      letterSpacing: "var(--tracking-mono)",
      color: muted
    }
  }, /*#__PURE__*/React.createElement("span", null, cookTime), /*#__PURE__*/React.createElement(Dot, null), /*#__PURE__*/React.createElement("span", null, credits), /*#__PURE__*/React.createElement(Dot, null), /*#__PURE__*/React.createElement("span", null, rpm)));
}
function Dot() {
  return /*#__PURE__*/React.createElement("span", {
    style: {
      opacity: 0.5
    }
  }, "\xB7");
}
function FoldPlay() {
  return /*#__PURE__*/React.createElement("svg", {
    width: "40",
    height: "40",
    viewBox: "0 0 40 40",
    "aria-hidden": "true",
    style: {
      position: "absolute",
      top: 0,
      right: 0
    }
  }, /*#__PURE__*/React.createElement("path", {
    d: "M40 0 V22 L18 0 Z",
    fill: "var(--accent)",
    opacity: "0.16"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M15 11 L15 27 L29 19 Z",
    fill: "var(--accent)"
  }));
}
function StatusPill({
  status
}) {
  if (status === "proven") {
    return /*#__PURE__*/React.createElement("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        gap: "5px",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--fs-mono-s)",
        letterSpacing: "var(--tracking-eyebrow)",
        textTransform: "uppercase",
        color: "var(--success)",
        background: "var(--success-soft)",
        border: "1px solid color-mix(in srgb, var(--success) 30%, transparent)",
        borderRadius: "var(--radius-pill)",
        padding: "3px 9px"
      }
    }, /*#__PURE__*/React.createElement(Spark, null), "Proven");
  }
  return /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      fontFamily: "var(--font-mono)",
      fontSize: "var(--fs-mono-s)",
      letterSpacing: "var(--tracking-eyebrow)",
      textTransform: "uppercase",
      color: "var(--accent-ink)",
      background: "var(--accent)",
      borderRadius: "var(--radius-pill)",
      padding: "3px 10px"
    }
  }, "New");
}
function Spark() {
  return /*#__PURE__*/React.createElement("svg", {
    width: "11",
    height: "11",
    viewBox: "0 0 12 12",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M1 9 L4 5 L6.5 7 L11 1",
    fill: "none",
    stroke: "var(--success)",
    strokeWidth: "1.6",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }));
}
Object.assign(__ds_scope, { RecipeCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/recipe/RecipeCard.jsx", error: String((e && e.message) || e) }); }

// ui_kits/app/AppShell.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* ChannelRecipe app — dark product. Interactive pipeline shell:
   signup -> recipe wall -> 6-step pipeline -> cooking -> Upload Kit -> account.
   Composes DS primitives. Global (no import/export). */
(function () {
  const {
    useState,
    useEffect,
    useRef
  } = React;
  const CR = window.ChannelRecipeDesignSystem_51526c;
  const {
    Logo,
    Button,
    RecipeCard,
    Stepper,
    ProgressStages,
    CreditsChip,
    Input,
    StatusChip
  } = CR;
  const {
    Icon,
    RECIPES
  } = window;
  const {
    TitleStep,
    ScriptStep,
    VoiceStep,
    ThumbStep,
    CookStep
  } = window.AppSteps;
  const STEPS = ["Recipe", "Title", "Script", "Voice", "Thumbnail", "Cook"];

  /* ---------------- Top bar ---------------- */
  function TopBar({
    credits,
    onNav,
    view
  }) {
    const tab = (id, txt) => /*#__PURE__*/React.createElement("button", {
      type: "button",
      onClick: () => onNav(id),
      style: {
        background: "none",
        border: "none",
        cursor: "pointer",
        fontFamily: "var(--font-body)",
        fontSize: 14,
        fontWeight: 500,
        color: view === id ? "var(--app-text-strong)" : "var(--app-text-muted)",
        padding: 0
      }
    }, txt);
    return /*#__PURE__*/React.createElement("header", {
      style: {
        position: "sticky",
        top: 0,
        zIndex: 20,
        background: "color-mix(in srgb, var(--app-bg) 88%, transparent)",
        backdropFilter: "blur(10px)",
        borderBottom: "1px solid var(--app-border)"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: 1080,
        margin: "0 auto",
        padding: "13px 24px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 26
      }
    }, /*#__PURE__*/React.createElement("button", {
      type: "button",
      onClick: () => onNav("wall"),
      style: {
        background: "none",
        border: "none",
        cursor: "pointer",
        padding: 0
      }
    }, /*#__PURE__*/React.createElement(Logo, {
      variant: "horizontal",
      theme: "dark",
      size: 26
    })), /*#__PURE__*/React.createElement("nav", {
      style: {
        display: "flex",
        gap: 20
      }
    }, tab("wall", "New video"), tab("account", "Account"))), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 14
      }
    }, /*#__PURE__*/React.createElement(CreditsChip, {
      credits: credits,
      resets: "Jul 14"
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        width: 32,
        height: 32,
        borderRadius: "50%",
        background: "var(--accent)",
        color: "#fff",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 14
      }
    }, "A"))));
  }
  const page = {
    maxWidth: 1080,
    margin: "0 auto",
    padding: "40px 24px 80px"
  };
  const panel = {
    background: "var(--app-surface)",
    border: "1px solid var(--app-border)",
    borderRadius: "var(--radius-card)",
    padding: 26
  };

  /* ---------------- Signup ---------------- */
  function Signup({
    onDone
  }) {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        width: "100%",
        maxWidth: 400
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 18,
        marginBottom: 26
      }
    }, /*#__PURE__*/React.createElement(Logo, {
      variant: "glyph",
      theme: "dark",
      size: 44
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        textAlign: "center"
      }
    }, /*#__PURE__*/React.createElement("h1", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 30,
        letterSpacing: "-.02em",
        color: "var(--app-text-strong)",
        margin: 0
      }
    }, "Start free"), /*#__PURE__*/React.createElement("p", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 15,
        color: "var(--app-text-body)",
        margin: "8px 0 0"
      }
    }, "Pick a recipe. We handle the cooking."))), /*#__PURE__*/React.createElement("div", {
      style: {
        ...panel,
        display: "flex",
        flexDirection: "column",
        gap: 14
      }
    }, /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      theme: "dark",
      full: true,
      leftIcon: /*#__PURE__*/React.createElement("span", {
        style: {
          width: 18,
          height: 18,
          borderRadius: "50%",
          background: "#fff",
          color: "#16161A",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "var(--font-display)",
          fontWeight: 800,
          fontSize: 12
        }
      }, "G")
    }, "Continue with Google"), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 12,
        color: "var(--app-text-muted)"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        height: 1,
        background: "var(--app-border)"
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        letterSpacing: ".06em"
      }
    }, "OR"), /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        height: 1,
        background: "var(--app-border)"
      }
    })), /*#__PURE__*/React.createElement(Input, {
      theme: "dark",
      label: "Email",
      type: "email",
      placeholder: "you@email.com",
      leftIcon: /*#__PURE__*/React.createElement("span", {
        style: {
          color: "var(--app-text-muted)",
          display: "inline-flex"
        }
      }, /*#__PURE__*/React.createElement(Icon, {
        name: "mail",
        size: 16
      }))
    }), /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      theme: "dark",
      full: true,
      onClick: onDone
    }, "Make your first video free"), /*#__PURE__*/React.createElement("p", {
      style: {
        textAlign: "center",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--app-text-muted)",
        margin: 0
      }
    }, "3 free videos \xB7 no card \xB7 2-minute setup"))));
  }

  /* ---------------- Recipe wall ---------------- */
  function Wall({
    onPick,
    credits
  }) {
    return /*#__PURE__*/React.createElement("div", {
      style: page
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexWrap: "wrap",
        alignItems: "flex-end",
        justifyContent: "space-between",
        gap: 12,
        marginBottom: 8
      }
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h1", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 32,
        letterSpacing: "-.02em",
        color: "var(--app-text-strong)",
        margin: 0
      }
    }, "Pick your first recipe"), /*#__PURE__*/React.createElement("p", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 16,
        color: "var(--app-text-body)",
        margin: "8px 0 0"
      }
    }, "Each one is proven on a real channel. A new recipe drops every week.")), /*#__PURE__*/React.createElement("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        fontFamily: "var(--font-body)",
        fontSize: 14,
        color: "var(--app-text-body)"
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "calendar-check",
      size: 16,
      style: {
        color: "var(--accent)"
      }
    }), " This week's drop is live")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
        gap: 18,
        marginTop: 28,
        alignItems: "stretch"
      }
    }, RECIPES.map(r => /*#__PURE__*/React.createElement(RecipeCard, _extends({
      key: r.name,
      theme: "dark"
    }, r, {
      onClick: () => onPick(r.name)
    })))));
  }

  /* ---------------- Pipeline ---------------- */
  function Pipeline({
    step,
    setStep,
    recipe,
    onCook,
    onExit
  }) {
    const bodies = {
      1: /*#__PURE__*/React.createElement(TitleStep, {
        recipe: recipe
      }),
      2: /*#__PURE__*/React.createElement(ScriptStep, null),
      3: /*#__PURE__*/React.createElement(VoiceStep, null),
      4: /*#__PURE__*/React.createElement(ThumbStep, null),
      5: /*#__PURE__*/React.createElement(CookStep, {
        recipe: recipe
      })
    };
    const back = () => step <= 1 ? onExit() : setStep(step - 1);
    const next = () => step >= 5 ? onCook() : setStep(step + 1);
    return /*#__PURE__*/React.createElement("div", {
      style: page
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        ...panel,
        marginBottom: 22
      }
    }, /*#__PURE__*/React.createElement(Stepper, {
      current: step
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        ...panel
      }
    }, bodies[step], /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginTop: 28,
        paddingTop: 20,
        borderTop: "1px solid var(--app-border)"
      }
    }, /*#__PURE__*/React.createElement(Button, {
      variant: "ghost",
      theme: "dark",
      onClick: back,
      leftIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "arrow-left",
        size: 16
      })
    }, step <= 1 ? "Recipes" : "Back"), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--app-text-muted)"
      }
    }, "Step ", step + 1, " of 6"), step >= 5 ? /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      theme: "dark",
      onClick: next,
      leftIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "play",
        size: 16
      })
    }, "Cook video") : /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      theme: "dark",
      onClick: next,
      rightIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "arrow-right",
        size: 16
      })
    }, "Next"))));
  }

  /* ---------------- Cooking ---------------- */
  function Cooking({
    onDone
  }) {
    const [pct, setPct] = useState(6);
    const done = pct >= 100;
    useEffect(() => {
      if (done) return;
      const t = setInterval(() => setPct(p => Math.min(100, p + 4)), 260);
      return () => clearInterval(t);
    }, [done]);
    const stageState = (lo, hi) => pct >= hi ? "done" : pct >= lo ? "active" : "todo";
    const stages = [{
      label: "Writing script",
      state: stageState(0, 25)
    }, {
      label: "Generating voiceover",
      state: stageState(25, 55)
    }, {
      label: "Assembling b-roll",
      state: stageState(55, 82)
    }, {
      label: "Rendering",
      state: stageState(82, 100)
    }];
    return /*#__PURE__*/React.createElement("div", {
      style: page
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: 560,
        margin: "0 auto",
        ...panel
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 12,
        marginBottom: 20
      }
    }, /*#__PURE__*/React.createElement("img", {
      src: "../../assets/glyph.svg",
      width: "30",
      height: "30",
      alt: ""
    }), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h1", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 24,
        letterSpacing: "-.01em",
        color: "var(--app-text-strong)",
        margin: 0
      }
    }, done ? "Recipe followed. Video ready." : "Cooking your video…"), /*#__PURE__*/React.createElement("p", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 14,
        color: "var(--app-text-body)",
        margin: "4px 0 0"
      }
    }, done ? "Your Upload Kit is packed and ready." : "You can leave this page — we'll keep cooking."))), done ? /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      theme: "dark",
      full: true,
      onClick: onDone,
      rightIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "arrow-right",
        size: 16
      })
    }, "View your Upload Kit") : /*#__PURE__*/React.createElement(ProgressStages, {
      percent: pct,
      eta: pct < 60 ? "about 3 minutes" : "about 1 minute",
      stages: stages
    })));
  }

  /* ---------------- Upload Kit ---------------- */
  function UploadKit({
    onAgain
  }) {
    const fields = [{
      k: "Title",
      v: "3 Reddit stories that broke 1M views"
    }, {
      k: "Description",
      v: "Three proven Reddit stories with the on-screen timer trick behind them. Made with ChannelRecipe."
    }, {
      k: "Tags",
      v: "reddit stories, faceless youtube, story time, timer"
    }];
    const [copied, setCopied] = useState(-1);
    const copy = (i, text) => {
      try {
        navigator.clipboard.writeText(text);
      } catch (e) {}
      setCopied(i);
      setTimeout(() => setCopied(-1), 1400);
    };
    return /*#__PURE__*/React.createElement("div", {
      style: page
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 10,
        marginBottom: 20
      }
    }, /*#__PURE__*/React.createElement(StatusChip, {
      kind: "proven"
    }, "Video ready"), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        color: "var(--app-text-muted)"
      }
    }, "This video: 1 credit")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "1.15fr 1fr",
        gap: 22,
        alignItems: "start"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        ...panel,
        padding: 0,
        overflow: "hidden"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        position: "relative",
        aspectRatio: "16/9",
        background: "linear-gradient(135deg,#241a52,#0F1222)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 60,
        height: 60,
        borderRadius: "50%",
        background: "rgba(255,255,255,.16)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center"
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "22",
      height: "22",
      viewBox: "0 0 14 14"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M4 2.5 L4 11.5 L11 7 Z",
      fill: "#fff"
    }))), /*#__PURE__*/React.createElement("span", {
      style: {
        position: "absolute",
        left: 14,
        bottom: 12,
        fontFamily: "var(--font-display)",
        fontWeight: 900,
        fontSize: 22,
        color: "#fff",
        letterSpacing: "-.01em",
        textShadow: "0 2px 12px rgba(0,0,0,.55)"
      }
    }, "3 Reddit stories", /*#__PURE__*/React.createElement("br", null), "that broke 1M")), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: 18,
        display: "flex",
        gap: 10,
        flexWrap: "wrap"
      }
    }, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      theme: "dark",
      leftIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "download",
        size: 16
      })
    }, "Download Upload Kit"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      theme: "dark",
      leftIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "play",
        size: 16
      })
    }, "Preview")), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: "0 18px 18px",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--app-text-muted)"
      }
    }, "MP4 \xB7 1080\xD71920 \xB7 00:48 \xB7 24 MB")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: 14
      }
    }, fields.map((f, i) => /*#__PURE__*/React.createElement("div", {
      key: f.k,
      style: {
        ...panel,
        padding: 16
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginBottom: 8
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        letterSpacing: ".06em",
        textTransform: "uppercase",
        color: "var(--accent)"
      }
    }, f.k), /*#__PURE__*/React.createElement("button", {
      type: "button",
      onClick: () => copy(i, f.v),
      style: {
        display: "flex",
        alignItems: "center",
        gap: 5,
        background: "none",
        border: "none",
        cursor: "pointer",
        color: copied === i ? "var(--success)" : "var(--app-text-body)",
        fontFamily: "var(--font-body)",
        fontSize: 13
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: copied === i ? "check" : "copy",
      size: 14
    }), " ", copied === i ? "Copied" : "Copy")), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 14,
        lineHeight: 1.5,
        color: "var(--app-text-body)"
      }
    }, f.v))), /*#__PURE__*/React.createElement(Button, {
      variant: "subtle",
      theme: "dark",
      full: true,
      onClick: onAgain,
      leftIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "plus",
        size: 16
      })
    }, "Make another"))));
  }

  /* ---------------- Account ---------------- */
  function Account({
    credits,
    onSignOut
  }) {
    const vids = [{
      t: "3 Reddit stories that broke 1M views",
      d: "Jul 5",
      views: "1,240,900",
      status: "monetized"
    }, {
      t: "The scariest text you'll read today",
      d: "Jul 2",
      views: "612,400",
      status: "monetized"
    }, {
      t: "10 facts that sound fake",
      d: "Jun 28",
      views: "388,120",
      status: "proven"
    }];
    return /*#__PURE__*/React.createElement("div", {
      style: page
    }, /*#__PURE__*/React.createElement("h1", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 32,
        letterSpacing: "-.02em",
        color: "var(--app-text-strong)",
        margin: "0 0 24px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center"
      }
    }, "Account", /*#__PURE__*/React.createElement(Button, {
      variant: "ghost",
      theme: "dark",
      onClick: onSignOut,
      leftIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "log-out",
        size: 16
      })
    }, "Sign out")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 18,
        marginBottom: 22
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: panel
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 14,
        color: "var(--app-text-muted)"
      }
    }, "Credits"), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 40,
        fontWeight: 600,
        color: "var(--app-text-strong)",
        margin: "6px 0 2px"
      }
    }, credits), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        color: "var(--app-text-muted)"
      }
    }, "resets Jul 14 \xB7 1 credit per video")), /*#__PURE__*/React.createElement("div", {
      style: panel
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 14,
        color: "var(--app-text-muted)"
      }
    }, "Plan"), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 26,
        color: "var(--app-text-strong)",
        margin: "6px 0 2px"
      }
    }, "$27 ", /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-body)",
        fontWeight: 400,
        fontSize: 15,
        color: "var(--app-text-muted)"
      }
    }, "/ month")), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        color: "var(--app-text-muted)"
      }
    }, "\u2248 15 videos \xB7 under $2 each"))), /*#__PURE__*/React.createElement("div", {
      style: {
        ...panel,
        padding: 4
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        padding: "14px 16px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        letterSpacing: ".06em",
        textTransform: "uppercase",
        color: "var(--app-text-muted)"
      }
    }, "Your videos"), vids.map((v, i) => /*#__PURE__*/React.createElement("div", {
      key: i,
      style: {
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "13px 16px",
        borderTop: "1px solid var(--app-border)"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        width: 64,
        height: 36,
        borderRadius: 6,
        background: "linear-gradient(135deg,#20264a,#0F1222)",
        flex: "none"
      }
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 15,
        fontWeight: 500,
        color: "var(--app-text-strong)",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis"
      }
    }, v.t), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--app-text-muted)"
      }
    }, v.d, " \xB7 ", v.views, " views")), /*#__PURE__*/React.createElement(StatusChip, {
      kind: v.status
    }, v.status === "monetized" ? "Monetized" : "Proven")))));
  }

  /* ---------------- Shell / router ---------------- */
  function ChannelRecipeApp() {
    const [view, setView] = useState("wall");
    const [step, setStep] = useState(1);
    const [recipe, setRecipe] = useState(RECIPES[0].name);
    const [credits, setCredits] = useState(11);
    const pick = name => {
      setRecipe(name);
      setStep(1);
      setView("pipeline");
    };
    const cook = () => {
      setView("cooking");
    };
    const kitDone = () => {
      setCredits(c => Math.max(0, c - 1));
      setView("kit");
    };
    return /*#__PURE__*/React.createElement("div", {
      style: {
        minHeight: "100vh",
        background: "var(--app-bg)"
      }
    }, view !== "signup" && /*#__PURE__*/React.createElement(TopBar, {
      credits: credits,
      view: view,
      onNav: v => setView(v)
    }), view === "signup" && /*#__PURE__*/React.createElement(Signup, {
      onDone: () => setView("wall")
    }), view === "wall" && /*#__PURE__*/React.createElement(Wall, {
      onPick: pick,
      credits: credits
    }), view === "pipeline" && /*#__PURE__*/React.createElement(Pipeline, {
      step: step,
      setStep: setStep,
      recipe: recipe,
      onCook: cook,
      onExit: () => setView("wall")
    }), view === "cooking" && /*#__PURE__*/React.createElement(Cooking, {
      onDone: kitDone
    }), view === "kit" && /*#__PURE__*/React.createElement(UploadKit, {
      onAgain: () => setView("wall")
    }), view === "account" && /*#__PURE__*/React.createElement(Account, {
      credits: credits,
      onSignOut: () => setView("signup")
    }));
  }
  window.ChannelRecipeApp = ChannelRecipeApp;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/app/AppShell.jsx", error: String((e && e.message) || e) }); }

// ui_kits/app/AppSteps.jsx
try { (() => {
/* Body content for each pipeline step. Presentational — driven by AppShell
   state via props. Global (no import/export). */
(function () {
  const {
    useState
  } = React;
  const CR = window.ChannelRecipeDesignSystem_51526c;
  const {
    Button,
    VoiceOption,
    Input
  } = CR;
  const {
    Icon
  } = window;
  const label = {
    fontFamily: "var(--font-body)",
    fontWeight: 600,
    fontSize: 15,
    color: "var(--app-text-strong)",
    marginBottom: 10
  };
  const hint = {
    fontFamily: "var(--font-body)",
    fontSize: 14,
    color: "var(--app-text-muted)",
    marginTop: 6
  };
  const surface = {
    background: "var(--app-surface-2)",
    border: "1px solid var(--app-border)",
    borderRadius: "var(--radius-md)"
  };
  function StepHead({
    title,
    sub
  }) {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        marginBottom: 22
      }
    }, /*#__PURE__*/React.createElement("h2", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 28,
        letterSpacing: "-.02em",
        color: "var(--app-text-strong)",
        margin: 0
      }
    }, title), /*#__PURE__*/React.createElement("p", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 15,
        color: "var(--app-text-body)",
        margin: "6px 0 0"
      }
    }, sub));
  }
  function Advanced({
    children
  }) {
    const [open, setOpen] = useState(false);
    return /*#__PURE__*/React.createElement("div", {
      style: {
        marginTop: 22,
        borderTop: "1px solid var(--app-border)",
        paddingTop: 16
      }
    }, /*#__PURE__*/React.createElement("button", {
      type: "button",
      onClick: () => setOpen(!open),
      style: {
        display: "flex",
        alignItems: "center",
        gap: 8,
        background: "none",
        border: "none",
        cursor: "pointer",
        color: "var(--app-text-body)",
        fontFamily: "var(--font-body)",
        fontSize: 14,
        fontWeight: 500,
        padding: 0
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        transform: open ? "rotate(90deg)" : "none",
        transition: "transform var(--dur-base)",
        display: "inline-flex"
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "chevron-right",
      size: 16
    })), "Advanced options"), open && /*#__PURE__*/React.createElement("div", {
      style: {
        marginTop: 14
      }
    }, children));
  }
  const chip = active => ({
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    padding: "13px 15px",
    borderRadius: "var(--radius-md)",
    cursor: "pointer",
    background: active ? "var(--accent-soft-dark)" : "var(--app-surface-2)",
    border: `1.5px solid ${active ? "var(--accent)" : "var(--app-border)"}`,
    fontFamily: "var(--font-body)",
    fontSize: 15,
    color: "var(--app-text-strong)"
  });
  function TitleStep({
    recipe
  }) {
    const titles = ["3 Reddit stories that broke 1M views", "The Reddit story that had everyone counting down", "You won't guess how this Reddit story ends"];
    const [sel, setSel] = useState(0);
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(StepHead, {
      title: "Pick a title",
      sub: "We drafted a few from proven patterns. Pick one or write your own."
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: 10
      }
    }, titles.map((t, i) => /*#__PURE__*/React.createElement("div", {
      key: i,
      style: chip(sel === i),
      onClick: () => setSel(i)
    }, /*#__PURE__*/React.createElement("span", null, t), /*#__PURE__*/React.createElement("span", {
      style: {
        width: 20,
        height: 20,
        flex: "none",
        borderRadius: "50%",
        border: `2px solid ${sel === i ? "var(--accent)" : "var(--app-border)"}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center"
      }
    }, sel === i && /*#__PURE__*/React.createElement("span", {
      style: {
        width: 10,
        height: 10,
        borderRadius: "50%",
        background: "var(--accent)"
      }
    }))))), /*#__PURE__*/React.createElement(Advanced, null, /*#__PURE__*/React.createElement(Input, {
      theme: "dark",
      label: "Write your own title",
      placeholder: "Your title"
    })));
  }
  function ScriptStep() {
    const script = "Ever wonder why some Reddit stories keep you watching to the very end?\n\nHere are three that blew up last week — and the exact timer trick behind them.\n\nFirst up: a story about a text that changed everything. Watch the clock in the corner…";
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(StepHead, {
      title: "Review the script",
      sub: "Written from the recipe. Edit anything, or keep it as is."
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        ...surface,
        padding: 16
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginBottom: 10
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        letterSpacing: ".06em",
        textTransform: "uppercase",
        color: "var(--accent)"
      }
    }, "Script \xB7 142 words \xB7 ~48s"), /*#__PURE__*/React.createElement("button", {
      type: "button",
      style: {
        display: "flex",
        alignItems: "center",
        gap: 6,
        background: "none",
        border: "none",
        color: "var(--app-text-body)",
        cursor: "pointer",
        fontFamily: "var(--font-body)",
        fontSize: 13
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "rotate-ccw",
      size: 14
    }), " Regenerate")), /*#__PURE__*/React.createElement("textarea", {
      defaultValue: script,
      style: {
        width: "100%",
        minHeight: 150,
        resize: "vertical",
        background: "transparent",
        border: "none",
        outline: "none",
        color: "var(--app-text-body)",
        fontFamily: "var(--font-body)",
        fontSize: 15,
        lineHeight: 1.6
      }
    })));
  }
  const VOICES = [{
    name: "Ava",
    descriptor: "Warm US female",
    recommended: true
  }, {
    name: "Marcus",
    descriptor: "Calm US male"
  }, {
    name: "Priya",
    descriptor: "Bright Indian English"
  }, {
    name: "Sofia",
    descriptor: "Soft UK female"
  }, {
    name: "Deo",
    descriptor: "Deep US male"
  }, {
    name: "Lena",
    descriptor: "Energetic US female"
  }];
  function VoiceStep() {
    const [sel, setSel] = useState(0);
    const [playing, setPlaying] = useState(-1);
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(StepHead, {
      title: "Choose a voice",
      sub: "Six voices, tuned for this recipe. The best pick is ready to go."
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 10
      }
    }, VOICES.map((v, i) => /*#__PURE__*/React.createElement(VoiceOption, {
      key: v.name,
      name: v.name,
      descriptor: v.descriptor,
      recommended: v.recommended,
      selected: sel === i,
      playing: playing === i,
      onSelect: () => setSel(i),
      onPlay: () => setPlaying(playing === i ? -1 : i)
    }))));
  }
  function ThumbStep() {
    const [sel, setSel] = useState(0);
    const thumbs = [{
      grad: "linear-gradient(135deg,#241a52,#0F1222)",
      text: "1,000,000 VIEWS?",
      tag: "Bold headline"
    }, {
      grad: "linear-gradient(135deg,#0f2f2a,#0F1222)",
      text: "THE TIMER TRICK",
      tag: "Curiosity"
    }];
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(StepHead, {
      title: "Pick a thumbnail",
      sub: "Two options in the recipe's proven style."
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 14
      }
    }, thumbs.map((t, i) => /*#__PURE__*/React.createElement("div", {
      key: i,
      onClick: () => setSel(i),
      style: {
        cursor: "pointer",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
        border: `2px solid ${sel === i ? "var(--accent)" : "var(--app-border)"}`
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        aspectRatio: "16/9",
        background: t.grad,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 12
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 900,
        fontSize: 26,
        color: "#fff",
        textAlign: "center",
        letterSpacing: "-.01em",
        textShadow: "0 2px 12px rgba(0,0,0,.5)"
      }
    }, t.text)), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: "9px 12px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        background: "var(--app-surface-2)"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 13,
        color: "var(--app-text-body)"
      }
    }, t.tag), sel === i && /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--accent)",
        display: "inline-flex"
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "check",
      size: 16
    })))))));
  }
  function CookStep({
    recipe
  }) {
    const rows = [["Recipe", recipe], ["Title", "3 Reddit stories that broke 1M views"], ["Voice", "Ava · Warm US female"], ["Length", "~48s · vertical 9:16"], ["Cost", "1 credit"]];
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(StepHead, {
      title: "Ready to cook",
      sub: "Here's your video. This uses 1 of your 11 credits."
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        ...surface,
        padding: 4
      }
    }, rows.map(([k, v], i) => /*#__PURE__*/React.createElement("div", {
      key: k,
      style: {
        display: "flex",
        justifyContent: "space-between",
        padding: "13px 15px",
        borderBottom: i < rows.length - 1 ? "1px solid var(--app-border)" : "none"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 14,
        color: "var(--app-text-muted)"
      }
    }, k), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: k === "Cost" ? "var(--font-mono)" : "var(--font-body)",
        fontSize: 14,
        color: "var(--app-text-strong)",
        fontWeight: 500
      }
    }, v)))));
  }
  window.AppSteps = {
    TitleStep,
    ScriptStep,
    VoiceStep,
    ThumbStep,
    CookStep,
    StepHead
  };
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/app/AppSteps.jsx", error: String((e && e.message) || e) }); }

// ui_kits/common.jsx
try { (() => {
/* Shared helpers for the UI kits: a Lucide-backed <Icon>, recipe data, and a
   section shell. Global (no import/export). Load before the section files. */
(function () {
  const {
    useRef,
    useEffect
  } = React;

  // Lucide-backed icon. The <i data-lucide> is swapped for an <svg> on mount.
  function Icon({
    name,
    size = 18,
    strokeWidth = 1.75,
    style,
    className
  }) {
    const ref = useRef(null);
    useEffect(() => {
      const host = ref.current;
      if (!host || !window.lucide) return;
      host.innerHTML = "";
      const el = document.createElement("i");
      el.setAttribute("data-lucide", name);
      host.appendChild(el);
      window.lucide.createIcons({
        attrs: {
          width: size,
          height: size,
          "stroke-width": strokeWidth
        },
        nameAttr: "data-lucide"
      });
    }, [name, size, strokeWidth]);
    return /*#__PURE__*/React.createElement("span", {
      ref: ref,
      className: className,
      style: {
        display: "inline-flex",
        width: size,
        height: size,
        lineHeight: 0,
        ...style
      },
      "aria-hidden": "true"
    });
  }
  const RECIPES = [{
    name: "Reddit story timers",
    promise: "Faceless story videos with on-screen text and a countdown.",
    cookTime: "~15 min",
    credits: "1 credit",
    rpm: "RPM $4–8",
    status: "new"
  }, {
    name: "Scary text stories",
    promise: "Two-voice horror chats over slow gradient b-roll.",
    cookTime: "~15 min",
    credits: "1 credit",
    rpm: "RPM $6–11",
    status: "proven"
  }, {
    name: "Fun facts countdown",
    promise: "Top-10 facts with a ticking number bug.",
    cookTime: "~15 min",
    credits: "1 credit",
    rpm: "RPM $5–9",
    status: "proven"
  }, {
    name: "Motivation shorts",
    promise: "Punchy quotes over cinematic loops.",
    cookTime: "~12 min",
    credits: "1 credit",
    rpm: "RPM $3–7",
    status: "proven"
  }, {
    name: "This day in history",
    promise: "One dated event, one clean map, one voice.",
    cookTime: "~15 min",
    credits: "1 credit",
    rpm: "RPM $5–10",
    status: "proven"
  }, {
    name: "Would you rather",
    promise: "Two options, a countdown, a reveal.",
    cookTime: "~13 min",
    credits: "1 credit",
    rpm: "RPM $4–8",
    status: "proven"
  }];

  // Full-bleed section band. tone: "canvas" | "band"
  function Section({
    tone = "canvas",
    children,
    style,
    id
  }) {
    return /*#__PURE__*/React.createElement("section", {
      id: id,
      style: {
        background: tone === "band" ? "var(--canvas-2)" : "var(--canvas)",
        ...style
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: "var(--container-max)",
        margin: "0 auto",
        padding: "var(--section-y) var(--gutter)"
      }
    }, children));
  }
  const Eyebrow = ({
    children
  }) => /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      letterSpacing: ".08em",
      textTransform: "uppercase",
      color: "var(--accent)",
      marginBottom: 14
    }
  }, children);
  Object.assign(window, {
    Icon,
    RECIPES,
    Section,
    Eyebrow
  });
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/common.jsx", error: String((e && e.message) || e) }); }

// ui_kits/marketing/Demo.jsx
try { (() => {
/* Hero demo loop — a silent, auto-advancing mock of the product pipeline,
   shown inside a BrowserFrame. Cycles: pick recipe -> script -> voice ->
   render -> finished video, then loops. Global (no import/export). */
(function () {
  const {
    useState,
    useEffect,
    useRef
  } = React;
  const {
    RecipeCard,
    ProgressStages,
    Stepper
  } = window.ChannelRecipeDesignSystem_51526c;
  const STAGES = ["recipe", "script", "voice", "render", "done"];
  const DUR = 2600;
  function DemoLoop() {
    const [i, setI] = useState(0);
    const timer = useRef(null);
    useEffect(() => {
      timer.current = setInterval(() => setI(p => (p + 1) % STAGES.length), DUR);
      return () => clearInterval(timer.current);
    }, []);
    const stage = STAGES[i];
    const stepIndex = {
      recipe: 0,
      script: 2,
      voice: 3,
      render: 5,
      done: 5
    }[stage];
    return /*#__PURE__*/React.createElement("div", {
      style: {
        padding: "18px 20px 22px",
        minHeight: 300,
        display: "flex",
        flexDirection: "column",
        gap: 16
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 10
      }
    }, /*#__PURE__*/React.createElement("img", {
      src: "../../assets/glyph.svg",
      width: "20",
      height: "20",
      alt: ""
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--app-text-muted)",
        letterSpacing: ".02em"
      }
    }, "new video")), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--app-text-muted)"
      }
    }, "11 credits")), /*#__PURE__*/React.createElement(Stepper, {
      current: stepIndex
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        display: "flex",
        alignItems: "stretch"
      }
    }, stage === "recipe" && /*#__PURE__*/React.createElement(RecipeStage, null), stage === "script" && /*#__PURE__*/React.createElement(ScriptStage, null), stage === "voice" && /*#__PURE__*/React.createElement(VoiceStage, null), stage === "render" && /*#__PURE__*/React.createElement(RenderStage, null), stage === "done" && /*#__PURE__*/React.createElement(DoneStage, null)));
  }
  function RecipeStage() {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 12,
        width: "100%",
        animation: "cr-fade .4s var(--ease-standard)"
      }
    }, /*#__PURE__*/React.createElement(RecipeCard, {
      theme: "dark",
      selected: true,
      status: "proven",
      name: "Reddit story timers",
      promise: "On-screen text + a countdown.",
      cookTime: "~15 min",
      credits: "1 credit",
      rpm: "RPM $4\u20138"
    }), /*#__PURE__*/React.createElement(RecipeCard, {
      theme: "dark",
      status: "new",
      name: "Scary text stories",
      promise: "Two-voice horror chats.",
      cookTime: "~15 min",
      credits: "1 credit",
      rpm: "RPM $6\u201311"
    }));
  }
  function Panel({
    children,
    label
  }) {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        width: "100%",
        background: "var(--app-surface)",
        border: "1px solid var(--app-border)",
        borderRadius: "var(--radius-card)",
        padding: 18,
        animation: "cr-fade .4s var(--ease-standard)"
      }
    }, label && /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        letterSpacing: ".08em",
        textTransform: "uppercase",
        color: "var(--accent)",
        marginBottom: 12
      }
    }, label), children);
  }
  function ScriptStage() {
    const lines = ["Ever wonder why some Reddit stories", "keep you watching to the very end?", "Here are three that blew up last week —", "and the exact timer trick behind them."];
    return /*#__PURE__*/React.createElement(Panel, {
      label: "Script"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: 9
      }
    }, lines.map((l, k) => /*#__PURE__*/React.createElement("div", {
      key: k,
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 14,
        lineHeight: 1.5,
        color: "var(--app-text-body)",
        opacity: 0,
        animation: `cr-line .4s var(--ease-standard) forwards`,
        animationDelay: `${k * 0.28}s`
      }
    }, l))));
  }
  function VoiceStage() {
    return /*#__PURE__*/React.createElement(Panel, {
      label: "Voice"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 14
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 44,
        height: 44,
        borderRadius: "50%",
        background: "var(--accent)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flex: "none"
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "16",
      height: "16",
      viewBox: "0 0 14 14"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M4 2.5 L4 11.5 L11 7 Z",
      fill: "#fff"
    }))), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-body)",
        fontWeight: 600,
        fontSize: 15,
        color: "var(--app-text-strong)"
      }
    }, "Ava \xB7 Warm US female"), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 3,
        height: 26,
        marginTop: 6
      }
    }, Array.from({
      length: 44
    }).map((_, k) => /*#__PURE__*/React.createElement("span", {
      key: k,
      style: {
        width: 3,
        borderRadius: 2,
        background: "var(--accent)",
        opacity: 0.35 + 0.65 * Math.abs(Math.sin(k * 0.9)),
        height: `${20 + 70 * Math.abs(Math.sin(k * 0.7))}%`,
        animation: "cr-eq 1s ease-in-out infinite",
        animationDelay: `${k * 0.03}s`
      }
    }))))));
  }
  function RenderStage() {
    return /*#__PURE__*/React.createElement(Panel, {
      label: "Cooking your video"
    }, /*#__PURE__*/React.createElement(ProgressStages, {
      percent: 72,
      eta: "about 2 minutes",
      stages: [{
        label: "Writing script",
        state: "done"
      }, {
        label: "Generating voiceover",
        state: "done"
      }, {
        label: "Assembling b-roll",
        state: "active"
      }, {
        label: "Rendering",
        state: "todo"
      }]
    }));
  }
  function DoneStage() {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        width: "100%",
        animation: "cr-fade .4s var(--ease-standard)",
        display: "flex",
        flexDirection: "column",
        gap: 12
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        position: "relative",
        width: "100%",
        aspectRatio: "16 / 9",
        background: "linear-gradient(135deg,#1b2140,#0F1222)",
        border: "1px solid var(--app-border)",
        borderRadius: "var(--radius-md)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 56,
        height: 56,
        borderRadius: "50%",
        background: "rgba(255,255,255,.14)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center"
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "20",
      height: "20",
      viewBox: "0 0 14 14"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M4 2.5 L4 11.5 L11 7 Z",
      fill: "#fff"
    }))), /*#__PURE__*/React.createElement("span", {
      style: {
        position: "absolute",
        left: 12,
        bottom: 10,
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 20,
        color: "#fff",
        letterSpacing: "-.01em",
        textShadow: "0 2px 10px rgba(0,0,0,.5)"
      }
    }, "3 Reddit stories that broke 1M")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 10
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        letterSpacing: ".06em",
        textTransform: "uppercase",
        color: "var(--success)",
        background: "var(--success-soft)",
        borderRadius: 999,
        padding: "3px 10px"
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "11",
      height: "11",
      viewBox: "0 0 12 12"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M1 9 L4 5 L6.5 7 L11 1",
      fill: "none",
      stroke: "var(--success)",
      strokeWidth: "1.6",
      strokeLinecap: "round",
      strokeLinejoin: "round"
    })), "Video ready"), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--app-text-muted)"
      }
    }, "Upload Kit \xB7 MP4 + title + tags")));
  }
  window.DemoLoop = DemoLoop;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/marketing/Demo.jsx", error: String((e && e.message) || e) }); }

// ui_kits/marketing/MarketingPage.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* ChannelRecipe marketing landing page — light cream canvas, single column.
   Composes DS primitives + the hero DemoLoop. Global (no import/export). */
(function () {
  const {
    useState
  } = React;
  const CR = window.ChannelRecipeDesignSystem_51526c;
  const {
    Logo,
    Button,
    PillBadge,
    RecipeCard,
    BrowserFrame,
    StatusChip
  } = CR;
  const {
    Icon,
    RECIPES,
    Section,
    Eyebrow
  } = window;

  /* ---------------- Nav ---------------- */
  function Nav() {
    return /*#__PURE__*/React.createElement("header", {
      style: {
        position: "sticky",
        top: 0,
        zIndex: 20,
        background: "color-mix(in srgb, var(--canvas) 88%, transparent)",
        backdropFilter: "blur(10px)",
        borderBottom: "1px solid var(--hairline)"
      }
    }, /*#__PURE__*/React.createElement("nav", {
      style: {
        maxWidth: "var(--container-max)",
        margin: "0 auto",
        padding: "14px var(--gutter)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between"
      }
    }, /*#__PURE__*/React.createElement(Logo, {
      variant: "horizontal",
      size: 28
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 22
      }
    }, /*#__PURE__*/React.createElement("a", {
      href: "#pricing",
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 15,
        fontWeight: 500
      }
    }, "Pricing"), /*#__PURE__*/React.createElement("a", {
      href: "#faq",
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 15,
        fontWeight: 500
      }
    }, "FAQ"), /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "sm"
    }, "Start free"))));
  }

  /* ---------------- Hero ---------------- */
  function Hero() {
    return /*#__PURE__*/React.createElement(Section, {
      style: {
        paddingTop: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        paddingTop: 64
      }
    }, /*#__PURE__*/React.createElement(PillBadge, null, "New: this week's recipe just dropped"), /*#__PURE__*/React.createElement("h1", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: "clamp(38px, 6.4vw, 68px)",
        lineHeight: 1.02,
        letterSpacing: "-.025em",
        color: "var(--ink)",
        margin: "22px 0 0",
        maxWidth: 900,
        textWrap: "balance"
      }
    }, "Turn a proven niche into a finished YouTube video in ", /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--accent)"
      }
    }, "15 minutes")), /*#__PURE__*/React.createElement("p", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: "clamp(17px, 2.2vw, 20px)",
        lineHeight: 1.5,
        color: "var(--ink-2)",
        margin: "20px 0 0",
        maxWidth: 620
      }
    }, "No editing, no camera, no experience. You pick the recipe \u2014 we write the script, record the voice, make the thumbnail, and render the video."), /*#__PURE__*/React.createElement("div", {
      style: {
        marginTop: 30,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 12
      }
    }, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg",
      leftIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "play",
        size: 17
      })
    }, "Make your first video free"), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        color: "var(--ink-3)",
        letterSpacing: ".01em"
      }
    }, "3 free videos \xB7 no card \xB7 2-minute setup"))), /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: 760,
        margin: "48px auto 0"
      }
    }, /*#__PURE__*/React.createElement(BrowserFrame, {
      url: "app.channelrecipe.com/new"
    }, /*#__PURE__*/React.createElement(window.DemoLoop, null)), /*#__PURE__*/React.createElement("p", {
      style: {
        textAlign: "center",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--ink-3)",
        marginTop: 14
      }
    }, "A real cook, start to finish \u2014 no talking, no fluff.")));
  }

  /* ---------------- How it works ---------------- */
  function HowItWorks() {
    const steps = [{
      n: "01",
      title: "Pick a recipe",
      body: "Choose a proven niche card. That's step one done — you've already started.",
      crop: /*#__PURE__*/React.createElement(MiniRecipe, null)
    }, {
      n: "02",
      title: "We cook it",
      body: "Script, voiceover, thumbnail, and a rendered video — about 15 minutes.",
      crop: /*#__PURE__*/React.createElement(MiniCook, null)
    }, {
      n: "03",
      title: "Download your Upload Kit",
      body: "Video file, title, description, and tags. Post it and you're live.",
      crop: /*#__PURE__*/React.createElement(MiniKit, null)
    }];
    return /*#__PURE__*/React.createElement(Section, {
      tone: "band",
      id: "how"
    }, /*#__PURE__*/React.createElement(Eyebrow, null, "How it works"), /*#__PURE__*/React.createElement("h2", {
      style: h2Style
    }, "Three steps. No guesswork."), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
        gap: 20,
        marginTop: 40
      }
    }, steps.map(s => /*#__PURE__*/React.createElement("div", {
      key: s.n,
      style: {
        background: "var(--white)",
        border: "1px solid var(--hairline)",
        borderRadius: "var(--radius-card)",
        padding: 22,
        boxShadow: "var(--shadow-sm)",
        display: "flex",
        flexDirection: "column",
        gap: 14
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        color: "var(--accent)",
        letterSpacing: ".08em"
      }
    }, s.n), /*#__PURE__*/React.createElement("div", {
      style: {
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
        border: "1px solid var(--hairline)"
      }
    }, s.crop), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 21,
        letterSpacing: "-.01em",
        color: "var(--ink)"
      }
    }, s.title), /*#__PURE__*/React.createElement("p", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 15,
        lineHeight: 1.55,
        color: "var(--ink-2)",
        margin: 0
      }
    }, s.body)))));
  }
  const cropWrap = {
    background: "var(--app-bg)",
    padding: 14,
    minHeight: 92
  };
  function MiniRecipe() {
    return /*#__PURE__*/React.createElement("div", {
      style: cropWrap
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        background: "var(--app-surface)",
        border: "1px solid var(--app-border)",
        borderRadius: 10,
        padding: 12,
        position: "relative"
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "26",
      height: "26",
      viewBox: "0 0 40 40",
      style: {
        position: "absolute",
        top: 0,
        right: 0
      }
    }, /*#__PURE__*/React.createElement("path", {
      d: "M40 0 V22 L18 0 Z",
      fill: "var(--accent)",
      opacity: "0.16"
    }), /*#__PURE__*/React.createElement("path", {
      d: "M15 11 L15 27 L29 19 Z",
      fill: "var(--accent)"
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 14,
        color: "var(--app-ink)"
      }
    }, "Reddit story timers"), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "var(--app-ink-3)",
        marginTop: 8
      }
    }, "~15 min \xB7 1 credit \xB7 RPM $4\u20138")));
  }
  function MiniCook() {
    return /*#__PURE__*/React.createElement("div", {
      style: cropWrap
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "var(--accent)",
        letterSpacing: ".06em",
        textTransform: "uppercase",
        marginBottom: 8
      }
    }, "Cooking your video"), /*#__PURE__*/React.createElement("div", {
      style: {
        height: 7,
        borderRadius: 999,
        background: "var(--app-surface-2)",
        overflow: "hidden"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        width: "68%",
        height: "100%",
        background: "var(--accent)",
        borderRadius: 999
      }
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        justifyContent: "space-between",
        marginTop: 7,
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "var(--app-ink-3)"
      }
    }, /*#__PURE__*/React.createElement("span", null, "68%"), /*#__PURE__*/React.createElement("span", null, "about 2 min")));
  }
  function MiniKit() {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        ...cropWrap,
        display: "flex",
        flexDirection: "column",
        gap: 8
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        aspectRatio: "16/9",
        background: "linear-gradient(135deg,#1b2140,#0F1222)",
        borderRadius: 6,
        display: "flex",
        alignItems: "center",
        justifyContent: "center"
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "16",
      height: "16",
      viewBox: "0 0 14 14"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M4 2.5 L4 11.5 L11 7 Z",
      fill: "#fff"
    }))), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        gap: 6,
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        color: "var(--app-ink-3)"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: pillMini
    }, "title"), /*#__PURE__*/React.createElement("span", {
      style: pillMini
    }, "description"), /*#__PURE__*/React.createElement("span", {
      style: pillMini
    }, "tags")));
  }
  const pillMini = {
    border: "1px solid var(--app-border)",
    borderRadius: 999,
    padding: "2px 7px"
  };

  /* ---------------- Proof strip ---------------- */
  function ProofStrip() {
    const vids = [{
      title: "3 Reddit stories that broke 1M",
      views: "1,240,900",
      recipe: "Reddit story timers"
    }, {
      title: "The scariest text you'll read today",
      views: "612,400",
      recipe: "Scary text stories"
    }, {
      title: "10 facts that sound fake",
      views: "388,120",
      recipe: "Fun facts countdown"
    }];
    return /*#__PURE__*/React.createElement(Section, null, /*#__PURE__*/React.createElement(Eyebrow, null, "Receipts"), /*#__PURE__*/React.createElement("h2", {
      style: h2Style
    }, "Real videos, made with the recipes."), /*#__PURE__*/React.createElement("p", {
      style: {
        ...subStyle
      }
    }, "Every recipe is run on a real channel before it ships. These are sample results from the founder's dashboard."), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
        gap: 18,
        marginTop: 36
      }
    }, vids.map(v => /*#__PURE__*/React.createElement("div", {
      key: v.title,
      style: {
        background: "var(--white)",
        border: "1px solid var(--hairline)",
        borderRadius: "var(--radius-card)",
        overflow: "hidden",
        boxShadow: "var(--shadow-sm)"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        aspectRatio: "16/9",
        background: "linear-gradient(135deg,#20264a,#0F1222)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        position: "relative"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 44,
        height: 44,
        borderRadius: "50%",
        background: "rgba(255,255,255,.16)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center"
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "16",
      height: "16",
      viewBox: "0 0 14 14"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M4 2.5 L4 11.5 L11 7 Z",
      fill: "#fff"
    })))), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: 16
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-body)",
        fontWeight: 600,
        fontSize: 15,
        color: "var(--ink)",
        lineHeight: 1.35
      }
    }, v.title), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 8,
        marginTop: 10
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 15,
        fontWeight: 600,
        color: "var(--ink)"
      }
    }, v.views), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 13,
        color: "var(--ink-3)"
      }
    }, "views")), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color: "var(--ink-3)",
        marginTop: 6
      }
    }, "via ", v.recipe))))));
  }

  /* ---------------- Recipe wall ---------------- */
  function RecipeWall() {
    return /*#__PURE__*/React.createElement(Section, {
      tone: "band",
      id: "recipes"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexWrap: "wrap",
        alignItems: "flex-end",
        justifyContent: "space-between",
        gap: 16
      }
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Eyebrow, null, "The Cookbook"), /*#__PURE__*/React.createElement("h2", {
      style: {
        ...h2Style,
        margin: 0
      }
    }, "A new proven recipe every week.")), /*#__PURE__*/React.createElement("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        fontFamily: "var(--font-body)",
        fontSize: 15,
        color: "var(--ink-2)"
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "calendar-check",
      size: 17,
      style: {
        color: "var(--accent)"
      }
    }), " This week's drop is live")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
        gap: 18,
        marginTop: 36,
        alignItems: "stretch"
      }
    }, RECIPES.map(r => /*#__PURE__*/React.createElement(RecipeCard, _extends({
      key: r.name
    }, r)))));
  }

  /* ---------------- Pricing ---------------- */
  function Pricing() {
    const feats = ["~15 videos a month", "Everything included — no API keys", "Every recipe + the weekly drop", "Full Upload Kit for each video", "Failed render? Credit auto-refunded"];
    return /*#__PURE__*/React.createElement(Section, {
      id: "pricing"
    }, /*#__PURE__*/React.createElement(Eyebrow, null, "Pricing"), /*#__PURE__*/React.createElement("h2", {
      style: h2Style
    }, "One plan. That's it."), /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: 440,
        margin: "40px auto 0",
        position: "relative",
        background: "var(--white)",
        border: "1px solid var(--hairline)",
        borderRadius: "var(--radius-card)",
        boxShadow: "var(--shadow-card)",
        padding: 30,
        overflow: "hidden"
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "48",
      height: "48",
      viewBox: "0 0 40 40",
      style: {
        position: "absolute",
        top: 0,
        right: 0
      }
    }, /*#__PURE__*/React.createElement("path", {
      d: "M40 0 V24 L16 0 Z",
      fill: "var(--accent)",
      opacity: "0.14"
    }), /*#__PURE__*/React.createElement("path", {
      d: "M13 12 L13 30 L30 21 Z",
      fill: "var(--accent)"
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "baseline",
        gap: 8
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: 54,
        letterSpacing: "-.02em",
        color: "var(--ink)"
      }
    }, "$27"), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 17,
        color: "var(--ink-3)"
      }
    }, "/ month")), /*#__PURE__*/React.createElement("div", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        color: "var(--ink-3)",
        marginTop: 4
      }
    }, "\u2248 15 videos \xB7 that's under $2 a video"), /*#__PURE__*/React.createElement("div", {
      style: {
        height: 1,
        background: "var(--hairline)",
        margin: "22px 0"
      }
    }), /*#__PURE__*/React.createElement("ul", {
      style: {
        listStyle: "none",
        padding: 0,
        margin: 0,
        display: "flex",
        flexDirection: "column",
        gap: 12
      }
    }, feats.map(f => /*#__PURE__*/React.createElement("li", {
      key: f,
      style: {
        display: "flex",
        alignItems: "center",
        gap: 11,
        fontFamily: "var(--font-body)",
        fontSize: 15,
        color: "var(--ink-2)"
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: "check",
      size: 17,
      style: {
        color: "var(--accent)",
        flex: "none"
      }
    }), " ", f))), /*#__PURE__*/React.createElement("div", {
      style: {
        marginTop: 26
      }
    }, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg",
      full: true
    }, "Make your first video free")), /*#__PURE__*/React.createElement("p", {
      style: {
        textAlign: "center",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        color: "var(--ink-3)",
        margin: "14px 0 0"
      }
    }, "Start with 3 free videos \xB7 no card \xB7 cancel anytime")));
  }

  /* ---------------- FAQ ---------------- */
  function FAQ() {
    const items = [{
      q: "Do I need my own API keys?",
      a: "No. Everything's included — the script model, the voices, the thumbnails, and the rendering. You just pick a recipe."
    }, {
      q: "Are the videos safe to upload to YouTube?",
      a: "Yes. Every recipe is built to follow YouTube's rules for original, faceless content, and you own what you make."
    }, {
      q: "What if a render fails?",
      a: "Your credit is refunded automatically — instantly, no support ticket. You'll see it back in your account with a note."
    }, {
      q: "Can I use my own script?",
      a: "Yes. Defaults are pre-filled so you can move fast, but you can edit the script, swap the voice, or change the thumbnail at any step."
    }, {
      q: "What happens after the free trial?",
      a: "After 3 free videos (or 7 days), it's $27/month for about 15 videos. No card is needed to start, and you can cancel anytime."
    }];
    const [open, setOpen] = useState(0);
    return /*#__PURE__*/React.createElement(Section, {
      tone: "band",
      id: "faq"
    }, /*#__PURE__*/React.createElement(Eyebrow, null, "FAQ"), /*#__PURE__*/React.createElement("h2", {
      style: h2Style
    }, "Straight answers."), /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: 720,
        marginTop: 34,
        display: "flex",
        flexDirection: "column",
        gap: 12
      }
    }, items.map((it, k) => {
      const isOpen = open === k;
      return /*#__PURE__*/React.createElement("div", {
        key: k,
        style: {
          background: "var(--white)",
          border: `1px solid ${isOpen ? "var(--accent)" : "var(--hairline)"}`,
          borderRadius: "var(--radius-md)",
          overflow: "hidden",
          transition: "border-color var(--dur-base)"
        }
      }, /*#__PURE__*/React.createElement("button", {
        type: "button",
        onClick: () => setOpen(isOpen ? -1 : k),
        style: {
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          padding: "18px 20px",
          background: "none",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          fontFamily: "var(--font-body)",
          fontWeight: 600,
          fontSize: 17,
          color: "var(--ink)"
        }
      }, it.q, /*#__PURE__*/React.createElement("span", {
        style: {
          transform: isOpen ? "rotate(180deg)" : "none",
          transition: "transform var(--dur-base)",
          color: "var(--accent)",
          flex: "none"
        }
      }, /*#__PURE__*/React.createElement(Icon, {
        name: "chevron-down",
        size: 20
      }))), isOpen && /*#__PURE__*/React.createElement("div", {
        style: {
          padding: "0 20px 20px",
          fontFamily: "var(--font-body)",
          fontSize: 15,
          lineHeight: 1.6,
          color: "var(--ink-2)"
        }
      }, it.a));
    })));
  }

  /* ---------------- Final CTA ---------------- */
  function FinalCTA() {
    return /*#__PURE__*/React.createElement("section", {
      style: {
        background: "var(--app-bg)"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: "var(--container-max)",
        margin: "0 auto",
        padding: "var(--section-y) var(--gutter)",
        textAlign: "center",
        display: "flex",
        flexDirection: "column",
        alignItems: "center"
      }
    }, /*#__PURE__*/React.createElement("img", {
      src: "../../assets/glyph.svg",
      width: "44",
      height: "44",
      alt: ""
    }), /*#__PURE__*/React.createElement("h2", {
      style: {
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: "clamp(30px,4.5vw,46px)",
        letterSpacing: "-.02em",
        color: "var(--app-ink)",
        margin: "20px 0 0",
        maxWidth: 620,
        textWrap: "balance"
      }
    }, "Pick a recipe. We handle the ", /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--accent)"
      }
    }, "cooking"), "."), /*#__PURE__*/React.createElement("p", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 18,
        color: "var(--app-ink-2)",
        margin: "16px 0 0"
      }
    }, "Your first video is free. You could be uploading tonight."), /*#__PURE__*/React.createElement("div", {
      style: {
        marginTop: 28,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 12
      }
    }, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg",
      theme: "dark",
      leftIcon: /*#__PURE__*/React.createElement(Icon, {
        name: "play",
        size: 17
      })
    }, "Make your first video free"), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        color: "var(--app-ink-3)"
      }
    }, "3 free videos \xB7 no card \xB7 2-minute setup"))));
  }

  /* ---------------- Footer ---------------- */
  function Footer() {
    return /*#__PURE__*/React.createElement("footer", {
      style: {
        background: "var(--canvas)",
        borderTop: "1px solid var(--hairline)"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: "var(--container-max)",
        margin: "0 auto",
        padding: "40px var(--gutter)",
        display: "flex",
        flexWrap: "wrap",
        gap: 20,
        alignItems: "center",
        justifyContent: "space-between"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: 10
      }
    }, /*#__PURE__*/React.createElement(Logo, {
      variant: "horizontal",
      size: 24
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-body)",
        fontSize: 13,
        color: "var(--ink-3)"
      }
    }, "From the team behind HeadStart Channels.")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexWrap: "wrap",
        gap: 20,
        alignItems: "center",
        fontFamily: "var(--font-body)",
        fontSize: 14
      }
    }, /*#__PURE__*/React.createElement("a", {
      href: "#terms"
    }, "Terms"), /*#__PURE__*/React.createElement("a", {
      href: "#privacy"
    }, "Privacy"), /*#__PURE__*/React.createElement("a", {
      href: "#refund"
    }, "Refund policy"), /*#__PURE__*/React.createElement("a", {
      href: "mailto:hello@channelrecipe.com",
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: 13
      }
    }, "hello@channelrecipe.com"))));
  }
  const h2Style = {
    fontFamily: "var(--font-display)",
    fontWeight: 800,
    fontSize: "clamp(28px,4vw,42px)",
    letterSpacing: "-.02em",
    color: "var(--ink)",
    margin: "0 0 0",
    textWrap: "balance"
  };
  const subStyle = {
    fontFamily: "var(--font-body)",
    fontSize: 17,
    lineHeight: 1.55,
    color: "var(--ink-2)",
    margin: "14px 0 0",
    maxWidth: 620
  };
  function MarketingPage() {
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Nav, null), /*#__PURE__*/React.createElement("main", null, /*#__PURE__*/React.createElement(Hero, null), /*#__PURE__*/React.createElement(HowItWorks, null), /*#__PURE__*/React.createElement(ProofStrip, null), /*#__PURE__*/React.createElement(RecipeWall, null), /*#__PURE__*/React.createElement(Pricing, null), /*#__PURE__*/React.createElement(FAQ, null), /*#__PURE__*/React.createElement(FinalCTA, null)), /*#__PURE__*/React.createElement(Footer, null));
  }
  window.MarketingPage = MarketingPage;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/marketing/MarketingPage.jsx", error: String((e && e.message) || e) }); }

__ds_ns.ProgressStages = __ds_scope.ProgressStages;

__ds_ns.Stepper = __ds_scope.Stepper;

__ds_ns.VoiceOption = __ds_scope.VoiceOption;

__ds_ns.Logo = __ds_scope.Logo;

__ds_ns.BrowserFrame = __ds_scope.BrowserFrame;

__ds_ns.CreditsChip = __ds_scope.CreditsChip;

__ds_ns.PillBadge = __ds_scope.PillBadge;

__ds_ns.StatusChip = __ds_scope.StatusChip;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.RecipeCard = __ds_scope.RecipeCard;

})();
