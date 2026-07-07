import React from "react";

/**
 * ProgressStages — honest, narrated cooking progress.
 * Shows a real progress bar + named stages. Occupied time feels half as long.
 * stages: [{ label, state }] where state = "done" | "active" | "todo".
 */
export function ProgressStages({
  stages = [
    { label: "Writing script", state: "done" },
    { label: "Generating voiceover", state: "active" },
    { label: "Assembling b-roll", state: "todo" },
    { label: "Rendering", state: "todo" },
  ],
  percent = 42,
  eta = "about 3 minutes",
  theme = "dark",
  style,
  ...rest
}) {
  const dark = theme !== "light";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)", ...style }} {...rest}>
      {/* progress bar */}
      <div>
        <div
          style={{
            height: "8px",
            borderRadius: "var(--radius-pill)",
            background: dark ? "var(--app-surface-2)" : "var(--canvas-2)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${percent}%`,
              height: "100%",
              borderRadius: "var(--radius-pill)",
              background: "var(--accent)",
              transition: "width var(--dur-slow) var(--ease-standard)",
            }}
          />
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: "8px",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--fs-mono-s)",
            letterSpacing: "var(--tracking-mono)",
            color: dark ? "var(--app-text-muted)" : "var(--text-muted)",
          }}
        >
          <span>{percent}%</span>
          <span>{eta}</span>
        </div>
      </div>

      {/* stage list */}
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {stages.map((s) => {
          const done = s.state === "done";
          const active = s.state === "active";
          return (
            <div key={s.label} style={{ display: "flex", alignItems: "center", gap: "11px" }}>
              <span
                style={{
                  width: "20px",
                  height: "20px",
                  flex: "none",
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: done ? "var(--accent)" : active ? "transparent" : "transparent",
                  border: done ? "none" : `1.5px solid ${active ? "var(--accent)" : dark ? "var(--app-border)" : "var(--hairline)"}`,
                  color: "var(--accent-ink)",
                }}
              >
                {done ? (
                  <svg width="11" height="11" viewBox="0 0 14 14" aria-hidden="true">
                    <path d="M2.5 7.5 L6 11 L11.5 3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : active ? (
                  <Spinner />
                ) : null}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-body)",
                  fontSize: "var(--fs-body)",
                  fontWeight: active ? "var(--fw-semibold)" : "var(--fw-regular)",
                  color: active
                    ? dark ? "var(--app-text-strong)" : "var(--ink)"
                    : done
                    ? dark ? "var(--app-text-body)" : "var(--text-body)"
                    : dark ? "var(--app-text-muted)" : "var(--text-muted)",
                }}
              >
                {s.label}
                {active && "…"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" style={{ animation: "cr-spin 0.8s linear infinite" }}>
      <circle cx="8" cy="8" r="6" fill="none" stroke="var(--accent)" strokeWidth="2" strokeOpacity="0.25" />
      <path d="M8 2 a6 6 0 0 1 6 6" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
      <style>{`@keyframes cr-spin { to { transform: rotate(360deg); } }`}</style>
    </svg>
  );
}
