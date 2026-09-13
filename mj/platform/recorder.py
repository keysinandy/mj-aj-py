"""平台对局结构化日志(JSONL):自记为唯一真相源(正式赛赛后无处拉取)。

每场对局一个文件 local/games/<YYYYMMDD>/<令牌>_<gid>.jsonl,一行一条
记录。记录类型:
- meta      开场:gid/令牌/锦标赛/YCBK/base
- sse_frame SSE 通知帧:payload/水位/closed/去重与唤醒结果
- req       每次状态轮询:请求时游标 seq/响应耗时/状态码/尝试次数/响应摘要；
            transport 可带 state_attempts 的物理状态与分阶段耗时摘要
- state_reconcile 状态响应应用完成:logical id/评估 revision/已满足原因/
            应用边界；与 req 分开记录，避免把响应后事实伪装成发送时视图
- window_confirm 窗口权威确认过程:phase/window identity/seq/deadline/outcome
- window_lifecycle 窗口生命周期状态转换:stage/state/outcome/归因字段
- window_authorization 权威授权快照:phase/responding/deadline/时序证据
- window_terminal 窗口终止事实:terminal reason/phase/server event
- snapshot  快照原文(离线重建锚点:my_hand/公共状态/墙长)
- events    事件批原文(离线重放数据源)
- decision  决策点:phase/合法动作集/所选动作/decide 耗时/镜像摘要
- claim_miss 规则允许的吃/碰/杠未成功(策略已选未成 / 未决策即 timeout)
- action    动作提交:payload/结果(成功或错误码)/耗时/配对决策 id；
            transport 可带 action_attempts 的安全分阶段耗时摘要
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
import copy


def _json_default(value):
    if hasattr(value, "as_json"):
        return value.as_json()
    if hasattr(value, "to_json"):
        return value.to_json()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


class GameLog:
    """单场对局 JSONL;写失败降级为静默丢弃(不影响对弈)。"""

    def __init__(self, path, gid):
        self.gid = gid
        self.path = path
        self.cursor = 0            # 当前事件游标(对齐用)
        self.pending_decision = None  # 最近一次未配对 action 的决策 id
        self.last_demand = None       # 最近一次 req demand snapshot
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
                          ensure_ascii=False, default=_json_default)
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

    def sse_frame(self, gid, seq=None, closed=False, payload=None,
                  raw_line=None, connection_id=None,
                  previous_wake_seq=None, last_wake_seq=None,
                  accepted=False, wake_enqueued=False, deduplicated=False,
                  parse_error=None, received_monotonic=None):
        """记录一条 SSE data 帧，不推进本地事件 cursor。

        ``seq`` 是服务端包含式 watermark，不是本地 /state 游标。除帧本身
        的 payload/raw_line 外，额外记录 listener 是否接受该帧并入队唤醒，
        以及它是否因水位重复/倒退而被去重。日志失败不能影响对弈，和其他
        Recorder 入口保持相同的 GameLog 写入语义。
        """
        rec = {
            "type": "sse_frame",
            "gid": gid,
            "seq": seq,
            "closed": bool(closed),
            "accepted": bool(accepted),
            "wake_enqueued": bool(wake_enqueued),
            "deduplicated": bool(deduplicated),
            "connection_id": connection_id,
            "previous_wake_seq": previous_wake_seq,
            "last_wake_seq": last_wake_seq,
        }
        for key, value in (
                ("payload", payload),
                ("raw_line", raw_line),
                ("parse_error", parse_error),
                ("received_monotonic", received_monotonic)):
            if value is not None:
                rec[key] = value
        self.log_for(gid).write(rec)

    def req(self, gid, seq, status, latency_ms, attempts=None, summary=None,
            transport=None, throttle=None, request_kind=None,
            logical_request_id=None, attempt_index=None, reason=None,
            generation=None, demand=None, requested_seq=None,
            candidate_id=None, candidate_created_at=None, queued_at=None,
            admitted_at=None, successor_of=None, transport_request_id=None,
            reason_revisions=None, satisfied_reasons=None,
            evaluated_revisions=None, metric_version=None,
            metric_source=None, response_applied_at=None,
            effective_deadline=None, deadline_source=None):
        """记录一次逻辑 /state 请求；新增诊断字段均为可选，兼容旧日志。

        ``request_kind`` 只描述调度诊断用途，例如 ``WINDOW_PENG``、
        ``WINDOW_CHI``、``RESYNC`` 或 ``SSE_DELTA``。它不改变请求语义，
        省略时旧日志的字段形状保持不变。
        """
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
        if request_kind:
            rec["request_kind"] = request_kind
        for key, value in (("logical_request_id", logical_request_id),
                           ("attempt_index", attempt_index),
                           ("reason", reason), ("generation", generation),
                           ("demand", demand), ("requested_seq", requested_seq)):
            if value is not None:
                rec[key] = value
        for key, value in (
                ("candidate_id", candidate_id),
                ("candidate_created_at", candidate_created_at),
                ("queued_at", queued_at),
                ("admitted_at", admitted_at),
                ("successor_of", successor_of),
                ("transport_request_id", transport_request_id),
                ("reason_revisions", reason_revisions),
                ("satisfied_reasons", satisfied_reasons),
                ("evaluated_revisions", evaluated_revisions),
                ("metric_version", metric_version),
                ("metric_source", metric_source),
                ("response_applied_at", response_applied_at),
                ("effective_deadline", effective_deadline),
                ("deadline_source", deadline_source)):
            if value is not None:
                rec[key] = value
        if demand is not None:
            # Keep a copy because callers reuse the live StateDemand snapshot
            # while the room continues; end() may need this as a compatibility
            # fallback when no explicit terminal snapshot is available.
            log.last_demand = copy.deepcopy(demand)
        log.write(rec)

    def state_reconcile(self, gid, logical_request_id=None,
                        evaluated_revisions=None, satisfied_reasons=None,
                        response_applied_at=None, pending=None,
                        lifecycle=None, error=None):
        """Record the post-transport response-application boundary.

        ``req`` intentionally contains the frozen dispatch view.  This
        additive record carries facts known only after the mirror/authoritative
        snapshot has been applied, so request analysis can distinguish the two
        phases without rewriting or duplicating the dispatch record.
        """
        fields = {
            "type": "state_reconcile",
            "logical_request_id": logical_request_id,
            "evaluated_revisions": evaluated_revisions,
            "satisfied_reasons": satisfied_reasons,
            "response_applied_at": response_applied_at,
            "pending": pending,
            "lifecycle": lifecycle,
        }
        if error is not None:
            fields["error"] = type(error).__name__
        self.log_for(gid).write({key: value for key, value in fields.items()
                                 if value is not None})

    def window_confirm(self, gid, **fields):
        """记录一次窗口权威确认过程。

        调用方按确认阶段填充字段；常用字段为 ``phase``、``window_id``、
        ``seq``、``status``、``request_kind``、``responding_seats``、
        ``legal``、``chosen``、``deadline_at``、``reason`` 和 ``outcome``。
        字段保持开放以兼容 peng/chi 两条确认路径，值为 ``None`` 的可选
        字段不落盘。该记录不会推进事件 cursor。
        """
        rec = {"type": "window_confirm"}
        rec.update({key: value for key, value in fields.items()
                    if value is not None})
        self.log_for(gid).write(rec)

    def window_lifecycle(self, gid, stage=None, state=None, outcome=None,
                         **fields):
        """记录一个 WindowAttemptKey 的生命周期状态转换。

        这是追加式事实记录；验收脚本按 attempt key 和记录时间重建最终
        状态，因此运行时不需要持久化一个会阻断对弈的复杂状态机。
        """
        rec = {"type": "window_lifecycle"}
        for key, value in (("stage", stage), ("state", state),
                           ("outcome", outcome)):
            if value is not None:
                rec[key] = value
        rec.update({key: value for key, value in fields.items()
                    if value is not None})
        self.log_for(gid).write(rec)

    def window_authorization(self, gid, **fields):
        """记录一次 authoritative_open 的快照授权证据。"""
        rec = {"type": "window_authorization"}
        rec.update({key: value for key, value in fields.items()
                    if value is not None})
        self.log_for(gid).write(rec)

    def window_terminal(self, gid, **fields):
        """记录窗口 phase 的终止事实，不把它直接当作客户端根因。"""
        rec = {"type": "window_terminal"}
        rec.update({key: value for key, value in fields.items()
                    if value is not None})
        self.log_for(gid).write(rec)

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

    def decision(self, gid, phase, legal, action, latency_ms, digest=None,
                 window_id=None, window_attempt_key=None,
                 identity_status=None, identity_origin=None,
                 first_seen_via=None, logical_request_id=None,
                 attempt_index=None, authorization_snapshot_seq=None,
                 authorization_phase=None,
                 authorization_responding_seats=None,
                 exact_deadline_at=None,
                 decision_started_at=None, decision_finished_at=None,
                 deadline_left_at_start_ms=None,
                 deadline_left_at_finish_ms=None,
                 decision_result=None):
        """返回决策 id(action 记录据此配对)。"""
        log = self.log_for(gid)
        did = log.next_decision_id()
        log.pending_decision = did
        rec = {"type": "decision", "id": did, "seq": log.cursor,
               "phase": phase, "legal": legal, "action": action,
               "latency_ms": latency_ms}
        if digest:
            rec["digest"] = digest
        for key, value in (("window_id", window_id),
                           ("window_attempt_key", window_attempt_key),
                           ("identity_status", identity_status),
                           ("identity_origin", identity_origin),
                           ("first_seen_via", first_seen_via),
                           ("logical_request_id", logical_request_id),
                           ("attempt_index", attempt_index),
                           ("authorization_snapshot_seq",
                            authorization_snapshot_seq),
                           ("authorization_phase", authorization_phase),
                           ("authorization_responding_seats",
                            authorization_responding_seats),
                           ("exact_deadline_at", exact_deadline_at),
                           ("decision_started_at", decision_started_at),
                           ("decision_finished_at", decision_finished_at),
                           ("deadline_left_at_start_ms",
                            deadline_left_at_start_ms),
                           ("deadline_left_at_finish_ms",
                            deadline_left_at_finish_ms),
                           ("decision_result", decision_result)):
            if value is not None:
                rec[key] = value
        log.write(rec)
        return did

    def claim_miss(self, gid, phase, legal, chosen=None, reason="",
                   payload=None, status=None, code="", deadline_at=None,
                   seq=None, pending=None, window_id=None,
                   window_attempt_key=None, logical_request_id=None,
                   exact_deadline_at=None, action_posted=None,
                   decision_id=None):
        """记录规则允许的吃/碰/杠机会未成功，供离线对账归因。

        窗口关联字段（window_id/window_attempt_key/logical_request_id/
        exact_deadline_at/action_posted）只在调用方可评估时落盘；缺失时
        离线分类只能归入 unknown，不参与强证据分母。
        """
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
            rec["legal_check"] = ("source_window" if reason.startswith("window_confirm_")
                                  else "current_mirror")
        # 只读 pending_decision 与其配对，不清空：紧随其后的 action 记录
        # （POST 被拒/结果未知）仍要按同一 decision id 配对。
        did = (decision_id if decision_id is not None
               else self.log_for(gid).pending_decision)
        if did is not None and chosen is not None:
            rec["decision"] = did
        observed_at = time.time()
        rec["observed_at"] = round(observed_at, 3)
        effective_deadline = (exact_deadline_at if exact_deadline_at is not None
                              else deadline_at)
        if effective_deadline is not None:
            rec["deadline_left_ms"] = round(
                (effective_deadline - observed_at) * 1000, 1)
        for key, value in (("payload", payload), ("status", status),
                           ("code", code), ("deadline_at", deadline_at),
                           ("seq", seq), ("pending", pending),
                           ("window_id", window_id),
                           ("window_attempt_key", window_attempt_key),
                           ("logical_request_id", logical_request_id),
                           ("exact_deadline_at", exact_deadline_at),
                           ("action_posted", action_posted)):
            if value is not None and value != "":
                rec[key] = value
        self.log_for(gid).write(rec)

    def action(self, gid, phase, payload, ok, status=200, code="",
               latency_ms=None, attempts=None, started_at=None,
               started_epoch=None, deadline_at=None, message=None,
               transport=None, logical_request_id=None, attempt_index=None,
               window_id=None, window_attempt_key=None,
               identity_status=None, identity_origin=None,
               first_seen_via=None, decision_id=None,
               authorization_snapshot_seq=None,
               authorization_phase=None,
               authorization_responding_seats=None,
               authorization_age_ms=None,
               exact_deadline_at=None,
               deadline_left_at_send_ms=None,
               deadline_left_at_response_ms=None,
               post_started_at=None, post_finished_at=None,
               post_status=None, response_epoch=None,
               server_trace_id=None, outcome=None, reconciliation=None):
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
        for key, value in (("logical_request_id", logical_request_id),
                           ("attempt_index", attempt_index),
                           ("window_id", window_id),
                           ("window_attempt_key", window_attempt_key),
                           ("identity_status", identity_status),
                           ("identity_origin", identity_origin),
                           ("first_seen_via", first_seen_via),
                           ("decision", decision_id),
                           ("authorization_snapshot_seq",
                            authorization_snapshot_seq),
                           ("authorization_phase", authorization_phase),
                           ("authorization_responding_seats",
                            authorization_responding_seats),
                           ("authorization_age_ms", authorization_age_ms),
                           ("exact_deadline_at", exact_deadline_at),
                           ("deadline_left_at_send_ms",
                            deadline_left_at_send_ms),
                           ("deadline_left_at_response_ms",
                            deadline_left_at_response_ms),
                           ("post_started_at", post_started_at),
                           ("post_finished_at", post_finished_at),
                           ("post_status", post_status),
                           ("response_epoch", response_epoch),
                           ("server_trace_id", server_trace_id),
                           ("outcome", outcome),
                           ("reconciliation", reconciliation)):
            if value is not None:
                rec[key] = value
        did = log.pending_decision
        if decision_id is None and did is not None:
            rec["decision"] = did
            log.pending_decision = None
        elif decision_id is not None and did == decision_id:
            # An explicit id is authoritative for cross-window joins.  Clear
            # only the matching compatibility slot.
            log.pending_decision = None
        if (server_trace_id is None
                and (outcome is not None or post_status is not None)):
            # For a response-attributed action, absence of a protocol trace is
            # evidence and is therefore kept as JSON null rather than omitted.
            rec["server_trace_id"] = None
        log.write(rec)

    def reset(self, gid, reason):
        self.log_for(gid).write({"type": "reset", "reason": reason})

    def end(self, gid, reason, scores=None, error=None, demand=None,
            transport_status=None, window_status=None, game_status=None):
        log = self.log_for(gid)
        rec = {"type": "end", "reason": reason, "scores": scores}
        if error is not None:
            rec["error"] = error
        if demand is not None:
            rec["demand"] = demand
            rec["demand_source"] = "end"
        elif log.last_demand is not None:
            rec["demand"] = copy.deepcopy(log.last_demand)
            rec["demand_source"] = "req_fallback"
        else:
            rec["demand_source"] = "missing"
        for key, value in (("transport_status", transport_status),
                           ("window_status", window_status),
                           ("game_status", game_status)):
            if value is not None:
                rec[key] = value
        log.write(rec)
        self.close(gid)
