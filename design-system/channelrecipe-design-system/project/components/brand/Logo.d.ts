import * as React from "react";

/**
 * ChannelRecipe logo: glyph (recipe card + folded-corner play triangle),
 * wordmark ("Channel" ink + "Recipe" violet), or the horizontal lockup.
 *
 * @startingPoint section="Brand" subtitle="Logo lockups" viewport="360x120"
 */
export interface LogoProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** "horizontal" (glyph + wordmark) | "glyph" | "wordmark" */
  variant?: "horizontal" | "glyph" | "wordmark";
  /** "light" (cream) or "dark" (app) surface */
  theme?: "light" | "dark";
  /** Glyph edge length in px; wordmark scales from it */
  size?: number;
}

export function Logo(props: LogoProps): JSX.Element;
