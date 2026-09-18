interface Props {
  index: number;
  total: number;
  onStepBack: () => void;
  onStepForward: () => void;
  onJump: (index: number) => void;
}

export function ReplayControls({ index, total, onStepBack, onStepForward, onJump }: Props) {
  const disabled = total <= 0;
  return (
    <div className="replay-controls">
      <button onClick={onStepBack} disabled={disabled || index <= 0} data-testid="btn-back">
        上一步
      </button>
      <span data-testid="index">{disabled ? "0/0" : `${index + 1}/${total}`}</span>
      <button onClick={onStepForward} disabled={disabled || index >= total - 1} data-testid="btn-next">
        下一步
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
    </div>
  );
}