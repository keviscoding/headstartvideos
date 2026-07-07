import * as React from "react";

/**
 * Honest, narrated cooking progress — real bar + named stages so occupied
 * time feels half as long. "Cooking your video — about 3 minutes."
 */
export interface Stage {
  label: string;
  state: "done" | "active" | "todo";
}
export interface ProgressStagesProps extends React.HTMLAttributes<HTMLDivElement> {
  stages?: Stage[];
  /** 0–100 */
  percent?: number;
  /** ETA text, e.g. "about 3 minutes" */
  eta?: string;
  theme?: "light" | "dark";
}

export function ProgressStages(props: ProgressStagesProps): JSX.Element;
