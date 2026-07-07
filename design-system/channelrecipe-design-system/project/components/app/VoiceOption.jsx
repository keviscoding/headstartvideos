import React from "react";

/**
 * VoiceOption — selectable voice row with a play button.
 * Six curated voices; best pre-selected. Never more choices than necessary.
 */
export function VoiceOption({
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
  return (
    <div
      onClick={onSelect}
      role="radio"
      aria-checked={selected}
      tabIndex={0}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "12px 14px",
        borderRadius: "var(--radius-md)",
        background: selected
          ? dark ? "var(--accent-soft-dark)" : "var(--accent-soft)"
          : dark ? "var(--app-surface-2)" : "var(--white)",
        border: `1.5px solid ${selected ? "var(--accent)" : dark ? "var(--app-border)" : "var(--hairline)"}`,
        cursor: "pointer",
        transition: "border-color var(--dur-fast), background var(--dur-fast)",
        ...style,
      }}
      {...rest}
    >
      <button
        type="button"
        aria-label={playing ? "Pause preview" : "Play preview"}
        onClick={(e) => {
          e.stopPropagation();
          onPlay && onPlay();
        }}
        style={{
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
          justifyContent: "center",
        }}
      >
        {playing ? (
          <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
            <rect x="3" y="2.5" width="3" height="9" rx="1" fill="currentColor" />
            <rect x="8" y="2.5" width="3" height="9" rx="1" fill="currentColor" />
          </svg>
        ) : (
          <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M4 2.5 L4 11.5 L11 7 Z" fill="currentColor" />
          </svg>
        )}
      </button>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            fontFamily: "var(--font-body)",
            fontWeight: "var(--fw-semibold)",
            fontSize: "var(--fs-body)",
            color: dark ? "var(--app-text-strong)" : "var(--ink)",
          }}
        >
          {name}
          {recommended && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "10px",
                letterSpacing: "var(--tracking-eyebrow)",
                textTransform: "uppercase",
                color: "var(--accent)",
                background: dark ? "var(--accent-soft-dark)" : "var(--accent-soft)",
                borderRadius: "var(--radius-pill)",
                padding: "2px 7px",
              }}
            >
              Best pick
            </span>
          )}
        </div>
        <div
          style={{
            fontFamily: "var(--font-body)",
            fontSize: "var(--fs-caption)",
            color: dark ? "var(--app-text-muted)" : "var(--text-muted)",
          }}
        >
          {descriptor}
        </div>
      </div>

      {/* selection radio */}
      <span
        style={{
          width: "20px",
          height: "20px",
          flex: "none",
          borderRadius: "50%",
          border: `2px solid ${selected ? "var(--accent)" : dark ? "var(--app-border)" : "var(--hairline)"}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {selected && <span style={{ width: "10px", height: "10px", borderRadius: "50%", background: "var(--accent)" }} />}
      </span>
    </div>
  );
}
