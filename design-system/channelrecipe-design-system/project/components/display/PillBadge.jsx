import React from "react";

/**
 * PillBadge — the rounded eyebrow pill above headlines.
 * Claim-free announcements: "New: this week's recipe just dropped".
 * A small violet dot signals the weekly drop.
 */
export function PillBadge({ children, theme = "light", dot = true, style, ...rest }) {
  const dark = theme === "dark";
  return (
    <span
      style={{
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
        ...style,
      }}
      {...rest}
    >
      {dot && (
        <span
          style={{
            width: "8px",
            height: "8px",
            borderRadius: "50%",
            background: "var(--accent)",
            boxShadow: "0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent)",
            flex: "none",
          }}
        />
      )}
      {children}
    </span>
  );
}
