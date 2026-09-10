"""平台对局结构化日志(JSONL):自记为唯一真相源(正式赛赛后无处拉取)。

每场对局一个文件 local/games/<YYYYMMDD>/<令牌>_<gid>.jsonl,一行一条
记录。记录类型:
- meta      开场:gid/令牌/锦标赛/YCBK/base
- req       每次状态轮询:请求时游标 seq/响应耗时/状态码/尝试次数/响应摘要
- snapshot  快照原文(离线重建锚点:my_hand/公共状态/墙长)
- events    事件批原文(离线重放数据源)
- decision  决策点:phase/合法动作集/所选动作/decide 耗时/镜像摘要
- claim_miss 规则允许的吃/碰/杠未成功(策略已选未成 / 未决策即 timeout)
- action    动作提交:payload/结果(成功或错误码)/耗时/配对决策 id
- reset     镜像失步重建原因
- end       收场:终局积分/原因

线程模型:一个 BotClient 打 M 场并发,每场一个 GameLog(各自工作线程
独占写;GameLog 内部再加锁兜底)。cursor 随 req/events/snapshot 推进,
decision 记录取当前 cursor 对齐事件 seq;decision→action 用 id 配对
(同 gid 同线程顺序写,无竞态)。

时间口径:ts 为 epoch 秒,耗时统一 monotonic 差值记毫秒(不受时钟
跳变影响)。
"""

import json
import os
import threading
import time


class GameLog:
    """单场对局 JSONL;写失败降级为静默丢弃(不影响对弈)。"""

    def __init__(self, path, gid):
        self.gid = gid
        self.path = path
        self.cursor = 0            # 当前事件游标(对齐用)
        self.pending_decision = None  # 最近一次未配对 action 的决策 id
        self._lock = threading.Lock()
        self._n_decisions = 0
        self._closed = False
        self._f = open(path, "a", encoding="utf-8")

    def next_decision_id(self):
        with self._lock:
            self._n_decisions += 1
            return self._n_decisions

    def write(self, rec):
        """追加一条记录;关闭后或写失败静默丢弃(日志永不打断对弈)。"""
        if self._closed:
            return
        line = json.dumps({"ts": round(time.time(), 3), **rec},
                          ensure_ascii=False)
        try:
            with self._lock:
                if self._closed:
                    return
                self._f.write(line + "\n")
                self._f.flush()
        except OSError:
            pass

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._f.close()
                except OSError:
                    pass


class Recorder:
    """结构化对局日志聚合入口(线程安全;一场对局一个 GameLog)。

    meta() 首次调用注册文件名;之后任意记录按 gid 取(未注册时以
    default_name 兜底创建)。
    """

    def __init__(self, root="local/games", default_name="bot"):
        self.root = root
        self.default_name = default_name
        self._logs = {}
        self._lock = threading.Lock()

    # ---------- 文件管理 ----------

    def log_for(self, gid, name=None):
        with self._lock:
            log = self._logs.get(gid)
            if log is None:
                day = time.strftime("%Y%m%d")
                d = os.path.join(self.root, day)
                os.makedirs(d, exist_ok=True)
                log = GameLog(
                    os.path.join(d, f"{name or self.default_name}_{gid}.jsonl"),
                    gid)
                self._logs[gid] = log
            return log

    def close(self, gid):
        with self._lock:
            log = self._logs.pop(gid, None)
        if log is not None:
            log.close()

    def close_all(self):
        with self._lock:
            logs = list(self._logs.values())
            self._logs.clear()
        for log in logs:
            log.close()

    # ---------- 记录类型 ----------

    def meta(self, gid, name, tid=None, you_cai_bi_kao=False, base=1,
             mode=None):
        """mode 标记对局来源(match=自由对战;测试房/正式赛缺省不写,
        log2data 按 mode 过滤时缺省视作非 match)。"""
        rec = {"type": "meta", "gid": gid, "name": name, "tid": tid,
               "you_cai_bi_kao": bool(you_cai_bi_kao), "base": base}
        if mode:
            rec["mode"] = mode
        self.log_for(gid, name).write(rec)

    def req(self, gid, seq, status, latency_ms, attempts=None, summary=None,
            transport=None, throttle=None):
        """记录一次逻辑 /state 请求；新增诊断字段均为可选，兼容旧日志。"""
        log = self.log_for(gid)
        log.cursor = seq
        rec = {"type": "req", "seq": seq, "status": status,
               "latency_ms": latency_ms, "attempts": attempts}
        if summary:
            rec["res"] = summary
        if transport:
            rec["transport"] = transport
        if throttle:
            rec["throttle"] = throttle
        log.write(rec)

    def snapshot(self, gid, seq, snap):
        log = self.log_for(gid)
        if seq is not None:
            log.cursor = seq
        log.write({"type": "snapshot", "seq": seq, "snap": snap})

    def events(self, gid, seq_to, events):
        log = self.log_for(gid)
        if seq_to is not None:
            log.cursor = seq_to
        # This is the local epoch at which the response was consumed, not the
        # server event timestamp. Keep it separate for window diagnostics.
        log.write({"type": "events", "seq_to": seq_to,
                   "received_epoch": time.time(), "events": events})

    def decision(self, gid, phase, legal, action, latency_ms, digest=None):
        """返回决策 id(action 记录据此配对)。"""
        log = self.log_for(gid)
        did = log.next_decision_id()
        log.pending_decision = did
        rec = {"type": "decision", "id": did, "seq": log.cursor,
               "phase": phase, "legal": legal, "action": action,
               "latency_ms": latency_ms}
        if digest:
            rec["digest"] = digest
        log.write(rec)
        return did

    def claim_miss(self, gid, phase, legal, chosen=None, reason="",
                   payload=None, status=None, code="", deadline_at=None,
                   seq=None, pending=None):
        """记录规则允许的吃/碰/杠机会未成功，供离线对账归因。"""
        rec = {"type": "claim_miss", "phase": phase,
               "legal": list(legal), "chosen": chosen,
               "client_decision": chosen is not None, "reason": reason}
        if chosen is None:
            rec["chosen_legal"] = None
            rec["legal_check"] = "not_decided"
        elif chosen not in legal:
            rec["chosen_legal"] = False
            rec["legal_check"] = "stale_or_mismatched"
        else:
            rec["chosen_legal"] = True
            rec["legal_check"] = "current_mirror"
        # 只读 pending_decision 与其配对，不清空：紧随其后的 action 记录
        # （POST 被拒/结果未知）仍要按同一 decision id 配对。
        did = self.log_for(gid).pending_decision
        if did is not None:
            rec["decision"] = did
        for key, value in (("payload", payload), ("status", status),
                           ("code", code), ("deadline_at", deadline_at),
                           ("seq", seq), ("pending", pending)):
            if value is not None and value != "":
                rec[key] = value
        self.log_for(gid).write(rec)

    def action(self, gid, phase, payload, ok, status=200, code="",
               latency_ms=None, attempts=None, started_at=None,
               started_epoch=None, deadline_at=None, message=None,
               transport=None):
        log = self.log_for(gid)
        rec = {"type": "action", "phase": phase, "payload": payload,
               "ok": ok, "status": status, "code": code,
               "latency_ms": latency_ms, "attempts": attempts}
        # ts is response completion/log time, not the time the POST was sent.
        # Keep both ends so slow responses cannot be mistaken for late sends.
        for key, value in (("started_at", started_at),
                           ("started_epoch", started_epoch),
                           ("deadline_at", deadline_at),
                           ("message", message), ("transport", transport)):
            if value is not None:
                rec[key] = value
        did = log.pending_decision
        if did is not None:
            rec["decision"] = did
            log.pending_decision = None
        log.write(rec)

    def reset(self, gid, reason):
        self.log_for(gid).write({"type": "reset", "reason": reason})

    def end(self, gid, reason, scores=None, error=None):
        rec = {"type": "end", "reason": reason, "scores": scores}
        if error is not None:
            rec["error"] = error
        self.log_for(gid).write(rec)
        self.close(gid)
