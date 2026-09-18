import type { TimelineEntry } from "../replay/timeline";

interface Props {
  entries: TimelineEntry[];
  currentIndex: number;
  onSelect: (index: number) => void;
}

export function Timeline({ entries, currentIndex, onSelect }: Props) {
  return (
    <ol className="timeline" data-testid="timeline">
      {entries.map((e) => (
        <li
          key={e.index}
          className={`tl-entry${e.index === currentIndex ? " tl-current" : ""}${e.gap ? " tl-gap" : ""}`}
          onClick={() => onSelect(e.index)}
          data-testid={`tl-item-${e.index}`}
        >
          {e.index}
          {e.gap ? " ⚠" : ""} {e.label}
        </li>
      ))}
    </ol>
  );
}