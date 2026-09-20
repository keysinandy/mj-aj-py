import type { ReplayFrame } from "./frame";
import type { ReplaySession, ReplayStep } from "./session";

export const DEFAULT_CHECKPOINT_INTERVAL = 32;

/**
 * 统一回放引擎。
 *
 * 后端已经按来源完成一次合法的状态重建,每个 ReplayStep 带有该时刻的
 * state。引擎只负责按步骤消费这些状态并缓存 checkpoint；未来增加纯事件
 * reducer 时只需替换 applyStep, UI 与导航语义不变。
 */
export class ReplayEngine {
  readonly session: ReplaySession;
  readonly checkpointInterval: number;
  private readonly checkpoints = new Map<number, ReplayFrame>();

  constructor(
    session: ReplaySession,
    checkpointInterval = DEFAULT_CHECKPOINT_INTERVAL,
  ) {
    this.session = session;
    this.checkpointInterval = Math.max(1, Math.floor(checkpointInterval));
    if (session.steps[0]) this.checkpoints.set(0, session.steps[0].state);
  }

  get total(): number {
    return this.session.steps.length;
  }

  clampIndex(index: number): number {
    if (!this.total) return 0;
    return Math.max(0, Math.min(this.total - 1, Math.round(index)));
  }

  stepAt(index: number): ReplayStep | null {
    return this.session.steps[this.clampIndex(index)] ?? null;
  }

  stateAt(index: number): ReplayFrame | null {
    if (!this.total) return null;
    const target = this.clampIndex(index);
    const exact = this.checkpoints.get(target);
    if (exact) return exact;

    let checkpoint = 0;
    for (const key of this.checkpoints.keys()) {
      if (key <= target && key >= checkpoint) checkpoint = key;
    }
    let state = this.checkpoints.get(checkpoint) ?? this.session.steps[0].state;
    for (let i = checkpoint + 1; i <= target; i += 1) {
      state = this.applyStep(state, this.session.steps[i]);
      if (i % this.checkpointInterval === 0 || i === target) {
        this.checkpoints.set(i, state);
      }
    }
    return state;
  }

  seqNoAt(index: number): number | null {
    return this.stepAt(index)?.seqNo ?? null;
  }

  indexForSeqNo(seqNo: number): number {
    if (!this.total) return 0;
    let floor = -1;
    let floorSeq = Number.NEGATIVE_INFINITY;
    let ceiling = -1;
    let ceilingSeq = Number.POSITIVE_INFINITY;
    this.session.steps.forEach((step, index) => {
      if (step.seqNo === null) return;
      if (step.seqNo === seqNo) {
        floor = index;
        floorSeq = step.seqNo;
        ceiling = index;
        ceilingSeq = step.seqNo;
        return;
      }
      if (step.seqNo < seqNo && step.seqNo > floorSeq) {
        floor = index;
        floorSeq = step.seqNo;
      }
      if (step.seqNo > seqNo && step.seqNo < ceilingSeq) {
        ceiling = index;
        ceilingSeq = step.seqNo;
      }
    });
    // 缺口时只落在请求序号之前的已知状态,避免把未来的吃碰杠提前显示。
    return floor >= 0 ? floor : Math.max(0, ceiling);
  }

  /** 单步状态边界。不能读取 target 之后的步骤。 */
  applyStep(_previous: ReplayFrame, step: ReplayStep): ReplayFrame {
    return step.state;
  }
}
