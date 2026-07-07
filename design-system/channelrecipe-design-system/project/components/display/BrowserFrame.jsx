import React from "react";

/**
 * BrowserFrame — browser-chrome mockup. The dark product framed on the light
 * marketing page IS the visual identity. Holds screenshots or the demo loop.
 */
export function BrowserFrame({
  url = "app.channelrecipe.com",
  theme = "dark", // frame chrome tone
  children,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const chrome = dark ? "var(--app-surface)" : "var(--white)";
  const chromeBorder = dark ? "var(--app-border)" : "var(--hairline)";
  const barText = dark ? "var(--app-text-muted)" : "var(--text-muted)";
  return (
    <div
      style={{
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
        border: `1px solid ${chromeBorder}`,
        boxShadow: "var(--shadow-lg)",
        background: chrome,
        ...style,
      }}
      {...rest}
    >
      {/* chrome bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          padding: "11px 14px",
          background: chrome,
          borderBottom: `1px solid ${chromeBorder}`,
        }}
      >
        <div style={{ display: "flex", gap: "7px" }}>
          {["#E5484D", "#E8A13C", "#14B87A"].map((c) => (
            <span key={c} style={{ width: "11px", height: "11px", borderRadius: "50%", background: c, opacity: 0.85 }} />
          ))}
        </div>
        <div
          style={{
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
            color: barText,
          }}
        >
          <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M3.5 5.5V4a2.5 2.5 0 015 0v1.5" fill="none" stroke={barText} strokeWidth="1.2" />
            <rect x="2.5" y="5.5" width="7" height="5" rx="1.2" fill={barText} opacity="0.85" />
          </svg>
          {url}
        </div>
      </div>
      <div style={{ background: dark ? "var(--app-bg)" : "var(--canvas)" }}>{children}</div>
    </div>
  );
}
