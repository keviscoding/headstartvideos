import React from "react";

/**
 * Stepper — the app's linear 6-step pipeline indicator.
 * Recipe -> Title -> Script -> Voice -> Thumbnail -> Cook.
 * Endowed progress: step 1 completes the moment a recipe is picked.
 */
export function Stepper({
  steps = ["Recipe", "Title", "Script", "Voice", "Thumbnail", "Cook"],
  current = 0,
  theme = "dark",
  style,
  ...rest
}) {
  const dark = theme !== "light";
  const line = dark ? "var(--app-border)" : "var(--hairline)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0",
        width: "100%",
        ...style,
      }}
      {...rest}
    >
      {steps.map((label, i) => {
        const done = i < current;
        const active = i === current;
        const dotBg = done ? "var(--accent)" : active ? "var(--accent)" : dark ? "var(--app-surface-2)" : "var(--white)";
        const dotBorder = done || active ? "var(--accent)" : line;
        const dotFg = done || active ? "var(--accent-ink)" : dark ? "var(--app-text-muted)" : "var(--text-muted)";
        return (
          <React.Fragment key={label}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "7px", flex: "none" }}>
              <span
                style={{
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
                  transition: "all var(--dur-base) var(--ease-standard)",
                }}
              >
                {done ? (
                  <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
                    <path d="M2.5 7.5 L6 11 L11.5 3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  i + 1
                )}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-body)",
                  fontSize: "var(--fs-caption)",
                  fontWeight: active ? "var(--fw-semibold)" : "var(--fw-regular)",
                  color: active
                    ? dark ? "var(--app-text-strong)" : "var(--ink)"
                    : done
                    ? "var(--accent)"
                    : dark ? "var(--app-text-muted)" : "var(--text-muted)",
                  whiteSpace: "nowrap",
                }}
              >
                {label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <span
                style={{
                  flex: 1,
                  height: "2px",
                  margin: "0 6px",
                  marginBottom: "22px",
                  background: i < current ? "var(--accent)" : line,
                  borderRadius: "2px",
                  transition: "background var(--dur-base)",
                }}
              />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
