/**
 * 指数退避重连调度:服务崩溃(disconnected)后自动重试恢复。
 * 与具体传输解耦,纯时序逻辑,便于单测。
 */

export interface ReconnectController {
  start(): void;
  stop(): void;
  notifyStatus(status: "connected" | "disconnected"): void;
  readonly attempts: number;
}

export interface Scheduler {
  /** 返回延迟 ms;attempt 从 1 起。实现方决定退避上界。 */
  delay(attempt: number): number;
  setTimer(fn: () => void, ms: number): void;
  clearTimer(): void;
}

export class BackoffReconnect implements ReconnectController {
  attempts = 0;
  private running = false;
  private connected = false;
  private readonly onRetry: () => void;
  private readonly sch: Scheduler;

  constructor(onRetry: () => void, sch: Scheduler) {
    this.onRetry = onRetry;
    this.sch = sch;
  }

  start(): void {
    this.running = true;
  }

  stop(): void {
    this.running = false;
    this.attempts = 0;
    this.sch.clearTimer();
  }

  notifyStatus(status: "connected" | "disconnected"): void {
    if (status === "connected") {
      this.connected = true;
      this.attempts = 0;
      this.sch.clearTimer();
      return;
    }
    // disconnected
    this.connected = false;
    if (!this.running) return;
    this.scheduleRetry();
  }

  private scheduleRetry(): void {
    this.sch.clearTimer();
    this.attempts += 1;
    const ms = this.sch.delay(this.attempts);
    this.sch.setTimer(() => {
      if (this.running && !this.connected) {
        this.onRetry();
      }
    }, ms);
  }
}

/** 默认退避:250ms 起 ×2 到顶 5s。 */
export function defaultScheduler(
  sandbox: { setTimeout: typeof setTimeout; clearTimeout: typeof clearTimeout },
): Scheduler {
  let handle: ReturnType<typeof setTimeout> | undefined;
  return {
    delay(attempt: number): number {
      return Math.min(250 * 2 ** (attempt - 1), 5000);
    },
    setTimer(fn, ms): void {
      handle = sandbox.setTimeout(fn, ms);
    },
    clearTimer(): void {
      if (handle !== undefined) {
        sandbox.clearTimeout(handle);
        handle = undefined;
      }
    },
  };
}