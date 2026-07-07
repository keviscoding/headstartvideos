import * as React from "react";

/**
 * The app's linear 6-step pipeline indicator: Recipe → Title → Script → Voice → Thumbnail → Cook.
 * Endowed progress — step 1 is complete as soon as a recipe card is picked.
 *
 * @startingPoint section="App" subtitle="Six-step pipeline stepper" viewport="720x110"
 */
export interface StepperProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Step labels; defaults to the six-step pipeline */
  steps?: string[];
  /** Index of the active step (0-based) */
  current?: number;
  theme?: "light" | "dark";
}

export function Stepper(props: StepperProps): JSX.Element;
