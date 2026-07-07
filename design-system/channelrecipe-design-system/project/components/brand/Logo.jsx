import React from "react";

/**
 * Logo — glyph (recipe card + folded-corner play triangle) with optional wordmark.
 * "Recipe" is set in violet, "Channel" in ink (camel-case lockup).
 */
export function Logo({
  variant = "horizontal", // "horizontal" | "glyph" | "wordmark"
  theme = "light", // "light" | "dark"
  size = 28,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const ink = dark ? "var(--app-text-strong)" : "var(--ink)";

  const glyph = (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" aria-hidden={variant !== "glyph"}>
      <path
        d="M18 6 H62 L82 26 V82 A12 12 0 0 1 70 94 H30 A12 12 0 0 1 18 82 V18 A12 12 0 0 1 30 6 Z"
        fill={ink}
      />
      <path d="M62 6 L82 26 H66 A4 4 0 0 1 62 22 Z" fill={dark ? "var(--app-bg)" : "var(--canvas)"} />
      <path d="M42 40 L42 68 L66 54 Z" fill="var(--accent)" />
    </svg>
  );

  const wordmark = (
    <span
      style={{
        fontFamily: "var(--font-display)",
        fontWeight: "var(--fw-heavy)",
        fontSize: size * 0.72,
        letterSpacing: "var(--tracking-tight)",
        lineHeight: 1,
        color: ink,
      }}
    >
      Channel<span style={{ color: "var(--accent)" }}>Recipe</span>
    </span>
  );

  if (variant === "glyph") {
    return (
      <span role="img" aria-label="ChannelRecipe" style={{ display: "inline-flex", ...style }} {...rest}>
        {glyph}
      </span>
    );
  }
  if (variant === "wordmark") {
    return (
      <span role="img" aria-label="ChannelRecipe" style={{ display: "inline-flex", ...style }} {...rest}>
        {wordmark}
      </span>
    );
  }
  return (
    <span
      role="img"
      aria-label="ChannelRecipe"
      style={{ display: "inline-flex", alignItems: "center", gap: size * 0.32, ...style }}
      {...rest}
    >
      {glyph}
      {wordmark}
    </span>
  );
}
