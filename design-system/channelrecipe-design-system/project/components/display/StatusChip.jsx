import React from "react";

/**
 * StatusChip — small status semantic chip.
 * kind: "new" (violet, weekly drop) | "proven" (green + spark) |
 *       "monetized" (green) | "warn" | "error" | "neutral".
 * Mono label, uppercase, receipt texture.
 */
export function StatusChip({ kind = "new", children, theme = "light", style, ...rest }) {
  const map = {
    new: { fg: "var(--accent-ink)", bg: "var(--accent)", bd: "transparent", spark: false },
    proven: { fg: "var(--success)", bg: "var(--success-soft)", bd: "color-mix(in srgb, var(--success) 32%, transparent)", spark: true },
    monetized: { fg: "var(--success)", bg: "var(--success-soft)", bd: "color-mix(in srgb, var(--success) 32%, transparent)", spark: false },
    warn: { fg: "#8a5a12", bg: "var(--warn-soft)", bd: "color-mix(in srgb, var(--warn) 40%, transparent)", spark: false },
    error: { fg: "var(--error)", bg: "var(--error-soft)", bd: "color-mix(in srgb, var(--error) 34%, transparent)", spark: false },
    neutral: { fg: "var(--text-muted)", bg: "var(--canvas-2)", bd: "var(--hairline)", spark: false },
  }[kind];

  const label = children || kind;

  return (
    <span
      style={{
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
        ...style,
      }}
      {...rest}
    >
      {map.spark && (
        <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M1 9 L4 5 L6.5 7 L11 1" fill="none" stroke="var(--success)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      {label}
    </span>
  );
}
