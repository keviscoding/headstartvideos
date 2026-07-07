import * as React from "react";

/**
 * The signature ChannelRecipe object: a niche recipe card with the
 * folded-corner-as-play-triangle motif.
 *
 * @startingPoint section="Recipe" subtitle="The signature recipe card" viewport="360x220"
 */
export interface RecipeCardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Niche name, e.g. "Reddit story timers" */
  name?: string;
  /** One-line promise about the niche */
  promise?: string;
  /** Cook time, shown in mono, e.g. "~15 min" */
  cookTime?: string;
  /** Credit cost, mono, e.g. "1 credit" */
  credits?: string;
  /** RPM band, mono, e.g. "RPM $4–8" */
  rpm?: string;
  /** Status chip: "new" (violet weekly drop) | "proven" (green + spark) | "none" */
  status?: "new" | "proven" | "none";
  /** "light" for cream marketing, "dark" for the app surface */
  theme?: "light" | "dark";
  /** Selected state (violet 2px border) */
  selected?: boolean;
  onClick?: (e: React.MouseEvent) => void;
}

export function RecipeCard(props: RecipeCardProps): JSX.Element;
