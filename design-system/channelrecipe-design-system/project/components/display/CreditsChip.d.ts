import * as React from "react";

/** Always-visible mono credit counter for the app top corner: "11 credits · resets Jul 14". */
export interface CreditsChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  credits?: number;
  /** Reset date label */
  resets?: string;
  /** Hide the reset date */
  compact?: boolean;
  theme?: "light" | "dark";
}

export function CreditsChip(props: CreditsChipProps): JSX.Element;
