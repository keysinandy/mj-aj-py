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
          <span className="tl-step">{e.index}</span>
          {e.seqNo !== null && <span className="tl-seq">seq {e.seqNo}</span>}
          {e.gap ? " ⚠" : ""} {e.label}
          {e.diagnosticCount > 0 && (
            <span className={`tl-diagnostic tl-${e.diagnosticSeverity ?? "warn"}`}>
              {e.diagnosticSeverity === "error" ? " 🔴" : " ⚠"} {e.diagnosticCount}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
