import React from "react";

/**
 * RecipeCard — the atomic brand object.
 * A rounded card with a folded top-right corner that reads as a play triangle
 * (recipe + video in one shape). Anatomy: niche name -> one-line promise ->
 * mono meta row (cook time / credit / RPM band) -> status chip.
 */
export function RecipeCard({
  name = "Recipe name",
  promise = "One-line promise about the niche.",
  cookTime = "~15 min",
  credits = "1 credit",
  rpm = "RPM $4–8",
  status = "none", // "new" | "proven" | "none"
  theme = "light", // "light" | "dark"
  selected = false,
  onClick,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const surface = dark ? "var(--app-surface-card)" : "var(--surface-card)";
  const border = selected
    ? "var(--accent)"
    : dark
    ? "var(--app-border-card)"
    : "var(--border-card)";
  const strong = dark ? "var(--app-text-strong)" : "var(--text-strong)";
  const body = dark ? "var(--app-text-body)" : "var(--text-body)";
  const muted = dark ? "var(--app-text-muted)" : "var(--text-muted)";

  const clickable = typeof onClick === "function";

  return (
    <div
      onClick={onClick}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      style={{
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
        ...style,
      }}
      {...rest}
    >
      {/* Folded corner = play triangle */}
      <FoldPlay />

      {status !== "none" && (
        <div style={{ marginBottom: "var(--space-1)" }}>
          <StatusPill status={status} />
        </div>
      )}

      <div
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: "var(--fw-heavy)",
          fontSize: "var(--fs-title)",
          lineHeight: "var(--lh-title)",
          letterSpacing: "var(--tracking-tight)",
          color: strong,
          maxWidth: "88%",
        }}
      >
        {name}
      </div>

      <div
        style={{
          fontFamily: "var(--font-body)",
          fontSize: "var(--fs-small)",
          lineHeight: "var(--lh-small)",
          color: body,
        }}
      >
        {promise}
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "6px 10px",
          alignItems: "center",
          marginTop: "auto",
          paddingTop: "var(--space-2)",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--fs-mono-s)",
          letterSpacing: "var(--tracking-mono)",
          color: muted,
        }}
      >
        <span>{cookTime}</span>
        <Dot />
        <span>{credits}</span>
        <Dot />
        <span>{rpm}</span>
      </div>
    </div>
  );
}

function Dot() {
  return <span style={{ opacity: 0.5 }}>·</span>;
}

function FoldPlay() {
  return (
    <svg
      width="40"
      height="40"
      viewBox="0 0 40 40"
      aria-hidden="true"
      style={{ position: "absolute", top: 0, right: 0 }}
    >
      {/* fold shadow */}
      <path d="M40 0 V22 L18 0 Z" fill="var(--accent)" opacity="0.16" />
      {/* the play triangle formed by the fold */}
      <path d="M15 11 L15 27 L29 19 Z" fill="var(--accent)" />
    </svg>
  );
}

function StatusPill({ status }) {
  if (status === "proven") {
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
          color: "var(--success)",
          background: "var(--success-soft)",
          border: "1px solid color-mix(in srgb, var(--success) 30%, transparent)",
          borderRadius: "var(--radius-pill)",
          padding: "3px 9px",
        }}
      >
        <Spark />
        Proven
      </span>
    );
  }
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--fs-mono-s)",
        letterSpacing: "var(--tracking-eyebrow)",
        textTransform: "uppercase",
        color: "var(--accent-ink)",
        background: "var(--accent)",
        borderRadius: "var(--radius-pill)",
        padding: "3px 10px",
      }}
    >
      New
    </span>
  );
}

function Spark() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true">
      <path
        d="M1 9 L4 5 L6.5 7 L11 1"
        fill="none"
        stroke="var(--success)"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
