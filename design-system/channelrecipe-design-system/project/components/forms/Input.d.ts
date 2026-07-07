import * as React from "react";

/** Text / email input with optional label, hint, and error state. */
export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  theme?: "light" | "dark";
  leftIcon?: React.ReactNode;
}

export function Input(props: InputProps): JSX.Element;
