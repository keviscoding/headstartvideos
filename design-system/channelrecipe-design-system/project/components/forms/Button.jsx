import React from "react";

/**
 * Button — rounded CTA. Buttons say exactly what they do ("Cook video").
 * variants: primary (violet), secondary (ink outline), ghost, subtle.
 */
export function Button({
  variant = "primary",
  size = "md", // "sm" | "md" | "lg"
  theme = "light",
  full = false,
  disabled = false,
  leftIcon,
  rightIcon,
  children,
  style,
  ...rest
}) {
  const dark = theme === "dark";
  const pads = {
    sm: { padding: "8px 14px", fontSize: "var(--fs-small)", radius: "var(--radius-sm)" },
    md: { padding: "12px 20px", fontSize: "var(--fs-body)", radius: "var(--radius-md)" },
    lg: { padding: "16px 28px", fontSize: "var(--fs-lead)", radius: "var(--radius-md)" },
  }[size];

  const base = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "9px",
    width: full ? "100%" : "auto",
    fontFamily: "var(--font-body)",
    fontWeight: "var(--fw-semibold)",
    fontSize: pads.fontSize,
    lineHeight: 1.1,
    letterSpacing: "var(--tracking-tight)",
    padding: pads.padding,
    borderRadius: pads.radius,
    border: "1px solid transparent",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: "background var(--dur-fast), border-color var(--dur-fast), transform var(--dur-fast), box-shadow var(--dur-base)",
    whiteSpace: "nowrap",
  };

  const looks = {
    primary: {
      background: "var(--accent)",
      color: "var(--accent-ink)",
      boxShadow: "var(--shadow-accent)",
    },
    secondary: {
      background: "transparent",
      color: dark ? "var(--app-text-strong)" : "var(--ink)",
      borderColor: dark ? "var(--app-border)" : "var(--ink)",
    },
    ghost: {
      background: "transparent",
      color: dark ? "var(--app-text-body)" : "var(--ink-2)",
    },
    subtle: {
      background: dark ? "var(--app-surface-2)" : "var(--accent-soft)",
      color: dark ? "var(--app-text-strong)" : "var(--accent)",
    },
  }[variant];

  return (
    <button
      type="button"
      disabled={disabled}
      style={{ ...base, ...looks, ...style }}
      onMouseEnter={(e) => {
        if (disabled) return;
        if (variant === "primary") e.currentTarget.style.background = "var(--accent-hover)";
        else if (variant === "secondary") e.currentTarget.style.background = dark ? "var(--app-surface-2)" : "var(--canvas-2)";
        else if (variant === "ghost") e.currentTarget.style.background = dark ? "var(--app-surface-2)" : "var(--canvas-2)";
        else {
          e.currentTarget.style.background = dark ? "var(--accent-soft-dark)" : "var(--accent)";
          if (!dark) e.currentTarget.style.color = "var(--accent-ink)";
        }
      }}
      onMouseLeave={(e) => {
        if (disabled) return;
        e.currentTarget.style.background = looks.background;
        e.currentTarget.style.color = looks.color;
      }}
      {...rest}
    >
      {leftIcon}
      {children}
      {rightIcon}
    </button>
  );
}
