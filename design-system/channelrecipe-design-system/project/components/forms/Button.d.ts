import * as React from "react";

/**
 * Rounded CTA button. Copy says exactly what it does ("Cook video", never "Submit").
 *
 * @startingPoint section="Forms" subtitle="Buttons — primary, secondary, ghost" viewport="360x120"
 */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary (violet) | secondary (ink outline) | ghost | subtle (violet tint) */
  variant?: "primary" | "secondary" | "ghost" | "subtle";
  size?: "sm" | "md" | "lg";
  theme?: "light" | "dark";
  /** Stretch to container width */
  full?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

export function Button(props: ButtonProps): JSX.Element;
