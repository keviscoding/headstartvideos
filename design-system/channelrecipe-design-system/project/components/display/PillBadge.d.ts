import * as React from "react";

/** Rounded eyebrow pill above headlines. Claim-free announcements with a violet drop dot. */
export interface PillBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  theme?: "light" | "dark";
  /** Show the violet drop dot */
  dot?: boolean;
}

export function PillBadge(props: PillBadgeProps): JSX.Element;
