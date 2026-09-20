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
          className={`tl-entry${e.index === currentIndex ? " tl-current" : ""}${e.gap ? " tl-gap" : ""}${e.isMine ? " tl-mine" : ""}`}
          onClick={() => onSelect(e.index)}
          data-testid={`tl-item-${e.index}`}
          data-mine={e.isMine}
        >
          <span className="tl-step">{e.index}</span>
          {e.seqNo !== null && <span className="tl-seq">seq {e.seqNo}</span>}
          {e.isMine && <span className="tl-mine-badge" aria-label="我的操作">我</span>}
          <span className="tl-label">{e.gap ? " ⚠" : ""} {e.label}</span>
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
