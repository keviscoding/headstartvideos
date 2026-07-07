import * as React from "react";

/** Selectable voice row with a play button. One of six curated voices; best pre-selected. */
export interface VoiceOptionProps extends React.HTMLAttributes<HTMLDivElement> {
  name?: string;
  /** Short descriptor, e.g. "Warm US female" */
  descriptor?: string;
  selected?: boolean;
  playing?: boolean;
  /** Show the "Best pick" tag */
  recommended?: boolean;
  onSelect?: () => void;
  onPlay?: () => void;
  theme?: "light" | "dark";
}

export function VoiceOption(props: VoiceOptionProps): JSX.Element;
