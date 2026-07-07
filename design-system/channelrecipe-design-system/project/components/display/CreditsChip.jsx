import React from "react";

/**
 * CreditsChip — always-visible mono credit counter for the app top corner.
 * e.g. "11 credits · resets Jul 14". Compact per-cook variant too.
 */
export function CreditsChip({ credits = 11, resets = "Jul 14", compact = false, theme = "dark", style, ...rest }) {
  const dark = theme === "dark";
  return (
    <span
      style={{
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
        ...style,
      }}
      {...rest}
    >
      <span
        style={{
          width: "7px",
          height: "7px",
          borderRadius: "50%",
          background: "var(--accent)",
          flex: "none",
        }}
      />
      <span style={{ color: dark ? "var(--app-text-strong)" : "var(--ink)", fontWeight: "var(--fw-medium)" }}>
        {credits} credits
      </span>
      {!compact && (
        <>
          <span style={{ opacity: 0.45 }}>·</span>
          <span>resets {resets}</span>
        </>
      )}
    </span>
  );
}
