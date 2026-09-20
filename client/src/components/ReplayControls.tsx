import { useEffect, useState } from "react";

interface Props {
  index: number;
  total: number;
  onStepBack: () => void;
  onStepForward: () => void;
  onJump: (index: number) => void;
  onFirst?: () => void;
  onLast?: () => void;
  playing?: boolean;
  onTogglePlaying?: () => void;
  seqNo?: number | null;
  onJumpSeqNo?: (seqNo: number) => void;
}

export function ReplayControls({
  index,
  total,
  onStepBack,
  onStepForward,
  onJump,
  onFirst,
  onLast,
  playing = false,
  onTogglePlaying,
  seqNo,
  onJumpSeqNo,
}: Props) {
  const [seqInput, setSeqInput] = useState(seqNo === null || seqNo === undefined ? "" : String(seqNo));
  useEffect(() => {
    setSeqInput(seqNo === null || seqNo === undefined ? "" : String(seqNo));
  }, [seqNo]);

  const disabled = total <= 0;
  function submitSeq(): void {
    if (!onJumpSeqNo) return;
    const value = Number(seqInput);
    if (Number.isFinite(value)) onJumpSeqNo(value);
  }

  return (
    <div className="replay-controls">
      <button onClick={onFirst} disabled={disabled || index <= 0} data-testid="btn-first">
        |&lt;
      </button>
      <button onClick={onStepBack} disabled={disabled || index <= 0} data-testid="btn-back">
        上一步
      </button>
      {onTogglePlaying && (
        <button onClick={onTogglePlaying} disabled={disabled || total < 2} data-testid="btn-play">
          {playing ? "暂停" : "播放"}
        </button>
      )}
      <span data-testid="index">{disabled ? "0/0" : `${index + 1}/${total}`}</span>
      <button onClick={onStepForward} disabled={disabled || index >= total - 1} data-testid="btn-next">
        下一步
      </button>
      <button onClick={onLast} disabled={disabled || index >= total - 1} data-testid="btn-last">
        &gt;|
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(0, total - 1)}
        value={disabled ? 0 : index}
        disabled={disabled}
        onChange={(e) => onJump(Number(e.target.value))}
        data-testid="slider"
        style={{ flex: 1 }}
      />
      {onJumpSeqNo && (
        <label className="seq-jump">
          seqNo
          <input
            type="number"
            value={seqInput}
            onChange={(event) => setSeqInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") submitSeq();
            }}
            disabled={disabled}
            data-testid="seq-input"
          />
          <button onClick={submitSeq} disabled={disabled || seqInput === ""} data-testid="btn-seq-jump">
            跳转
          </button>
        </label>
      )}
    </div>
  );
}
