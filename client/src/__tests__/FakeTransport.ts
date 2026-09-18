import type { Transport, TransportEvent } from "../service/transport";

/** 可控假传输:手动触发事件,驱动连接状态机。 */
export class FakeTransport implements Transport {
  cbs: Record<TransportEvent, Array<() => void>> = {
    connecting: [],
    connected: [],
    disconnected: [],
  };
  connected = false;
  opened = 0;
  closed = 0;

  open(): void {
    this.opened += 1;
    this.emit("connecting");
  }
  close(): void {
    this.closed += 1;
    this.connected = false;
    this.emit("disconnected");
  }
  on(event: TransportEvent, cb: () => void): () => void {
    this.cbs[event].push(cb);
    return () => {
      const i = this.cbs[event].indexOf(cb);
      if (i >= 0) this.cbs[event].splice(i, 1);
    };
  }
  get isConnected(): boolean {
    return this.connected;
  }
  emit(event: TransportEvent): void {
    if (event === "connected") this.connected = true;
    else if (event === "disconnected") this.connected = false;
    this.cbs[event].forEach((f) => f());
  }
}