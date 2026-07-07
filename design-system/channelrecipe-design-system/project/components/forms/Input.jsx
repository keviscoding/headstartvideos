import React from "react";

/** Input — text/email field. Calm, generous, cream or dark surface. */
export function Input({
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
  return (
    <label htmlFor={inputId} style={{ display: "flex", flexDirection: "column", gap: "6px", ...style }}>
      {label && (
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: "var(--fs-small)",
            fontWeight: "var(--fw-medium)",
            color: dark ? "var(--app-text-body)" : "var(--text-body)",
          }}
        >
          {label}
        </span>
      )}
      <span
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          background: dark ? "var(--app-surface-2)" : "var(--white)",
          border: `1px solid ${error ? "var(--error)" : dark ? "var(--app-border)" : "var(--hairline)"}`,
          borderRadius: "var(--radius-md)",
          padding: "12px 14px",
        }}
      >
        {leftIcon}
        <input
          id={inputId}
          style={{
            flex: 1,
            border: "none",
            outline: "none",
            background: "transparent",
            fontFamily: "var(--font-body)",
            fontSize: "var(--fs-body)",
            color: dark ? "var(--app-text-strong)" : "var(--ink)",
            minWidth: 0,
          }}
          {...rest}
        />
      </span>
      {(hint || error) && (
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: "var(--fs-caption)",
            color: error ? "var(--error)" : dark ? "var(--app-text-muted)" : "var(--text-muted)",
          }}
        >
          {error || hint}
        </span>
      )}
    </label>
  );
}
