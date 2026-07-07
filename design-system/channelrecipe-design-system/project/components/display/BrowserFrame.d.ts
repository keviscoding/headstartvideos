import * as React from "react";

/**
 * Browser-chrome mockup. Dark product on a light page is the brand's signature —
 * wrap product screenshots and the demo loop in this.
 *
 * @startingPoint section="Display" subtitle="Browser frame for product mockups" viewport="720x460"
 */
export interface BrowserFrameProps extends React.HTMLAttributes<HTMLDivElement> {
  /** URL shown in the address pill */
  url?: string;
  /** Chrome tone: dark (default) or light */
  theme?: "light" | "dark";
}

export function BrowserFrame(props: BrowserFrameProps): JSX.Element;
