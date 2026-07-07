import * as React from "react";

/** Small status semantic chip in mono uppercase. Violet NEW, green PROVEN (with spark), etc. */
export interface StatusChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** new | proven | monetized | warn | error | neutral */
  kind?: "new" | "proven" | "monetized" | "warn" | "error" | "neutral";
  theme?: "light" | "dark";
}

export function StatusChip(props: StatusChipProps): JSX.Element;
