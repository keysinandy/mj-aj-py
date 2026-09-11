"""单令牌 bot 生命周期:发现房间 → register/ready → 状态循环 → 打活跃场。

实测协议要点(2026-09-08 探针):
- 测试房 M=10 → 同 4 人 10 场并发:run() 为每个活跃场派独立工作线程;
- 快照(seq=0/gap)含全量公共状态(四家牌河/副露/手牌数/wall_remaining/
  last_discard/dealer),Mirror.apply_snapshot 全量重建自愈;
- 增量事件不带快照;决策事件驱动:自家 tile_drawn/吃碰 → 弃牌决策,
  他家 tile_discarded → 反应窗(碰窗先于吃窗,均固定走满 1s)。

限速预算:state 16/s/用户,10 场并发 × (长轮询 ~1.3/s + 少量动作) 需
控制在 ~1.5/s/场——无触发批次后 sleep,动作后立即轮询。
"""

import json
import inspect
import math
import queue
import random
import threading
import time

from .api import ApiError
from .mirror import Mirror, MirrorInconsistent
from .actions import action_to_payload
from .proto import parse_event, tidx
from .state_demand import (
    PENDING,
    RESYNC,
    SATISFIED,
    SSE_DELTA,
    TERMINAL as DEMAND_TERMINAL,
    WINDOW_CONFIRM,
    StateDemand,
    WindowAttemptKey,
    WindowId,
    window_attempt_tuple,
)

TERMINAL = ("finished", "closed", "void")
WINDOW_SEC = 1.0  # 碰/吃窗口固定走满时长(提交吃牌须等碰窗结束)
DISCARD_SEC = 3.0
DEADLINE_MARGIN = 0.12  # 为模型串行决策与动作提交预留的本地安全余量
SUBMIT_EPS = 0.05  # 窗口守卫余量:仅剩此余量时物理上来不及提交才放弃
LAZY_POLL_WAIT = 1.2  # 懒轮询:预测无关事件的最长推迟(吃窗 T+2 内须追平)
EAGER_CHI_LEAD = 0.25  # 吃窗快照提前量:临近开窗才抓,避免长时间空占 EDF
EAGER_CHI_GAP = 0.12   # 同一吃窗两次快照抓取的最小间隔(限速 ~8/s)
EAGER_CHI_MAX = 8      # 同一吃窗最多抓取次数(无截止/相位不推进时的兜底界)


class _ActionResync(Exception):
    """动作结果不能继续沿用本地镜像,必须 seq=0 重锚。

    409 是服务端对窗口/阶段的最终裁定;网络错误则无法知道 POST 是否
    已被服务端接受。两种情况都不能由外层的通用决策异常处理,否则会被
    计入 decide_errors 或盲目重试动作。
    """

    def __init__(self, reason, status=None, uncertain=False):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.uncertain = uncertain


class _WindowConfirm(Exception):
    """碰窗临界时请求一次权威 seq=0 快照。

    这条路径只为确认窗口事实，不能复用确认前的动作，也不能把秒级
    弃牌时间戳当作动作授权截止。``schedule_deadline`` 仅传给共享
    /state 调度器排序；最终是否还能提交只由确认快照的精确 deadline
    和新的合法集决定。
    """

    def __init__(self, *, phase, pending, round_no, legal, source_seq=None,
                 source_ts=None, source_watermark=None, chosen=None,
                 schedule_deadline=None,
                 reason="quantized_deadline"):
        super().__init__(reason)
        self.phase = phase
        self.pending = tuple(pending) if pending is not None else None
        self.round_no = round_no
        self.legal = tuple(legal or ())
        self.source_seq = source_seq
        # Diagnostic cursor only; this is never used as WindowId identity.
        self.source_watermark = source_watermark
        self.source_ts = source_ts
        self.chosen = chosen
        self.schedule_deadline = schedule_deadline
        self.reason = reason


def _ms(t0):
    """monotonic 差值 → 毫秒(不受时钟跳变影响)。"""
    return round((time.monotonic() - t0) * 1000.0, 1)


class BotClient:
    """一个令牌一个实例;工作线程并发打 M 场(decide 调用串行加锁)。"""

    def __init__(self, api, name, decide, log=None, window_wait=WINDOW_SEC,
                 idle_sleep=0.35, recorder=None, mode=None,
                 match_retry_wait=10.0, use_notify=False,
                 notify_fallback_wait=15.0, notify_retry_wait=0.5,
                 long_poll=False):
        self.api = api
        self.name = name
        self.decide = decide
        self._log = log or (lambda msg: None)
        self.window_wait = window_wait
        self.idle_sleep = idle_sleep
        self.recorder = recorder
        self.mode = mode        # 对局来源标记(run_match 设 "match")
        self.match_retry_wait = match_retry_wait  # match 瞬态错误退避(测试注入 0)
        self.use_notify = use_notify          # play_game 挂 /notify SSE(v12)
        self.notify_fallback_wait = notify_fallback_wait  # SSE 在航时兜底轮询间隔
        self.notify_retry_wait = notify_retry_wait  # SSE 断流重连初始退避(测试注入)
        # 长轮询模式(v11 语义,2026-09-09 活体实验确认):空闲批次不再
        # 等待直接再发 /state——服务端挂起至事件刷新(~0.5s 粒度)后带
        # 事件返回,观测迟到硬上界 ~0.6s 无尾部;请求率=事件簇率(~1/s/
        # 场次),首次低于 12.5/s 限速与 16/s 服务端墙。SSE 在场会禁用
        # 挂起(3 房日志 0 次 pending),故本模式不兼容 use_notify
        self.long_poll = long_poll and not use_notify
        self._tid = None
        self.you_cai_bi_kao = False
        self.base = 1
        self._decide_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._done_games = set()
        self._game_fails = {}  # gid → 连续异常次数(超限放弃重派)
        self.stats = {
            "games": 0, "actions": 0, "hu": 0, "err409": 0, "gaps": 0,
            "auto_played": 0, "mirror_resets": 0, "scores": [],
            "rooms": 0, "hu_failed": 0, "decide_errors": 0,
            "throttle_waits": 0, "throttle_wait_ms": 0.0,
            "throttle_wait_ms_max": 0.0, "deadline_missed": 0,
            # 诊断计数彼此独立。auto_played 保留旧版本兼容口径；验收时
            # 应优先看下面的客户端放弃、响应 409、未知 POST 和服务端
            # timeout 计数，避免把推断的代打与平台实际事件混在一起。
            "client_deadline_abandons": 0,
            "client_state_abandons": 0,
            "response_409": 0,
            "post_uncertain": 0,
            "timeout_discard": 0,
            "timeout_response": 0,
            "my_timeout_discard": 0,
            "my_timeout_peng": 0,
            "my_timeout_chi": 0,
            "other_timeout_discard": 0,
            "other_timeout_response": 0,
            "platform_forced_discard": 0,
            "no_legal_response": 0,
            "stale_trigger_cancelled": 0,
            # 碰窗临界确认与最终结果分开计数。确认本身不是动作重锚，
            # 也不代表提交成功；action/response_409/post_uncertain 仍
            # 维持原有口径。
            "window_confirm_requests": 0,
            "window_confirm_open": 0,
            "window_confirm_closed": 0,
            "window_confirm_stale": 0,
            "window_confirm_unconfirmed": 0,
            "window_confirm_miss": 0,
            # /state 传输诊断在收到响应后立即累计，避免后续动作覆盖
            # Api TLS 上下文。
            "state_attempts": 0,
            "state_retry_429": 0,
            "logical_demands": 0,
            "coalesced_demands": 0,
            "physical_state_requests": 0,
            "suppressed_duplicates": 0,
            "coalescing_ratio": None,
        }

    # ---------- 生命周期(监督线程) ----------

    def run(self, max_games=None, stop=None):
        me = self.api.me()
        tid = me.get("tournament_id") or ""
        if not tid:
            raise RuntimeError("令牌未绑定锦标赛(报名/测试房令牌)")
        self._tid = tid
        try:
            cfg = (self.api.rules().get("config")) or {}
            self.you_cai_bi_kao = bool(cfg.get("YouCaiBiKao"))
            self.base = cfg.get("BaseScore", 1)
            self._log(f"房间 {tid} config: YCBK={self.you_cai_bi_kao} "
                      f"base={self.base}")
        except ApiError as e:
            self._log(f"rules 拉取失败: {e}")
        self._enter(tid)
        workers = {}
        while not (stop is not None and stop.is_set()):
            with self._stats_lock:
                games = self.stats["games"]
            if max_games is not None and games >= max_games:
                break
            try:
                t = self.api.tournament(tid)
            except ApiError as e:
                self._log(f"tournament 查询失败: {e}")
                time.sleep(2)
                continue
            status = t.get("status")
            if status in TERMINAL:
                # 测试房跨轮复用:finished 后再 ready 开下一轮
                if max_games is not None and games < max_games:
                    self._enter(tid)
                    time.sleep(0.5)
                    continue
                break
            if status in ("registering", "stage_open"):
                if status == "stage_open" and not t.get("qualified"):
                    self._log("未获得本阶段资格,退出")
                    break
                self._enter(tid)
                time.sleep(1)
                continue
            if status == "stage_done":
                time.sleep(1)
                continue
            # running:为新出现的活跃场派工作线程(M 场并发)
            for gid in self._my_active(t):
                if gid in self._done_games:
                    continue
                th = workers.get(gid)
                if th is None or not th.is_alive():
                    th = threading.Thread(
                        target=self._play_game_safe, args=(gid,),
                        name=f"{self.name}:{gid}", daemon=True)
                    workers[gid] = th
                    th.start()
            time.sleep(0.5)
        for th in workers.values():
            th.join(timeout=5)
        if self.recorder is not None:
            self.recorder.close_all()
        return dict(self.stats)

    # ---------- 自由对战(自动匹配房,run_match) ----------

    def run_match(self, max_games=None, stop=None, room_close_wait=65.0):
        """自由对战挂机循环:/api/match 入席 → 打完整房 → 等关停 → 再战。

        max_games 以整房为退出粒度:跨过 N 后仍打完当前房再退(不中途
        弃房——房内剩余场次会因离线被服务端代打,污染他人对局)。auto
        房 finished 后 ~60s 宽限才关停释放并发额度(v15:每房自
        registering 占 10/16 格),故等 room_close_wait 再 re-match;
        在途重调 /api/match 幂等返原房,崩溃重启天然续房(v24)。
        """
        while not (stop is not None and stop.is_set()):
            with self._stats_lock:
                games = self.stats["games"]
            if max_games is not None and games >= max_games:
                break
            tid, cfg = self._match_seat(stop)
            if tid is None:
                break
            self._tid = tid
            self.you_cai_bi_kao = bool(cfg.get("YouCaiBiKao"))
            self.base = cfg.get("BaseScore", 1)
            with self._stats_lock:
                self.stats["rooms"] += 1
            self._done_games.clear()  # 新房新场次(旧房 gids 不会再活跃)
            self._game_fails.clear()
            self._log(f"入席 auto 房 {tid}: YCBK={self.you_cai_bi_kao} "
                      f"base={self.base}")
            self._play_room(tid, stop)
            if self._sleep_stop(room_close_wait, stop):
                break
        if self.recorder is not None:
            self.recorder.close_all()
        return dict(self.stats)

    def _match_seat(self, stop):
        """调 /api/match 直到入席;返回 (room_id, config) 或 (None, None)。

        403 PORTAL_BINDING_REQUIRED(令牌非门户绑定)/ 401 / scoped
        令牌 400 TOKEN_NOT_SCOPED 为永久错误直接抛出;MATCH_BUSY/
        MATCH_LIMIT_REACHED/网络抖动按 10s 退避重试(≥ /api/match
        10/min 限速节奏)。
        """
        while not (stop is not None and stop.is_set()):
            try:
                res = self.api.match()
            except ApiError as e:
                permanent = (e.status in (401, 403)
                             or (e.status == 400
                                 and e.code == "TOKEN_NOT_SCOPED"))
                if permanent:
                    raise
                self._log(f"match 未入席(HTTP {e.status} {e.code}),"
                          f"10s 后重试")
                if self._sleep_stop(self.match_retry_wait, stop):
                    return None, None
                continue
            except OSError:
                self._log("match 网络异常,10s 后重试")
                if self._sleep_stop(self.match_retry_wait, stop):
                    return None, None
                continue
            tid = res.get("room_id")
            if not tid:
                self._log(f"match 响应缺 room_id: {res}")
                if self._sleep_stop(self.match_retry_wait, stop):
                    return None, None
                continue
            return tid, res.get("config") or {}
        return None, None

    def _play_room(self, tid, stop):
        """单 auto 房监督:等满员 → 为新活跃场派工作线程 → 终态收官。

        与 run() 的锦标赛监督分离:auto 房无 register/ready(直连 409
        AUTO_MATCH_ONLY),registering = 等其他人入席满 4;finished/
        closed/void 或房间 404(关停)= 本房结束,循环回去 re-match。
        """
        workers = {}
        idle = 0.0
        while not (stop is not None and stop.is_set()):
            try:
                t = self.api.tournament(tid)
            except ApiError as e:
                if e.status == 404:
                    self._log(f"auto 房 {tid} 已关停(正常生命周期)")
                else:
                    self._log(f"tournament 查询失败: {e}")
                break
            except OSError as e:
                # 网络瞬断(实测 SSL EOF):退避重试,不弃房——弃房会使
                # 全部在途场次被服务端代打
                self._log(f"tournament 网络瞬断({e}),3s 重试")
                time.sleep(3)
                continue
            status = t.get("status")
            if status in TERMINAL:
                break
            if status == "running":
                for gid in self._my_active(t):
                    if gid in self._done_games:
                        continue
                    th = workers.get(gid)
                    if th is None or not th.is_alive():
                        th = threading.Thread(
                            target=self._play_game_safe, args=(gid,),
                            name=f"{self.name}:{gid}", daemon=True)
                        workers[gid] = th
                        th.start()
                idle = 0.0
            else:
                # registering:等其他人入席满 4(空转期无 SSE 可挂,轮询)
                idle += 0.5
                if idle >= 30:
                    self._log(f"auto 房 {tid} 等待满员(status={status})…")
                    idle = 0.0
            time.sleep(0.5)
        # join 宽限须 ≥ 一个长轮询周期(35s 超时):太短会把仍在收尾的
        # 工作线程丢下,场次计数滞后 → run_match 误判未达标多开新房
        for th in workers.values():
            th.join(timeout=45)

    @staticmethod
    def _sleep_stop(sec, stop):
        """可中断 sleep(0.5s 粒度);返回 True 表示 stop 已置位。"""
        end = time.monotonic() + sec
        while not (stop is not None and stop.is_set()):
            remain = end - time.monotonic()
            if remain <= 0:
                return False
            time.sleep(min(remain, 0.5))
        return True

    def _wait_wake(self, wake, sse, max_wait=None):
        """无触发批次时的等待:SSE 在航等帧(长兜底超时),断连期间退回
        idle_sleep 轮询节奏——帧只作唤醒,不当游标(游标纪律见 v12)。
        max_wait 给定时封顶等待(吃窗截止)。返回是否被唤醒( False=
        超时,懒轮询据此区分「有积压事件」与「纯空闲」)。"""
        if wake is None:
            if max_wait is None:
                time.sleep(self.idle_sleep)
            elif self.idle_sleep:
                time.sleep(min(self.idle_sleep, max_wait))
            else:
                time.sleep(max_wait)  # idle_sleep=0(测试):睡满截止不热轮询
            return False
        timeout = self.notify_fallback_wait if sse["alive"] \
            else self.idle_sleep
        if max_wait is not None:
            timeout = min(timeout, max_wait)
        try:
            wake.get(timeout=timeout)
        except queue.Empty:
            return False
        try:
            # 一阵 SSE 帧只需拉一次状态，消费端合并余下 wake，避免多个
            # 同批帧被连续转换为 /state 请求。
            while True:
                wake.get_nowait()
        except queue.Empty:
            return True

    def _auto_played(self, gid, reason, category=None):
        """记录代打，同时保留兼容总数与可归因分类。"""
        self._log(f"我方{reason},本回合交服务端代打")
        with self._stats_lock:
            self.stats["auto_played"] += 1
            if category and category in self.stats:
                self.stats[category] += 1

    def _stale_cancelled(self, gid, reason):
        """陈旧触发被新事实取消，不等同于服务端已代打。"""
        with self._stats_lock:
            self.stats["stale_trigger_cancelled"] += 1
        self._log(f"陈旧触发作废:{reason}")

    def _deadline_abandon(self, gid, phase, reason, legal=None, chosen=None,
                          mirror=None, deadline=None):
        """本地根据截止时间放弃提交。

        这条路径不能冒充已经观测到的服务端 timeout，也不递增
        auto_played，避免随后收到 timeout 时重复计数。
        """
        self._log(f"我方{reason}({phase}),本地放弃提交，等待服务端状态")
        if legal:
            self._claim_miss(gid, phase, legal, chosen=chosen, reason=reason,
                             mirror=mirror, deadline=deadline)
        with self._stats_lock:
            self.stats["client_deadline_abandons"] += 1

    def _claim_miss(self, gid, phase, legal, chosen=None, reason="",
                    payload=None, status=None, code="", deadline=None,
                    mirror=None, seq=None, pending=None):
        """记录规则允许、但客户端未成功完成的吃/碰/杠机会。"""
        # 已完成策略决策时 chosen 为非 pass；服务端 timeout 也记录“规则有
        # 合法动作但策略尚未完成决策”的窗口，供区分两类损失。
        undecided_timeout = (chosen is None
                             and (reason.startswith("server_timeout_")
                                  or reason.startswith("window_confirm_")))
        if (phase not in ("response_peng", "response_chi")
                or not legal or (chosen is None and not undecided_timeout)
                or chosen == -1):
            return
        deadline_at = None
        if deadline is not None:
            deadline_at = time.time() + deadline - time.monotonic()
        if self.recorder is not None:
            self.recorder.claim_miss(
                gid, phase, sorted(legal), chosen=chosen, reason=reason,
                payload=payload, status=status, code=code,
                deadline_at=deadline_at, seq=seq,
                pending=(pending if pending is not None else
                         (None if mirror is None else mirror.pending)))
        self._log(f"规则可{phase}但未成功: legal={sorted(legal)} "
                  f"chosen={chosen} reason={reason}")

    def _record_window_confirm(self, gid, confirm, outcome, *, reason=None,
                               snap=None, seq=None, legal=None):
        """写碰窗确认诊断；Recorder 尚未升级时保持 fake 兼容。

        ``window_confirm`` 的字段契约：
        ``phase``, ``outcome`` (requested/open/closed/stale/unconfirmed/no_legal),
        ``reason``, ``pending`` (owner,tile), ``round_no``, ``legal``、
        ``source_seq``、``seq``、``estimated_deadline_at``、
        ``exact_deadline_at``。这些字段只描述确认过程，不描述动作 POST。
        """
        if confirm is None:
            return
        phase = confirm.phase
        logical_confirm_id = getattr(confirm, "logical_request_id", None)
        if logical_confirm_id is None:
            logical_confirm_id = (
                f"window-confirm:{gid}:{confirm.round_no}:"
                f"{repr(confirm.pending)}:{phase}")
            confirm.logical_request_id = logical_confirm_id
        confirm_attempt_index = getattr(confirm, "attempt_index", 0)
        if outcome == "requested":
            confirm_attempt_index += 1
            confirm.attempt_index = confirm_attempt_index
        window_id = WindowId(
            game_id=gid,
            round_id=confirm.round_no,
            discard_owner=(confirm.pending[0]
                           if confirm.pending is not None else None),
            source_discard_seq=confirm.source_seq,
            tile=(confirm.pending[1]
                  if confirm.pending is not None else None),
            identity_status=("authoritative"
                             if confirm.source_seq is not None
                             else "legacy_unresolved"),
            fallback=(None if confirm.source_seq is not None
                      else ("confirm", confirm.round_no, confirm.pending)),
        )
        fields = {
            "phase": phase,
            "outcome": outcome,
            "reason": reason or confirm.reason,
            "pending": (list(confirm.pending)
                         if confirm.pending is not None else None),
            "round_no": confirm.round_no,
            "legal": sorted(legal if legal is not None else confirm.legal),
            "source_seq": confirm.source_seq,
            "window_id": window_id.as_json(),
            "window_attempt_key": WindowAttemptKey(window_id, phase).as_json(),
            "identity_status": window_id.identity_status,
            "logical_request_id": logical_confirm_id,
            "attempt_index": confirm_attempt_index or None,
            "generation": getattr(confirm, "generation", None),
            "source_watermark": confirm.source_watermark,
            "seq": seq,
            "estimated_deadline_at": self._epoch_from_mono(
                confirm.schedule_deadline),
        }
        if isinstance(snap, dict):
            fields["exact_deadline_at"] = self._snapshot_deadline(snap)
            fields["snapshot_phase"] = snap.get("phase")
            fields["responding_seats"] = snap.get("responding_seats") or []
        # JSONL 记录不需要 null 字段，且旧 fake Recorder 可能只接收
        # 非空字段；保留 pending/round 等稳定字段，丢弃未提供项。
        fields = {key: value for key, value in fields.items()
                  if value is not None}
        with self._stats_lock:
            if outcome == "requested":
                self.stats["window_confirm_requests"] += 1
            elif outcome in ("open", "confirmed"):
                self.stats["window_confirm_open"] += 1
            elif outcome in ("closed", "expired", "not_responding",
                             "deadline_missing"):
                self.stats["window_confirm_closed"] += 1
            elif outcome in ("stale", "identity_changed", "new_round"):
                self.stats["window_confirm_stale"] += 1
            elif outcome == "unconfirmed":
                self.stats["window_confirm_unconfirmed"] += 1
            if outcome == "miss":
                self.stats["window_confirm_miss"] += 1
        if self.recorder is not None:
            writer = getattr(self.recorder, "window_confirm", None)
            if writer is not None:
                writer(gid, **fields)

    @classmethod
    def _epoch_from_mono(cls, mono):
        """把本地 monotonic 截止转回 epoch，仅用于诊断记录。"""
        if mono is None:
            return None
        try:
            return time.time() + (float(mono) - time.monotonic())
        except (TypeError, ValueError):
            return None

    @classmethod
    def _confirm_schedule_deadline(cls, stamp):
        """以秒级弃牌 ts+2 生成仅供 EDF 排序的乐观上界。

        与 ``_srv_deadline`` 不同，这里故意保留已过期值；确认请求即使
        排队超时也必须发出以取得最终权威状态。调用者绝不以此授权动作。
        """
        stamp = cls._epoch_seconds(stamp)
        if stamp is None:
            return None
        return time.monotonic() + (stamp + WINDOW_SEC * 2 - time.time())

    @classmethod
    def _window_confirm_matches(cls, mirror, confirm, snap=None, seq=None):
        """确认快照是否仍是原碰窗；不比较脆弱的 ``_window_key``。

        同局同家同牌可能再次出现，因此补充 latest discard 与秒级 ts
        的一致性检查；牌河计数只作镜像重建提示，不作为硬条件。
        """
        if mirror is None or confirm is None:
            return False
        if mirror.round_no != confirm.round_no:
            return False
        if mirror.pending is None or confirm.pending is None:
            return False
        if tuple(mirror.pending) != tuple(confirm.pending):
            return False
        snap_source = cls._explicit_source_seq(snap)
        if (confirm.source_seq is not None and snap_source is not None
                and str(snap_source) != str(confirm.source_seq)):
            return False
        if isinstance(snap, dict) and snap.get("last_discard"):
            try:
                if tidx(snap["last_discard"]) != confirm.pending[1]:
                    return False
            except (KeyError, TypeError, ValueError):
                return False
        return True

    def _resolve_window_confirm(self, gid, mirror, snap, confirm, seq=None):
        """消费一次临界碰窗确认，返回 ``confirmed`` 或最终结果。

        ``confirmed`` 只说明快照允许重新计算动作；动作仍由
        ``_act_window`` 用快照阶段、responding、精确 deadline 和新合法集
        再次判断。其它结果不会沿用旧动作。
        """
        if not isinstance(snap, dict) or not self._window_confirm_matches(
                mirror, confirm, snap=snap, seq=seq):
            reason = "new_round" if (mirror is not None and
                                      mirror.round_no != confirm.round_no) \
                else "identity_changed"
            self._record_window_confirm(gid, confirm, "stale", reason=reason,
                                        snap=snap, seq=seq)
            if confirm.legal and confirm.chosen != -1:
                self._claim_miss(
                    gid, confirm.phase, confirm.legal, chosen=confirm.chosen,
                    reason="window_confirm_" + reason, mirror=None,
                    seq=(confirm.source_watermark
                         if confirm.source_watermark is not None
                         else (seq if seq is not None else confirm.source_seq)),
                    pending=confirm.pending)
                self._record_window_confirm(gid, confirm, "miss",
                                            reason=reason, snap=snap, seq=seq)
            return "stale"

        phase = snap.get("phase")
        seat = mirror.me
        responding = seat in (snap.get("responding_seats") or [])
        exact = self._snapshot_deadline(snap)
        current_legal = self._claim_legal(mirror, "response_peng") \
            if phase == "response_peng" else []
        if phase != "response_peng":
            outcome, reason = "closed", "phase_changed"
        elif not responding:
            outcome, reason = "not_responding", "seat_not_responding"
        elif exact is None:
            outcome, reason = "deadline_missing", "exact_deadline_missing"
        elif (confirm.source_ts is not None
              and exact > confirm.source_ts + WINDOW_SEC * 2):
            # 秒级 ts+2 只能给调度器提供乐观上界；如果精确 deadline
            # 已跳到它之后，当前快照身份无法确认，不能把新窗当旧窗，
            # 也不能据此写最终 miss。
            outcome, reason = "identity_unconfirmed", "deadline_outside_source_window"
        elif exact - time.time() <= SUBMIT_EPS:
            outcome, reason = "expired", "exact_deadline_expired"
        elif not current_legal:
            outcome, reason = "no_legal", "new_legal_set_empty"
        else:
            self._record_window_confirm(
                gid, confirm, "open", reason="authoritative_open",
                snap=snap, seq=seq, legal=current_legal)
            return "confirmed"

        # 缺少精确 deadline 只能说明无法授权动作，不能推断服务端已
        # 关闭窗口；这类结果保持 unconfirmed，不记最终 miss。
        if outcome in ("deadline_missing", "identity_unconfirmed"):
            self._record_window_confirm(gid, confirm, "unconfirmed",
                                        reason=reason, snap=snap, seq=seq,
                                        legal=current_legal)
            return "unconfirmed"

        self._record_window_confirm(gid, confirm, outcome, reason=reason,
                                    snap=snap, seq=seq, legal=current_legal)
        # 新快照已证明原碰窗不能再安全提交；只有原镜像确实有候选时
        # 才落最终 miss。预确认阶段绝不写 claim_miss。
        if outcome != "no_legal" and confirm.legal and confirm.chosen != -1:
            self._claim_miss(
                gid, confirm.phase, confirm.legal, chosen=confirm.chosen,
                reason="window_confirm_" + reason, mirror=None,
                seq=(confirm.source_watermark
                     if confirm.source_watermark is not None
                     else (seq if seq is not None else confirm.source_seq)),
                pending=confirm.pending)
            self._record_window_confirm(gid, confirm, "miss", reason=reason,
                                        snap=snap, seq=seq,
                                        legal=current_legal)
        return outcome

    def _state_abandon(self, gid, phase, reason):
        """镜像/手牌状态无法安全决策时的交接诊断。"""
        self._log(f"我方{reason}({phase}),本回合交服务端代打")
        with self._stats_lock:
            self.stats["client_state_abandons"] += 1
            self.stats["auto_played"] += 1

    def _record_timeout(self, e, me=None):
        """记录全局与本人/他家服务端 timeout，保留服务端事件口径。"""
        if e.get("type") != "timeout":
            return
        kind = e.get("kind")
        if kind == "discard":
            key = "timeout_discard"
        elif kind == "response":
            key = "timeout_response"
        else:
            return
        mine = e.get("seat") == me
        with self._stats_lock:
            self.stats[key] += 1
            if mine:
                if kind == "discard":
                    self.stats["my_timeout_discard"] += 1
                elif (e.get("data") or {}).get("window") == "peng":
                    self.stats["my_timeout_peng"] += 1
                elif (e.get("data") or {}).get("window") == "chi":
                    self.stats["my_timeout_chi"] += 1
            elif kind == "discard":
                self.stats["other_timeout_discard"] += 1
            else:
                self.stats["other_timeout_response"] += 1

    @staticmethod
    def _epoch_seconds(value):
        """把事件/快照时间字段归一为 epoch 秒；坏值返回 None。"""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        # window_deadline_ms 是毫秒；事件 ts 通常是秒，但兼容毫秒形态。
        if value > 100_000_000_000:
            value /= 1000.0
        return value

    @classmethod
    def _snapshot_deadline(cls, snap):
        """读取服务端快照给出的当前窗口绝对截止(epoch 秒)。"""
        if not isinstance(snap, dict):
            return None
        try:
            value = float(snap["window_deadline_ms"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    @classmethod
    def _mono_deadline(cls, epoch):
        """将服务端 epoch 截止映射到本地 monotonic。

        只用剩余时间做一次映射，动作日志仍保留原始 epoch 截止；不会
        把 HTTP 响应完成时间当作服务端接受时间。
        """
        epoch = cls._epoch_seconds(epoch)
        if epoch is None:
            return None
        return time.monotonic() + (epoch - time.time())

    @classmethod
    def _window_key(cls, mirror, phase, ev=None, snap=None):
        """生成跨 seq=0 快照仍稳定的逻辑窗口身份。

        ``seq`` 是包含式状态 watermark，不能冒充弃牌来源序号。只有
        ``source_discard_seq`` 及协议明确的别名才是 authoritative；旧
        协议没有来源序号时保留弱 fallback，但显式标记为
        ``legacy_unresolved``，不能拿它证明两个窗口相同。
        """
        if mirror is None or mirror.pending is None:
            return None
        owner, tile = mirror.pending
        source_seq = None

        source_seq = cls._explicit_source_seq(ev)
        if source_seq is None:
            source_seq = cls._explicit_source_seq(snap)
        if source_seq is None:
            source_seq = getattr(mirror, "_source_discard_seq", None)
        if source_seq is not None:
            try:
                source_seq = int(source_seq)
            except (TypeError, ValueError):
                source_seq = str(source_seq)
            identity_status = "authoritative"
            fallback = None
        else:
            # This is useful for diagnostics and best-effort legacy dedupe,
            # but it is deliberately not an authoritative source identity.
            identity_status = "legacy_unresolved"
            fallback = (mirror.n_discards(),
                        tuple(len(m) for m in mirror.melds))
        return WindowAttemptKey(
            WindowId(
                game_id=getattr(mirror, "_game_id", None),
                round_id=mirror.round_no,
                discard_owner=owner,
                source_discard_seq=source_seq,
                tile=tile,
                identity_status=identity_status,
                fallback=fallback,
            ),
            phase,
        )

    @staticmethod
    def _explicit_source_seq(value):
        """Read only an explicit discard-source sequence, never ``seq``."""
        if not isinstance(value, dict):
            return None
        for field in ("source_discard_seq", "last_discard_seq", "discard_seq"):
            if value.get(field) is not None:
                return value[field]
        data = value.get("data")
        if isinstance(data, dict):
            for field in ("source_discard_seq", "last_discard_seq", "discard_seq"):
                if data.get(field) is not None:
                    return data[field]
        return None

    @staticmethod
    def _same_window_identity(left, right):
        """Compare logical window identities without treating weak legacy
        fallbacks as authoritative source sequences.
        """
        if isinstance(left, WindowAttemptKey) and isinstance(right, WindowAttemptKey):
            if left.phase != right.phase:
                return False
            a, b = left.window_id, right.window_id
            if (a.game_id, a.round_id, a.discard_owner, a.tile) != (
                    b.game_id, b.round_id, b.discard_owner, b.tile):
                return False
            if a.authoritative and b.authoritative:
                return a.source_discard_seq == b.source_discard_seq
            # Legacy keys are diagnostic only.  They must not block a new
            # confirmation/action after seq=0 because the fallback can be
            # shared by two real windows.
            return False
        return left == right

    @staticmethod
    def _strong_window_key(key):
        return (isinstance(key, WindowAttemptKey)
                and key.window_id.authoritative)

    @classmethod
    def _window_was_attempted(cls, mirror, key, attempted_keys):
        """Check action safety without turning legacy fallback into identity.

        Authoritative keys use the shared cross-reanchor set.  Legacy keys
        are only deduped within the current non-reanchor snapshot epoch; a
        transport-failed legacy action remains blocked separately so an
        unknown/409 POST is never blindly replayed.
        """
        if key is None:
            return False
        if cls._strong_window_key(key):
            return key in (attempted_keys or set())
        legacy_attempts = getattr(mirror, "_legacy_attempts", {})
        legacy_failed = getattr(mirror, "_legacy_failed", set())
        return bool((legacy_failed and key in legacy_failed) or (
            key in legacy_attempts
            and legacy_attempts[key] == getattr(mirror, "_legacy_epoch", None)))

    def _make_chi_pending(self, mirror, ev=None, snap=None,
                          passed_explicitly=False, responded_keys=None,
                          attempted_keys=None, peng_end_epoch=None,
                          phase_hint=None):
        """从当前 pending 建立吃窗等待态。

        ready_mono 是碰窗结束后的最早提交时刻，deadline_mono 是吃窗
        绝对截止。快照的 window_deadline_ms 优先；增量事件没有该字段时
        才以弃牌 ts/观测时刻作保守估计。等待态携带窗口身份，避免新弃牌
        或 seq=0 重锚后复用陈旧候选。
        """
        if mirror.pending is None:
            return None
        owner, tile = mirror.pending
        if (owner + 1) % 4 != mirror.me:
            return None
        try:
            if not any(a != -1 for a in
                       mirror.build_game("response_chi").legal_actions()):
                return None
        except MirrorInconsistent:
            return None
        key = self._window_key(mirror, "response_chi", ev=ev, snap=snap)
        responded_keys = responded_keys or set()
        attempted_keys = attempted_keys or set()
        # 吃窗已成功响应或已经有一个未确定的 POST，不重复提交。
        if self._window_was_attempted(mirror, key, attempted_keys):
            return None

        needed = {mirror.me}
        for other in range(4):
            if other in (mirror.me, owner):
                continue
            if not (mirror.freeze > 0 and other != mirror.freezer):
                needed.add(other)

        observed = time.monotonic()
        anchored = False
        discard_epoch = None
        if ev is not None:
            discard_epoch = self._epoch_seconds(ev.get("ts"))
        if discard_epoch is not None:
            anchored = True

        # 服务端快照 deadline 是当前 phase 的结束点。response_peng
        # 快照的 deadline 还要再跨过一个吃窗；response_chi 快照的
        # deadline 就是吃窗结束点。
        snap_deadline = self._snapshot_deadline(snap)
        deadline_epoch = None
        if snap_deadline is not None:
            anchored = True
            if phase_hint == "response_chi":
                deadline_epoch = snap_deadline
                ready_mono = observed
            else:
                peng_end_epoch = snap_deadline
                chi_span = self.window_wait or WINDOW_SEC
                deadline_epoch = peng_end_epoch + chi_span
                ready_mono = self._mono_deadline(peng_end_epoch)
                if ready_mono is None:
                    ready_mono = observed
                ready_mono += SUBMIT_EPS
        else:
            # timeout(response,peng) 事件通常紧跟碰窗结束；它比粗粒度
            # 弃牌 ts 更接近真实 transition，但仍用弃牌锚作截止上界。
            if peng_end_epoch is None and discard_epoch is not None:
                peng_end_epoch = discard_epoch + WINDOW_SEC
            if peng_end_epoch is not None:
                ready_mono = self._mono_deadline(peng_end_epoch)
                if ready_mono is None:
                    ready_mono = observed
                ready_mono += SUBMIT_EPS if self.window_wait > 0 else 0.0
            else:
                ready_mono = (observed + self.window_wait + SUBMIT_EPS
                              if self.window_wait > 0 else observed)
            # window_wait=0 is the deterministic-test fast-forward knob; it
            # suppresses sleeping but must not make the protocol deadline zero.
            if discard_epoch is not None:
                deadline_epoch = discard_epoch + max(
                    self.window_wait * 2, WINDOW_SEC * 2)
            elif self.window_wait > 0:
                deadline_epoch = time.time() + self.window_wait * 2
            else:
                deadline_epoch = time.time() + WINDOW_SEC * 2 - SUBMIT_EPS if anchored else None
            if self.window_wait <= 0:
                ready_mono = observed
                if peng_end_epoch is not None:
                    ready_mono = observed
                deadline_epoch = (discard_epoch + WINDOW_SEC * 2
                                  if discard_epoch is not None else deadline_epoch)

        # 如果收包已经接近/超过 estimated ready，必须立即尝试；不能
        # 再以 obs+window_wait 重新睡一整段把动作推过 T+2。
        deadline_mono = self._mono_deadline(deadline_epoch)
        if deadline_mono is not None:
            ready_mono = min(ready_mono, deadline_mono - SUBMIT_EPS)

        return {
            "key": key,
            "round_no": mirror.round_no,
            "pending": (owner, tile),
            "t0": self._mono_at(discard_epoch) if discard_epoch is not None
            else observed,
            "obs": observed,
            "ready_mono": ready_mono,
            "deadline_mono": deadline_mono,
            "deadline_epoch": deadline_epoch,
            "anchored": anchored,
            "confirmed": phase_hint == "response_chi",
            "needed": needed,
            "seen": {mirror.me} if passed_explicitly else set(),
            "resolved": ({mirror.me} if not any(
                a != -1 for a in mirror.build_game("response_peng").legal_actions())
                else set()),
        }

    @classmethod
    def _chi_is_current(cls, mirror, chi):
        """检查等待态仍对应同一 pending 弃牌。"""
        if not chi or mirror is None or mirror.pending is None:
            return False
        if tuple(mirror.pending) != tuple(chi.get("pending", ())):
            return False
        # key 的精确形态可能来自增量 seq 或快照 deadline；两者在
        # seq=0 重锚时必然不同。pending + round 是状态机真正的身份，
        # 新弃牌/认领会在事件处理中先清掉等待态。
        return mirror.round_no == chi.get("round_no", mirror.round_no)

    def _lost_claim(self, mirror):
        """碰窗作废时是否可能损失碰/杠机会:可评估时以真实合法集为准
        (无选项 = 服务端代过与我们自选过等价,不记代打——match 实测
        22 次作废仅 1 次真持有对子);张数漂移无法评估则保守记账;本窗
        已本地决策为过(-1)时不算损失。注意保守记账只影响 auto_played,
        漂移时算不出合法集,不会写 claim_miss(诊断日志只记可评估的窗口)。"""
        if not mirror.hand_count_ok("response_peng"):
            return True
        if self._window_decision(mirror, "response_peng") == -1:
            return False
        return bool(self._claim_legal(mirror, "response_peng"))

    @staticmethod
    def _claim_legal(mirror, phase):
        """返回规则允许的非 pass 吃/碰/杠动作；无法评估时返回空集。"""
        if mirror is None or not mirror.hand_count_ok(phase):
            return []
        try:
            g = mirror.build_game(phase)
            return [a for a in g.legal_actions() if a != -1]
        except MirrorInconsistent:
            return []

    def _record_claim_timeout(self, gid, mirror, phase, reason, seq=None):
        """服务端 timeout 时仍有规则合法动作 → 按本地决策归因写 claim_miss。

        本窗已本地决策为过(-1)不算机会损失,直接跳过;已决策为吃/碰/杠
        记 chosen=该动作;尚无决策记 chosen=None(决策预算不足)。
        """
        legal = self._claim_legal(mirror, phase)
        if not legal:
            return []
        decided = self._window_decision(mirror, phase)
        if decided == -1:
            return legal
        self._claim_miss(gid, phase, legal,
                         chosen=None if decided == "unset" else decided,
                         reason=reason, mirror=mirror, seq=seq)
        return legal

    def _mark_window_decision(self, mirror, phase, act):
        """记录本窗本地已选动作;timeout 归因据此区分未决策/主动过/已选。"""
        decisions = getattr(mirror, "_window_decisions", None)
        if decisions is None:
            return
        key = self._window_key(mirror, phase)
        if key is not None:
            decisions[key] = act

    def _window_decision(self, mirror, phase):
        """本窗本地决策:动作值 / -1(主动过) / "unset"(尚未决策)。"""
        decisions = getattr(mirror, "_window_decisions", None) or {}
        key = self._window_key(mirror, phase)
        return decisions.get(key, "unset") if key is not None else "unset"

    def _listen_notify(self, gid, wake, stop, sse):
        """SSE 监听线程(GET /api/games/{gid}/notify,v12)。

        服务器主动推「状态已变」帧(只含 seq 包含式水位)替代高频轮询:
        收帧 → wake 唤醒主循环以本地游标拉 /state 增量。断流
        (closed/网络错误/keepalive 超时)指数退避自动重连;403/404 =
        场次不可访问,监听退出(主循环靠 /state 收尾);重连期间
        sse["alive"]=False,主循环自动退回轮询节奏。
        """
        delay = self.notify_retry_wait
        while not stop.is_set():
            try:
                resp = self.api.open_notify(gid)
            except ApiError as e:
                if e.status in (401, 403, 404):
                    sse["alive"] = False
                    return
            except OSError:
                pass
            else:
                try:
                    sse["alive"] = True
                    delay = self.notify_retry_wait
                    for raw in resp:
                        if stop.is_set():
                            break
                        line = raw.decode(errors="replace").strip() \
                            if isinstance(raw, bytes) else str(raw).strip()
                        if not line.startswith("data:"):
                            continue  # keepalive 注释(: ...)与事件行
                        try:
                            j = json.loads(line[len("data:"):].strip())
                        except ValueError:
                            continue
                        seq = j.get("seq")
                        # 帧只是水位唤醒信号；重复/倒退水位不应制造额外
                        # /state 拉取，但绝不把它当作本地事件游标。
                        last = sse.get("last_wake_seq")
                        if seq is None or last is None or seq > last or j.get("closed"):
                            sse["last_wake_seq"] = seq
                            wake.put((seq, bool(j.get("closed"))))
                        if j.get("closed"):
                            break
                except (OSError, TimeoutError):
                    pass  # 断流(keepalive 超时/对端关闭):退避重连
                finally:
                    try:
                        resp.close()
                    except OSError:
                        pass
            sse["alive"] = False
            if self._sleep_stop(delay, stop):
                return
            delay = min(delay * 2, 10.0)

    def _play_game_safe(self, gid):
        try:
            self.play_game(gid)
        except Exception as e:
            self._log(f"场次 {gid} 异常: {type(e).__name__}: {e}")
            with self._stats_lock:
                fails = self._game_fails.get(gid, 0) + 1
                self._game_fails[gid] = fails
            if fails >= 3:
                # 连续 3 次异常:放弃重派(防崩溃循环),落终态记录
                # (match 实测 2026-09-08:502 一次即永久弃局,10 局全被
                # 服务端代打污染积分;有限重派 + seq=0 快照重锚可续打)
                if self.recorder is not None:
                    self.recorder.end(gid, "error",
                                      error=f"{type(e).__name__}: {e}")
                self._done_games.add(gid)
            # 未超限:不标完成、不落 end(error)——监督线程重派工作线程,
            # 快照重锚后续写同一份对局日志(避免双终态记录)
            return
        self._done_games.add(gid)

    def _enter(self, tid):
        """幂等进场:register + ready(409 竞态吞掉,主循环兜底)。"""
        try:
            self.api.register(tid)
        except ApiError as e:
            if e.code != "TOURNAMENT_STARTED":
                self._log(f"register: {e}")
        try:
            self.api.ready(tid)
        except ApiError as e:
            if e.code != "TOURNAMENT_STARTED":
                self._log(f"ready: {e}")

    def _my_active(self, t):
        try:
            act = [g.get("game_id") for g in
                   (self.api.me().get("active_games") or [])]
        except ApiError:
            return []
        mine = t.get("my_games")
        if mine is not None:
            ms = set(mine)
            act = [g for g in act if g in ms]
        return [g for g in act if g]

    # ---------- 单场对弈(工作线程) ----------

    def play_game(self, gid):
        self._log(f"开始场次 {gid}")
        rec = self.recorder
        if rec is not None:
            rec.meta(gid, self.name, self._tid,
                     self.you_cai_bi_kao, self.base, mode=self.mode)
        # SSE 通知流(v12):帧 = 状态已变信号,唤醒主循环立即拉 /state
        wake = sse = stop_l = listener = None
        if self.use_notify:
            wake = StateDemand()
            sse = {"alive": False}
            stop_l = threading.Event()
            listener = threading.Thread(
                target=self._listen_notify, args=(gid, wake, stop_l, sse),
                name=f"{self.name}:{gid}:sse", daemon=True)
            listener.start()
        try:
            self._play_loop(gid, wake, sse)
        finally:
            if stop_l is not None:
                stop_l.set()
            if listener is not None:
                listener.join(timeout=2)

    @staticmethod
    def _stale_deadline_step(state_deadline, stale_used, now):
        """过期截止一次性追赶:窗口关闭后若推进事件缺位(流局
        round_ended/gap 等),过期截止会滞留并被后续拉取反复携带——
        持续空占 EDF 抢其他场次配额、虚增 deadline_missed(match 实测
        2026-09-09 b2:流局过渡期连发 7 次)。允许携带过期截止优先
        拉取一次用于追赶,之后恢复普通刷新;新鲜(未来)截止到达时
        重置追赶资格。
        """
        if state_deadline is not None and state_deadline < now:
            return (None, True) if stale_used else (state_deadline, True)
        return state_deadline, False

    @staticmethod
    def _mono_at(ev_ts):
        """服务端事件时刻对应的本地 monotonic(两机钟差实测 p50≈0,
        动作回声 ts 漂移 ±0.4s 为服务端提交管线,不校钟)。ts 缺失或
        异常(超出 10s 钳制)退回当前时刻。"""
        ev_ts = BotClient._epoch_seconds(ev_ts)
        if ev_ts is None:
            return time.monotonic()
        lag = time.time() - ev_ts
        if not 0.0 <= lag <= 10.0:
            return time.monotonic()
        return time.monotonic() - lag

    @classmethod
    def _srv_deadline(cls, ev_ts, span):
        """以服务端事件 ts 锚定的本地窗口截止(供 EDF)。

        观测迟到时按真实剩余窗收缩(旧实现锚定观测时刻,迟到 0.5s 的
        1 秒碰窗截止虚高 0.5s,EDF 排序失真且生来过期);剩余不足
        2×DEADLINE_MARGIN(死窗)返回 None——不为已关窗口抢配额。
        ts 缺失退回观测时刻锚定。
        """
        ev_ts = cls._epoch_seconds(ev_ts)
        if ev_ts is None:
            return time.monotonic() + span - DEADLINE_MARGIN
        remaining = ev_ts + span - time.time()
        if remaining <= 2 * DEADLINE_MARGIN:
            return None
        return time.monotonic() + remaining - DEADLINE_MARGIN

    @staticmethod
    def _next_seat_step(next_seat, e):
        """从已观测事件推进「下一个动作者」预测(懒轮询门用)。

        摸牌/吃/碰/杠者接下来打牌(杠为补牌后);弃牌后轮到下家摸;
        pass/timeout(窗口解决)不改道。反应窗被他人认领会改道到认领
        者——不可预测,由懒轮询门的保守预测兜底(见 _poll_urgent)。
        """
        t, s = e["type"], e["seat"]
        if t in ("tile_drawn", "chi", "peng", "gang"):
            return s
        if t == "tile_discarded" and s is not None:
            return (s + 1) % 4
        return next_seat

    def _poll_urgent(self, mirror, next_seat, window_responded=True):
        """预测下一事件是否可能开启我方动作窗(懒轮询门,SSE/长轮询共用)。

        保守集:状态未知/抓打圈在途一律急;当前反应窗未被本方响应过
        一律急(决策尚未发生);轮到我方摸打(3s 弃牌窗)、下家打牌
        (2s 吃窗)或我方持对子(1s 碰窗,财神 33 不可碰杠)视为急,
        其余他家摸打可推迟观测 ≤ LAZY_POLL_WAIT(自摸 3s 窗与吃窗
        T+2 截止均有余量;他人认领改道约 1% 概率落在推迟窗内,
        吃窗 2s 仍可追)。我方已响应过的窗口(含自家弃牌)其解决
        超时簇与我方无关,可吸收。
        """
        if next_seat is None or mirror.freeze > 0:
            return True
        if mirror.pending is not None and not window_responded:
            return True
        if next_seat == mirror.me or (next_seat + 1) % 4 == mirror.me:
            return True
        hand = mirror.my_hand
        return any(hand[t] >= 2 for t in range(33))

    @staticmethod
    def _structurally_no_nonpass_response(mirror, owner, tile):
        """Prove that the cards alone cannot produce peng/chi.

        This deliberately does not inspect ``mirror.phase`` or any local
        response-turn flag.  It is therefore safe as the only basis for
        clearing an urgent window fetch when an incremental mirror may still
        be behind the authoritative catch-play phase.
        """
        try:
            tile = int(tile)
            hand = mirror.my_hand
        except (AttributeError, TypeError, ValueError):
            return False
        if not (0 <= tile < len(hand)):
            return False
        if hand[tile] >= 2:
            return False  # peng/ming-gang candidate
        if owner is None or (owner + 1) % 4 != mirror.me:
            return hand[tile] < 2
        if tile >= 27:
            return hand[tile] < 2
        offset, pos = divmod(tile, 9)
        patterns = ((-2, -1), (-1, 1), (1, 2))
        for left, right in patterns:
            if 0 <= pos + left < 9 and 0 <= pos + right < 9:
                if hand[offset + pos + left] > 0 and hand[offset + pos + right] > 0:
                    return False
        return True

    def _play_loop(self, gid, wake, sse):
        rec = self.recorder
        # With SSE this is the same per-game object used by the listener; in
        # polling mode it still coordinates logical reasons and physical
        # requests locally.
        demand = wake if isinstance(wake, StateDemand) else StateDemand()
        seq = 0
        mirror = None
        chi_pending = None  # 吃窗等待态 {"t0","needed","seen"}(提交前保持)
        window_confirm = None  # 临界 response_peng 的一次权威确认
        request_kind = "RESYNC"  # 本轮 /state 的诊断分类，消费后清空
        responded_windows = set()  # 当前对局已成功提交的响应窗身份
        attempted_windows = set()  # 409/未知结果后禁止在同窗盲重试
        legacy_attempts = {}       # fallback key → current non-reanchor epoch
        legacy_failed = set()       # legacy transport failure safety barrier
        legacy_epoch = 0            # increments only for FULL/seq=0 snapshots
        window_decisions = {}  # 窗口身份 → 本地已选动作(timeout 归因用)
        # 当前 pending 弃牌的来源身份：(round_no, owner, tile, event_seq)；
        # seq=0 重锚只在快照仍指向同一 pending 时沿用它。
        pending_source = None
        state_deadline = None  # 下一次 /state 的本地窗口截止(monotonic)
        stale_dl_used = False  # 过期截止已用于一次追赶拉取
        next_seat = None       # 下一个动作者预测(懒轮询门;快照后重置)
        window_responded = True  # 当前反应窗已被本方响应(超时簇可吸收)
        lazy_until = 0.0       # 懒轮询窗截止:本次拉取后 +LAZY_POLL_WAIT
        lazy_floor = 0.0       # 动作后绝对懒下限(锚定窗关闭+0.6s)
        chi_fetch_at = 0.0     # 上次吃窗快照抓取(monotonic,限速用)
        chi_fetches = 0        # 本弃牌窗已抓取次数(每张新弃牌清零)
        decide_fails = 0    # 决策/快照路径自愈预算(超限上抛终止)
        demand_seen = {}
        while True:
            t0 = time.monotonic()
            state_deadline, stale_dl_used = self._stale_deadline_step(
                state_deadline, stale_dl_used, t0)
            used_request_kind = request_kind
            if used_request_kind is None:
                if window_confirm is not None:
                    used_request_kind = "WINDOW_PENG"
                elif chi_pending is not None:
                    used_request_kind = "WINDOW_CHI"
                elif seq == 0:
                    used_request_kind = "RESYNC"
                else:
                    used_request_kind = "SSE_DELTA"
            plan = self._prepare_state_demand(
                demand, gid, seq, used_request_kind, state_deadline,
                window_confirm, chi_pending)
            self._record_demand_metrics(demand, demand_seen)
            physical_seq = seq if plan is None else plan.seq
            if plan is not None:
                if plan.kind == RESYNC:
                    used_request_kind = "RESYNC"
                elif plan.kind == WINDOW_CONFIRM:
                    phase = (plan.reasons.get(WINDOW_CONFIRM) or {}).get("phase")
                    used_request_kind = ("WINDOW_CHI"
                                         if phase == "response_chi"
                                         else "WINDOW_PENG")
                else:
                    used_request_kind = "SSE_DELTA"
            # request_kind describes this physical/logical state request;
            # after it starts, local action paths must explicitly request a
            # new reason instead of inheriting the old label.
            request_kind = None
            state_attempts = None
            state_transport = None
            try:
                from .api import Api
                if isinstance(self.api, Api):
                    res = self._state(gid, physical_seq, state_deadline,
                                      request_kind=used_request_kind,
                                      plan=plan)
                else:
                    # Keep the small fake/server adapter signature used by
                    # replay and unit tests.
                    res = self._state(gid, physical_seq, state_deadline,
                                      request_kind=used_request_kind)
                state_attempts = self._attempts()
                state_transport = self._transport()
                self._record_state_transport(state_attempts, state_transport)
                if isinstance(wake, StateDemand):
                    wake.acknowledge(res.get("seq", seq))
                status = 200
            except ApiError as e:
                status = e.status
                # Api TLS 诊断必须在后续 throttle/日志路径前取出；错误
                # 请求同样计入物理 state 尝试和 429 重试。
                state_attempts = self._attempts()
                state_transport = self._transport()
                self._record_state_transport(state_attempts, state_transport)
                ticket = self._throttle_ticket()
                self._record_throttle(ticket)
                if rec is not None:
                    self._record_state_req(
                        rec, gid, seq, status, _ms(t0), state_attempts,
                        state_transport, ticket, used_request_kind,
                        plan=plan, demand=demand, requested_seq=physical_seq)
                if e.status == 404:
                    # 场次不可访问(轮次切换/房间回收):视作已结束计数
                    self._log(f"场次 {gid} 已不可访问")
                    with self._stats_lock:
                        self.stats["games"] += 1
                    if rec is not None:
                        self._record_end(rec, gid, "inaccessible")
                    self._record_demand_metrics(demand, demand_seen)
                    return
                raise
            ticket = self._throttle_ticket()
            self._record_throttle(ticket)
            lazy_until = time.monotonic() + LAZY_POLL_WAIT
            if rec is not None:
                self._record_state_req(
                    rec, gid, seq, status, _ms(t0), state_attempts,
                    state_transport, ticket, used_request_kind,
                    summary=self._state_summary(res), plan=plan, demand=demand,
                    requested_seq=physical_seq)
            if plan is not None:
                # Only watermark/resync facts are settled here.  Window
                # confirmation waits for the phase-specific resolver below.
                demand.reconcile(
                    res.get("seq"),
                    response_mode=plan.mode,
                    snapshot=res.get("snapshot"),
                )
            if res.get("finished"):
                if window_confirm is not None:
                    confirm_outcome = self._resolve_window_confirm(
                        gid, None, None, window_confirm, seq=res.get("seq"))
                    demand.finish_window_confirm(
                        self._demand_window_status(confirm_outcome))
                    window_confirm = None
                elif plan is not None and plan.kind == WINDOW_CONFIRM:
                    demand.finish_window_confirm(DEMAND_TERMINAL)
                snap = res.get("snapshot") or {}
                with self._stats_lock:
                    self.stats["games"] += 1
                    if snap.get("scores") is not None:
                        self.stats["scores"].append(snap["scores"])
                self._log(f"场次 {gid} 结束,积分 {snap.get('scores')}")
                if rec is not None:
                    self._record_end(
                        rec, gid, "finished", scores=snap.get("scores"),
                        demand=demand.request_snapshot(),
                        transport_status="complete",
                        window_status="complete",
                        game_status="complete")
                self._record_demand_metrics(demand, demand_seen)
                return
            if res.get("pending"):
                continue
            if res.get("gap"):
                with self._stats_lock:
                    self.stats["gaps"] += 1
            batch = res.get("events") or []
            snap = res.get("snapshot")

            if snap is not None:
                # 快照响应(seq=0/gap/轮边界):全量重建镜像 + 锚定
                seq = res.get("seq", seq)
                if rec is not None:
                    rec.snapshot(gid, seq, snap)
                try:
                    if plan is not None and plan.mode == "FULL":
                        legacy_epoch += 1
                    mirror = self._mirror_from_snapshot(snap)
                    # /state seq is a stream watermark, not the source
                    # discard identity.  Carry the latest parsed discard
                    # sequence only when this authoritative snapshot still
                    # describes that exact pending tile; otherwise start
                    # without a synthetic identity and let the snapshot's
                    # explicit source field (if any) win.
                    source_seq = None
                    for field in ("source_discard_seq", "last_discard_seq",
                                  "discard_seq"):
                        if isinstance(snap, dict) and snap.get(field) is not None:
                            source_seq = snap[field]
                            break
                    if source_seq is None and pending_source is not None:
                        source_round, source_owner, source_tile, source_seq = \
                            pending_source
                        if (source_round != mirror.round_no
                                or mirror.pending != (source_owner,
                                                      source_tile)):
                            source_seq = None
                    if source_seq is not None:
                        mirror._source_discard_seq = source_seq
                    mirror._attempted_windows = attempted_windows
                    mirror._window_decisions = window_decisions
                    mirror._legacy_attempts = legacy_attempts
                    mirror._legacy_failed = legacy_failed
                    mirror._legacy_epoch = legacy_epoch
                    next_seat = None  # 快照后动作者未知,懒门转急直至事件重建
                    confirm_outcome = None
                    if window_confirm is not None:
                        pending_confirm = window_confirm
                        window_confirm = None
                        confirm_outcome = self._resolve_window_confirm(
                            gid, mirror, snap, pending_confirm,
                            seq=res.get("seq", seq))
                        demand.finish_window_confirm(
                            self._demand_window_status(confirm_outcome))
                    elif plan is not None and plan.kind == WINDOW_CONFIRM:
                        # A chi wait-state has no exception object; its
                        # authoritative snapshot still resolves the reason
                        # by phase.  A phase transition is terminal for this
                        # logical window and a new window may be submitted.
                        reason_data = (demand.reasons.get(WINDOW_CONFIRM)
                                       or {})
                        expected_phase = reason_data.get("phase")
                        if expected_phase == snap.get("phase"):
                            demand.finish_window_confirm(SATISFIED)
                        elif snap.get("phase") is not None:
                            demand.finish_window_confirm(DEMAND_TERMINAL)
                    # 快照是新的事实边界；旧批次的 trigger/chi 等待态
                    # 不能跨边界携带。已成功/已尝试的响应身份保留在本
                    # 局循环内，防止 seq=0 后重复 POST。
                    chi_pending = self._act_on_snapshot(
                        mirror, snap, gid,
                        responded_keys=responded_windows,
                        attempted_keys=attempted_windows,
                        allow_peng=(confirm_outcome in (None, "confirmed", "stale")))
                    if chi_pending is not None:
                        state_deadline = chi_pending.get("deadline_mono")
                        request_kind = "WINDOW_CHI"
                        window_responded = bool(
                            self._window_key(mirror, "response_peng",
                                             snap=snap)
                            in responded_windows)
                        self._wait_wake(wake, sse, max_wait=max(
                            0.0, chi_pending["ready_mono"] - time.monotonic()))
                        seq = 0  # 下一次拉取同时承担窗口确认，不另加请求
                    else:
                        state_deadline = None
                except Exception as ex:
                    if isinstance(ex, _WindowConfirm):
                        window_confirm = ex
                        seq, mirror = 0, None
                        chi_pending = None
                        state_deadline = ex.schedule_deadline
                        request_kind = "WINDOW_PENG"
                        next_seat = None
                        window_responded = True
                        self._record_window_confirm(
                            gid, ex, "requested", reason=ex.reason,
                            seq=ex.source_seq)
                        continue
                    if isinstance(ex, _ActionResync):
                        self._log(f"动作结果需重锚: {ex.reason}")
                        if rec is not None:
                            rec.reset(gid, f"动作结果需重锚: {ex.reason}")
                        seq, mirror = 0, None
                        chi_pending = None
                        window_confirm = None
                        state_deadline = None
                        request_kind = "RESYNC"
                        next_seat = None
                        window_responded = True
                        continue
                    decide_fails += 1
                    if decide_fails > 3:
                        raise
                    self._log(f"快照决策异常({type(ex).__name__}: {ex}),"
                              f"重拉快照({decide_fails}/3)")
                    with self._stats_lock:
                        self.stats["decide_errors"] += 1
                    if rec is not None:
                        rec.reset(gid,
                                  f"快照决策异常: {type(ex).__name__}: {ex}")
                    seq, mirror = 0, None
                    chi_pending = None
                    window_confirm = None
                    state_deadline = None
                    request_kind = "RESYNC"
                    next_seat = None
                    window_responded = True
                continue

            if rec is not None and batch:
                rec.events(gid, res.get("seq"), batch)
            trigger = None
            batch_seen = set()  # 同批内触发弃牌之后的其他家窗口响应
            window_event = None  # 当前 batch 最后一个仍可响应的弃牌
            peng_timeout = None  # 我方碰窗已由服务端关闭的事件
            chi_timeout = False
            for ev in batch:
                e_seq = ev.get("seq")
                if e_seq is not None:
                    seq = max(seq, e_seq)
                if mirror is None:
                    continue  # 首快照未到,事件留待快照重建
                try:
                    e = parse_event(ev)
                    mirror.apply_event(ev)
                except MirrorInconsistent as ex:
                    self._log(f"镜像失步({ex}),seq=0 重建")
                    with self._stats_lock:
                        self.stats["mirror_resets"] += 1
                    if rec is not None:
                        rec.reset(gid, str(ex))
                    seq, mirror, trigger = 0, None, None
                    pending_source = None
                    chi_pending = None
                    state_deadline = None  # 重锚后旧窗截止作废(防滞留虚增 dm)
                    next_seat = None
                    window_responded = True
                    break
                self._record_timeout(e, mirror.me)
                t = e["type"]
                next_seat = self._next_seat_step(next_seat, e)
                if t == "tile_drawn" and e["seat"] == mirror.me:
                    trigger = ("draw", e)
                    chi_pending = None  # 我方回合推进:旧窗作废
                    state_deadline = self._srv_deadline(e["ts"], DISCARD_SEC)
                elif t in ("chi", "peng") and e["seat"] == mirror.me:
                    trigger = ("draw", e)  # 吃碰后进入自家弃牌
                    chi_pending = None
                    state_deadline = self._srv_deadline(e["ts"], DISCARD_SEC)
                elif t == "tile_discarded":
                    # Keep the source discard sequence outside Mirror: a
                    # seq=0 snapshot rebuild resets Mirror's local counters,
                    # while this identity must survive until the pending
                    # discard is claimed, timed out, or replaced.
                    pending_source = (mirror.round_no, e["seat"], e["tile"],
                                      self._explicit_source_seq(e))
                    window_responded = False  # 新弃牌开新窗:未响应态
                    chi_fetches = 0           # 新弃牌:吃窗抓取预算重置
                    if e["seat"] == mirror.me:
                        # 自家弃牌:正常提交后的回声(无触发,空跑),或
                        # 网络停摆期弃牌窗超时被服务端代打(批内 draw
                        # 触发已陈旧,再提交必 409)——作废触发,交由
                        # 后续事件推进(match 实测 2026-09-08,P4)
                        if trigger is not None and trigger[0] == "draw":
                            trigger = None
                            self._auto_played(gid, "弃牌窗超时被代打")
                        chi_pending = None
                        window_event = None
                        state_deadline = None
                    else:
                        trigger = ("window", e)
                        window_event = e
                        chi_pending = None  # 任何新弃牌:旧窗作废
                        # 先抢 1 秒碰/杠窗；若仅能吃，_act_window 建立吃窗后
                        # 下一轮把截止放宽到 2 秒。锚定服务端弃牌 ts:
                        # 观测迟到时按真实剩余窗收缩,死窗(None)不抢配额。
                        state_deadline = self._srv_deadline(e["ts"], WINDOW_SEC)
                        # Only a phase-independent card-structure proof may
                        # clear urgent.  Local legal_actions() depends on the
                        # mirror phase and was the source of the catch-play
                        # freeze race; unresolved identity/phase stays urgent.
                        if self._structurally_no_nonpass_response(
                                mirror, e["seat"], e["tile"]):
                            state_deadline = None
                elif t in ("chi", "peng", "gang"):
                    if mirror.pending is None:
                        pending_source = None
                    chi_pending = None  # 有人吃/碰/杠:窗口被认领
                    window_event = None
                    if trigger is not None and trigger[0] == "window":
                        trigger = None  # 同批内认领:窗口已失效
                        state_deadline = None  # 窗已死:截止滞留只虚增 dm
                elif (t == "timeout" and e.get("kind") == "hu_failed"
                        and e["seat"] == mirror.me):
                    # 我方吃/碰后平台不发弃牌窗(timeout kind=hu_failed,
                    # turn 直达下家摸牌,手牌自此比引擎预期多 1 张)——
                    # 作废本批后续触发并 seq=0 快照重锚,避免提交废弃牌
                    # (必 409)与后续决策的手牌数断言炸线程
                    # (match 实测 2026-09-08,详见 PROGRESS.md P4)
                    self._log("我方吃碰后 hu_failed:弃牌被跳过,快照重锚")
                    with self._stats_lock:
                        self.stats["hu_failed"] += 1
                    if rec is not None:
                        rec.reset(gid, "hu_failed:吃碰后弃牌被跳过")
                    seq, mirror, trigger = 0, None, None
                    pending_source = None
                    chi_pending = None
                    state_deadline = None  # 重锚后旧窗截止作废(防滞留虚增 dm)
                    next_seat = None
                    window_responded = True
                    break
                elif t == "timeout" and e.get("kind") == "discard" \
                        and e["seat"] == mirror.me:
                    # 我方弃牌窗超时代打:draw 触发已陈旧(提交必 409)
                    if trigger is not None and trigger[0] == "draw":
                        trigger = None
                        if mirror.freeze > 0 and mirror.freezer != mirror.me:
                            # 抓打圈被冻座位的强制弃牌(只弃刚摸牌,无
                            # 选择)由服务端即时代打,非我方损失——实测
                            # 同秒摸牌+代打,任何客户端都来不及也不必
                            # 提交(v3 房 7 次全部如此);回声同步手牌
                            self._log("抓打圈强制弃牌被服务端代打(非损失)")
                            with self._stats_lock:
                                self.stats["platform_forced_discard"] += 1
                        else:
                            self._auto_played(gid, "弃牌窗超时被代打")
                        state_deadline = None  # 弃牌窗已死:截止滞留虚增 dm
                elif t in ("round_ended", "game_ended"):
                    pending_source = None
                elif t in ("pass", "timeout"):
                    # 碰窗响应(pass 或 response 超时;discard 已上面处理)
                    if t == "pass" or e.get("kind") == "response":
                        if t == "pass" or e["data"].get("window") == "peng":
                            batch_seen.add(e["seat"])
                            if chi_pending is not None:
                                chi_pending["seen"].add(e["seat"])
                        if t == "timeout" and e["seat"] == mirror.me:
                            if e["data"].get("window") == "chi":
                                # 我方吃窗被服务端代过:记录仍有规则合法动作的机会
                                self._record_claim_timeout(
                                    gid, mirror, "response_chi",
                                    "server_timeout_chi", seq=seq)
                                chi_pending = None
                                chi_timeout = True
                            elif e["data"].get("window") == "peng":
                                # 我方碰窗超时只作废 peng 提交。若该
                                # 弃牌属于下家，T+1 后仍有合法吃窗，
                                # window_event 必须保留到批处理结束。
                                peng_timeout = e
                                if trigger is not None \
                                        and trigger[0] == "window":
                                    trigger = None
                                    state_deadline = None  # peng 窗已死
                                if self._lost_claim(mirror):
                                    self._record_claim_timeout(
                                        gid, mirror, "response_peng",
                                        "server_timeout_peng", seq=seq)
                                    self._auto_played(gid, "碰窗超时被代打")
                        else:
                            if chi_pending is not None:
                                chi_pending["seen"].add(e["seat"])
                            if trigger is not None and trigger[0] == "window":
                                batch_seen.add(e["seat"])
            # 同批 tile_discarded + 我方 peng timeout：旧实现到这里
            # 只有 trigger=None，直接丢掉 chi。保留最后一个仍在
            # pending 的弃牌，并在碰窗关闭后建立吃窗候选。
            if (trigger is None and window_event is not None
                    and peng_timeout is not None and not chi_timeout
                    and mirror is not None and mirror.pending is not None):
                chi_pending = self._make_chi_pending(
                    mirror, ev=window_event,
                    peng_end_epoch=self._epoch_seconds(
                        peng_timeout.get("ts")),
                    responded_keys=responded_windows,
                    attempted_keys=attempted_windows)
                if chi_pending is not None:
                    chi_pending["seen"] |= batch_seen

            if trigger is None:
                if chi_pending is not None:
                    # 无碰/杠可做 → 碰窗与本人无关:不必等响应观测齐,直接
                    # 用带吃窗截止的 seq=0 快照轮询(EDF 优先,实测往返
                    # ~60ms)。增量轮询会被服务端挂起拖到 ~0.8s,而吃窗只有
                    # 1s,等不起——干等期占满往返即丢窗(实测 41/106)。
                    now = time.monotonic()
                    chi_deadline = chi_pending.get("deadline_mono")
                    # 无碰/杠可做 + 截止可评估 + 未超抓取上限 → 直接抓快照
                    if (not chi_pending.get("peng_claims")
                            and chi_deadline is not None
                            and chi_fetches < EAGER_CHI_MAX):
                        if now >= chi_deadline - SUBMIT_EPS:
                            chi_pending = None  # 窗已关:不再抢配额
                            continue
                        ready = chi_pending.get("ready_mono")
                        # 离吃窗开启还早 / 距上次抓取太近:短等,别空占 EDF
                        hold = 0.0
                        if ready is not None:
                            hold = max(hold, ready - EAGER_CHI_LEAD - now)
                        hold = max(hold, chi_fetch_at + EAGER_CHI_GAP - now)
                        if hold > 0:
                            self._wait_wake(wake, sse, max_wait=hold)
                            continue
                        chi_fetch_at = now
                        chi_fetches += 1
                        seq = 0
                        state_deadline = chi_deadline
                        request_kind = "WINDOW_CHI"
                        chi_pending = None
                        continue
                    # 有碰/杠选项:按原语义等响应观测齐/转换点,再抓快照确认
                    # (不能直接授权 POST;秒级时间戳会使这里比实际开窗更早)
                    ready = chi_pending.get(
                        "ready_mono", chi_pending["t0"] + self.window_wait + 0.05)
                    if not chi_pending["needed"].issubset(
                            chi_pending["seen"] | chi_pending.get("resolved", set())) \
                            and now < ready:
                        if self.long_poll:
                            pre = ready - 0.15 - now
                            if pre > 0:
                                time.sleep(pre)
                            continue
                        else:
                            self._wait_wake(wake, sse, max_wait=ready - now)
                            continue
                    seq = 0
                    state_deadline = chi_pending.get("deadline_mono")
                    request_kind = "WINDOW_CHI"
                    chi_pending = None
                    continue
                elif self.long_poll:
                    # 长轮询:直接再发 /state 由服务端挂起。返回率=事件
                    # 刷新簇率(每回合 ~2-3 簇:弃牌/T+1 碰超时/T+2 吃
                    # 超时),10 场并发仍会饱和限速——懒下限期内(动作后
                    # 锚定窗关闭+0.3s)绝对不发;预测无关簇(已响应窗
                    # 口的超时等)睡到懒截止再发,批量领取积压事件
                    now = time.monotonic()
                    if (state_deadline is None and now < lazy_until
                            and not self._poll_urgent(
                                mirror, next_seat, window_responded)):
                        time.sleep(lazy_until - now)
                elif (sse is not None and sse.get("alive")
                        and mirror is not None and state_deadline is None
                        and time.monotonic() < lazy_until
                        and not self._poll_urgent(mirror, next_seat,
                                                  window_responded)):
                    # 懒轮询:预测下一事件与我方无关时,唤醒只记账不
                    # 消耗限速配额;懒截止后的首个唤醒(有积压待追平)、
                    # 预测转急或兜底超时才真正拉取。纯空闲(无唤醒到
                    # 懒截止)回到正常兜底节奏,不额外拉取。
                    absorbed = False
                    while time.monotonic() < lazy_until:
                        if self._wait_wake(wake, sse, max_wait=(
                                lazy_until - time.monotonic())):
                            absorbed = True
                        else:
                            break
                    if not absorbed:
                        self._wait_wake(wake, sse)
                else:
                    self._wait_wake(wake, sse)  # SSE 帧/兜底超时驱动下一轮
                continue
            try:
                if trigger[0] == "draw":
                    self._act_draw(mirror, gid, trigger[1])
                    window_responded = True  # 自家弃牌窗:超时簇与我无关
                    # 决策已提交；后续拉取只是等回声/下一事件，不再沿用
                    # 已消费的弃牌截止，避免过期 deadline 触发无意义快重试。
                    state_deadline = None
                elif trigger[0] == "window":
                    chi_pending = self._act_window(mirror, trigger[1], gid)
                    window_responded = True  # 窗口已响应(过/碰/吃登记)
                    if chi_pending is not None:
                        # 同批内已到的他家窗口响应直接入账(否则要等下批)
                        chi_pending["seen"] |= batch_seen
                        chi_deadline = chi_pending.get("deadline_mono")
                        if (not chi_pending.get("peng_claims")
                                and chi_deadline is not None
                                and chi_fetches < EAGER_CHI_MAX):
                            # 无碰/杠可做:直接抓权威吃窗快照(seq=0 + 吃窗
                            # 截止 → EDF 优先,实测往返 ~60ms),不要先用增量
                            # 轮询把 1s 窗耗掉(服务端挂起 ~0.8s,实测
                            # 41/106 窗口因此从未发出确认请求)
                            chi_fetch_at = time.monotonic()
                            chi_fetches += 1
                            seq = 0
                            state_deadline = chi_deadline
                            request_kind = "WINDOW_CHI"
                            chi_pending = None
                        else:
                            state_deadline = (chi_pending["t0"]
                                              + self.window_wait * 2
                                              - DEADLINE_MARGIN)
                    else:
                        state_deadline = None
                # 动作后的 T+1/T+2 窗口超时簇与我方无关:懒门下限抬到
                # 锚定窗关闭点+0.3s,期间绝对不轮询——持对子的紧急预测
                # 不覆盖此下限(v2 实弹:预测覆盖使需求仍 ~15/s 饱和);
                # +0.3 而非 +0.6:快对手的下一张弃牌最早 T+2.6 到达,
                # 下限须在此之前到期,否则可碰弃牌观测被推迟过 1s 窗
                # (v3 实弹 5 次真丢碰均为下限边缘 +0.5s 刷新叠加)
                if trigger[1] is not None:
                    # +0.3 基础 + [0, 0.3) 抖动:下限上界 T+2.6 仍在
                    # 最早下张弃牌(T+2.5)的安全侧边缘内,且 10 场次的
                    # 唤醒去同步,避免限速队列突发(v7 丢碰尾部成因)
                    lazy_floor = max(
                        lazy_floor,
                        self._mono_at(trigger[1].get("ts"))
                        + self.window_wait * 2 + 0.3
                        + random.random() * 0.3)
                    lazy_until = max(lazy_until, lazy_floor)
            except Exception as ex:
                if isinstance(ex, _WindowConfirm):
                    window_confirm = ex
                    seq, mirror, trigger = 0, None, None
                    chi_pending = None
                    state_deadline = ex.schedule_deadline
                    request_kind = "WINDOW_PENG"
                    next_seat = None
                    window_responded = True
                    self._record_window_confirm(
                        gid, ex, "requested", reason=ex.reason,
                        seq=ex.source_seq)
                    continue
                if isinstance(ex, _ActionResync):
                    self._log(f"动作结果需重锚: {ex.reason}")
                    if rec is not None:
                        rec.reset(gid, f"动作结果需重锚: {ex.reason}")
                    seq, mirror, trigger = 0, None, None
                    chi_pending = None
                    window_confirm = None
                    state_deadline = None
                    request_kind = "RESYNC"
                    next_seat = None
                    window_responded = True
                    continue
                # 决策/提交路径异常(如镜像失步后手牌张数不符,shanten
                # 断言 ValueError 直穿):有限次快照重锚自愈,超限上抛,
                # 由 _play_game_safe 落 end(error) 终止——不再静默丢局
                decide_fails += 1
                if decide_fails > 3:
                    raise
                self._log(f"决策异常({type(ex).__name__}: {ex}),"
                          f"快照重锚({decide_fails}/3)")
                with self._stats_lock:
                    self.stats["decide_errors"] += 1
                if rec is not None:
                    rec.reset(gid,
                              f"决策异常: {type(ex).__name__}: {ex}")
                seq, mirror, trigger = 0, None, None
                chi_pending = None
                window_confirm = None
                state_deadline = None  # 重锚后旧窗截止作废(防滞留虚增 dm)
                request_kind = "RESYNC"
                next_seat = None
                window_responded = True
                continue

    # ---------- 决策 ----------

    def _decide_logged(self, g, mirror, phase, gid):
        """decide 包装:记录合法集/所选动作/耗时/镜像摘要。"""
        legal = sorted(g.legal_actions())
        t0 = time.monotonic()
        with self._decide_lock:
            act = self.decide(g, mirror.me)
        if self.recorder is not None:
            self.recorder.decision(
                gid, phase, legal, act, _ms(t0),
                digest={"hand": int(sum(mirror.my_hand)),
                        "wall": mirror.live_wall_left(),
                        "round_no": mirror.round_no})
        return act

    def _attempts(self):
        """本次请求的尝试次数(含退避重试);非 Api 实例无此信息。"""
        from .api import Api, _TLS
        if isinstance(self.api, Api):
            return getattr(_TLS, "attempts", None)
        return None

    def _transport(self):
        """取 Api._request 留下的本次传输诊断；fake API 返回 None。"""
        from .api import Api, _TLS
        if isinstance(self.api, Api):
            return getattr(_TLS, "request_meta", None)
        return None

    def _throttle_ticket(self):
        from .api import Api, _TLS
        if not isinstance(self.api, Api):
            return None
        ticket = getattr(_TLS, "throttle_ticket", None)
        if ticket is None:
            return None
        return {"queue_wait_ms": ticket.waited_ms,
                "urgent": ticket.urgent,
                "deadline_missed": ticket.deadline_missed,
                "deadline_left_ms": ticket.deadline_left_ms,
                "throttle_enter": getattr(ticket, "throttle_enter", None),
                "throttle_granted": getattr(ticket, "throttle_granted", None),
                "queue_wait_ms_monotonic": getattr(ticket, "queue_wait_ms", None),
                "status": "applicable"}

    @staticmethod
    def _window_id_for_demand(gid, confirm=None, window_key=None):
        if isinstance(window_key, WindowAttemptKey):
            source = window_key.window_id
            return WindowId(
                game_id=gid if source.game_id is None else source.game_id,
                round_id=source.round_id,
                discard_owner=source.discard_owner,
                source_discard_seq=source.source_discard_seq,
                tile=source.tile,
                identity_status=source.identity_status,
                fallback=source.fallback,
            )
        if confirm is None:
            return None
        pending = confirm.pending
        if pending is None:
            return None
        source_seq = confirm.source_seq
        if source_seq is not None:
            try:
                source_seq = int(source_seq)
            except (TypeError, ValueError):
                source_seq = str(source_seq)
            identity_status = "authoritative"
            fallback = None
        else:
            identity_status = "legacy_unresolved"
            fallback = ("confirm", confirm.round_no, tuple(pending))
        return WindowId(
            game_id=gid,
            round_id=confirm.round_no,
            discard_owner=pending[0],
            source_discard_seq=source_seq,
            tile=pending[1],
            identity_status=identity_status,
            fallback=fallback,
        )

    def _prepare_state_demand(self, demand, gid, seq, request_kind,
                              deadline, window_confirm, chi_pending):
        """Merge the current loop intent and choose the physical request."""
        if window_confirm is not None:
            demand.submit_window_confirm(
                self._window_id_for_demand(gid, confirm=window_confirm),
                window_confirm.phase,
                deadline=window_confirm.schedule_deadline,
            )
            window_confirm.generation = demand.generation
        elif chi_pending is not None:
            demand.submit_window_confirm(
                self._window_id_for_demand(
                    gid, window_key=chi_pending.get("key")),
                "response_chi",
                deadline=chi_pending.get("deadline_mono"),
            )
            chi_pending["demand_generation"] = demand.generation
        elif request_kind == "RESYNC":
            demand.submit_resync(cause="loop_resync")
        elif request_kind in ("WINDOW_PENG", "WINDOW_CHI"):
            # The detailed reason normally came from window_confirm or
            # chi_pending.  Keep a structured phase even when a legacy branch
            # has already cleared its local wait-state.
            demand.submit_window_confirm(
                None,
                "response_peng" if request_kind == "WINDOW_PENG"
                else "response_chi",
                deadline=deadline,
            )
        else:
            demand.submit_sse(seq)
        plan = demand.start_request(
            default_seq=seq,
            default_kind=(RESYNC if request_kind == "RESYNC"
                          else WINDOW_CONFIRM
                          if request_kind in ("WINDOW_PENG", "WINDOW_CHI")
                          else SSE_DELTA),
            deadline=deadline,
        )
        return plan

    @staticmethod
    def _demand_window_status(outcome):
        if outcome == "confirmed":
            return SATISFIED
        # ``unconfirmed`` means the authoritative response did not expose
        # enough deadline/identity facts to authorize the old action.  The
        # logical window is terminal for this attempt; a later explicit
        # window event can create a fresh WINDOW_CONFIRM reason.  The
        # coordinator still supports PENDING for responses that are known to
        # be the same, not-yet-advanced window.
        return DEMAND_TERMINAL

    def _reconcile_state_demand(self, demand, plan, res, *, snap=None,
                                confirm_outcome=None):
        """Apply reason-specific completion; generation alone never retries."""
        if plan is None:
            return False
        demand.reconcile(
            res.get("seq") if isinstance(res, dict) else None,
            response_mode=plan.mode,
            snapshot=snap,
        )
        if confirm_outcome is not None:
            demand.finish_window_confirm(
                self._demand_window_status(confirm_outcome))
        elif plan.kind == WINDOW_CONFIRM and isinstance(snap, dict):
            reasons = demand.reasons
            window_reason = reasons.get(WINDOW_CONFIRM) or {}
            expected_phase = window_reason.get("phase")
            actual_phase = snap.get("phase")
            if expected_phase and actual_phase == expected_phase:
                demand.finish_window_confirm(SATISFIED)
            elif actual_phase and actual_phase != expected_phase:
                demand.finish_window_confirm(DEMAND_TERMINAL)
        return demand.has_pending

    def _state(self, gid, seq, deadline, request_kind=None, plan=None):
        """向真实 Api 传截止；request_kind 留在逻辑请求日志中。"""
        from .api import Api
        if isinstance(self.api, Api):
            kwargs = {"deadline": deadline}
            if plan is not None:
                kwargs.update({
                    "logical_request_id": plan.logical_request_id,
                    "reason": list(plan.reasons),
                    "generation": plan.started_generation,
                })
            return self.api.game_state(gid, seq, **kwargs)
        return self.api.game_state(gid, seq)

    def _record_state_transport(self, attempts, transport):
        """收到 state 返回后立即汇总物理尝试/429，避免动作覆盖 TLS。"""
        if isinstance(transport, dict):
            physical = transport.get("state_physical_attempts")
            if physical is None:
                physical = transport.get("attempts")
            retry_429 = transport.get("state_429")
            if retry_429 is None:
                retry_429 = transport.get("retry_429", 0)
        else:
            physical = attempts
            retry_429 = 0
        try:
            physical = int(physical) if physical is not None else 1
        except (TypeError, ValueError):
            physical = 1
        try:
            retry_429 = int(retry_429 or 0)
        except (TypeError, ValueError):
            retry_429 = 0
        with self._stats_lock:
            self.stats["state_attempts"] += max(physical, 0)
            self.stats["state_retry_429"] += max(retry_429, 0)

    @staticmethod
    def _record_state_req(rec, gid, seq, status, latency_ms, attempts,
                          transport, throttle, request_kind, summary=None,
                          plan=None, demand=None, requested_seq=None):
        """调用新旧 Recorder.req，给旧 fake 保持可选字段兼容。"""
        kwargs = {"transport": transport, "throttle": throttle}
        if summary is not None:
            kwargs["summary"] = summary
        try:
            params = inspect.signature(rec.req).parameters
        except (TypeError, ValueError):
            params = {}
        if ("request_kind" in params
                or any(p.kind == inspect.Parameter.VAR_KEYWORD
                       for p in params.values())):
            kwargs["request_kind"] = request_kind
        if plan is not None and (
                "logical_request_id" in params
                or any(p.kind == inspect.Parameter.VAR_KEYWORD
                       for p in params.values())):
            kwargs.update({
                "logical_request_id": plan.logical_request_id,
                "attempt_index": plan.attempt_index,
                "reason": list(plan.reasons),
                "generation": plan.started_generation,
            })
        if demand is not None and (
                "demand" in params
                or any(p.kind == inspect.Parameter.VAR_KEYWORD
                       for p in params.values())):
            kwargs["demand"] = demand.request_snapshot()
        if requested_seq is not None and (
                "requested_seq" in params
                or any(p.kind == inspect.Parameter.VAR_KEYWORD
                       for p in params.values())):
            kwargs["requested_seq"] = requested_seq
        rec.req(gid, seq, status, latency_ms, attempts, **kwargs)

    @staticmethod
    def _record_end(rec, gid, reason, **kwargs):
        try:
            params = inspect.signature(rec.end).parameters
        except (TypeError, ValueError):
            params = {}
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in params.values())
        if not accepts_kwargs:
            kwargs = {key: value for key, value in kwargs.items()
                      if key in params}
        rec.end(gid, reason, **kwargs)

    def _record_throttle(self, ticket):
        if ticket is None:
            return
        with self._stats_lock:
            if ticket["queue_wait_ms"] > 0:
                self.stats["throttle_waits"] += 1
                self.stats["throttle_wait_ms"] += ticket["queue_wait_ms"]
                self.stats["throttle_wait_ms_max"] = max(
                    self.stats["throttle_wait_ms_max"], ticket["queue_wait_ms"])
            if ticket["deadline_missed"]:
                self.stats["deadline_missed"] += 1

    def _record_demand_metrics(self, demand, seen):
        """Accumulate coordinator counters without double counting games."""
        snapshot = demand.request_snapshot()
        keys = ("logical_demands", "coalesced_demands",
                "physical_state_requests", "suppressed_duplicates")
        with self._stats_lock:
            for key in keys:
                current = int(snapshot.get(key) or 0)
                previous = int(seen.get(key) or 0)
                if current > previous:
                    self.stats[key] += current - previous
                seen[key] = current
            logical = self.stats["logical_demands"]
            physical = self.stats["physical_state_requests"]
            self.stats["coalescing_ratio"] = (
                1.0 - physical / logical if logical else None)

    @staticmethod
    def _state_summary(res):
        if not isinstance(res, dict):
            return None
        return {"n_events": len(res.get("events") or []),
                "pending": bool(res.get("pending")),
                "gap": bool(res.get("gap")),
                "snapshot": res.get("snapshot") is not None,
                "finished": bool(res.get("finished")),
                "seq": res.get("seq")}

    def _mirror_from_snapshot(self, snap):
        mirror = Mirror(my_seat=snap["seat"], dealer=snap.get("dealer", 0),
                       base=self.base,
                       you_cai_bi_kao=self.you_cai_bi_kao,
                       round_no=snap.get("round_no", 1))
        mirror._game_id = snap.get("game_id")
        mirror.apply_snapshot(snap)  # 全量锚定(手牌/公共状态/墙长)
        return mirror

    def _act_on_snapshot(self, mirror, snap, gid, responded_keys=None,
                         attempted_keys=None, allow_peng=True):
        """快照驱动的决策兜底(开局直抽/409 重建后的窗口)。

        response_peng 快照不能直接丢弃：若本人只需过碰窗，返回吃窗等待态，
        由主循环在碰窗截止后继续处理。response_chi 快照则立即进入吃窗决策。
        """
        responded_keys = responded_keys or set()
        attempted_keys = attempted_keys or set()
        seat = snap.get("seat", -1)
        phase = snap.get("phase")
        if seat < 0:
            return None
        if phase == "draw" and snap.get("turn") == seat:
            self._act_draw(mirror, gid)
            return None
        if phase == "response_peng" and allow_peng \
                and seat in (snap.get("responding_seats") or []):
            if self._snapshot_deadline(snap) is None:
                # 快照缺少截止时不能把未完成的确认变成无界 POST。
                return None
            key = self._window_key(mirror, "response_peng", snap=snap)
            if self._window_was_attempted(mirror, key, attempted_keys):
                # 服务端 responding_seats 不保证只包含尚未响应者。
                # 重复快照不得再决策 peng；后续 chi 仍须独立权威确认。
                return self._make_chi_pending(
                    mirror, snap=snap, phase_hint=phase,
                    passed_explicitly=(self._strong_window_key(key)
                                      and key in responded_keys),
                    responded_keys=responded_keys, attempted_keys=attempted_keys)
            return self._act_window(mirror, snap, gid)
        if phase == "response_chi" \
                and seat in (snap.get("responding_seats") or []):
            if self._snapshot_deadline(snap) is None:
                self._state_abandon(gid, "response_chi", "快照缺少有效窗口截止")
                return None
            chi = self._make_chi_pending(
                mirror, snap=snap, responded_keys=responded_keys,
                attempted_keys=attempted_keys, phase_hint="response_chi")
            if chi is not None:
                self._act_chi(mirror, chi, gid)
            return None
        return None

    def _skip_drifted(self, mirror, gid, phase):
        """手牌张数漂移(hu_failed 等服务端异常):本回合交服务端代打。"""
        self._log(f"手牌张数 {sum(mirror.my_hand)} 与阶段 {phase} 不符,"
                  f"本回合交服务端代打")
        with self._stats_lock:
            self.stats["auto_played"] += 1

    def _act_draw(self, mirror, gid, ev=None):
        """自家弃牌回合(摸牌后或吃碰后)。

        ev = 触发事件(自家摸牌/吃碰回声):锚定服务端 ts 的弃牌窗
        守卫——窗已关(观测迟到)则不再提交,交服务端代打(提交必
        409,回声会同步镜像手牌,无漂移)。
        """
        if ev is not None and ev.get("ts") is not None:
            if ev["ts"] + DISCARD_SEC - time.time() <= SUBMIT_EPS:
                self._auto_played(gid, "弃牌窗超时被代打")
                return
        if not mirror.hand_count_ok("draw"):
            self._skip_drifted(mirror, gid, "draw")
            return
        try:
            g = mirror.build_game("draw")
        except MirrorInconsistent as e:
            self._log(f"弃牌决策构建失败: {e}")
            return
        act = self._decide_logged(g, mirror, "draw", gid)
        if ev is not None and ev.get("ts") is not None \
                and ev["ts"] + DISCARD_SEC - time.time() <= SUBMIT_EPS:
            self._deadline_abandon(gid, "draw", "弃牌窗在决策后已关闭")
            return
        self._submit(mirror, gid, act, "draw")

    def _act_window(self, mirror, ev, gid, phase=None):
        """他家弃牌的碰窗(含明杠);返回吃窗等待态(仅出牌者下家)。

        碰窗立即决策(有碰/明杠选项时);吃窗登记起始时刻与碰窗响应
        名单,由主循环在「响应观测齐 或 截止到达」时触发 _act_chi。
        抓打圈中被冻座位不参与任何反应窗。
        """
        if mirror.freeze > 0 and mirror.me != mirror.freezer:
            return None  # 抓打圈:不能吃碰明杠
        if not mirror.hand_count_ok("response_peng"):
            self._skip_drifted(mirror, gid, "response")
            return None
        try:
            g = mirror.build_game("response_peng")
            claims = [a for a in g.legal_actions() if a != -1]
        except MirrorInconsistent as e:
            self._log(f"碰窗构建失败: {e}")
            return None
        passed_explicitly = False
        if claims:
            # A catch-play discard can arrive before the incremental mirror
            # knows which seat owns the freeze.  Do not infer that every
            # locally legal peng is currently actionable: ask for one
            # authoritative response_peng snapshot, then let
            # responding_seats + exact deadline decide.  This is deliberately
            # limited to the protocol's explicit catch_play marker so normal
            # discard latency and request volume remain unchanged.
            event_data = (ev or {}).get("data") or {}
            if event_data.get("catch_play") and "phase" not in (ev or {}):
                stamp = self._epoch_seconds((ev or {}).get("ts"))
                raise _WindowConfirm(
                    phase="response_peng", pending=mirror.pending,
                    round_no=mirror.round_no, legal=claims,
                    source_seq=self._explicit_source_seq(ev), source_ts=stamp,
                    source_watermark=(ev or {}).get("seq"),
                    schedule_deadline=self._confirm_schedule_deadline(stamp),
                    reason="catch_play_confirmation")
            # 秒级 ts 的 T+1 只是最早可能关闭点：临界时确认快照，
            # 不把估计当成已超时，也不盲发迟到的动作。
            stamp = self._epoch_seconds((ev or {}).get("ts"))
            if (self._snapshot_deadline(ev) is None and stamp is not None
                    and stamp + WINDOW_SEC - time.time() <= SUBMIT_EPS):
                # 尚未完成策略决策，不能判定为“策略想做”。
                raise _WindowConfirm(
                    phase="response_peng", pending=mirror.pending,
                    round_no=mirror.round_no, legal=claims,
                    source_seq=self._explicit_source_seq(ev), source_ts=stamp,
                    source_watermark=(ev or {}).get("seq"),
                    schedule_deadline=self._confirm_schedule_deadline(stamp))
            act = self._decide_logged(g, mirror, "response_peng", gid)
            self._mark_window_decision(mirror, "response_peng", act)
            if act != -1:  # 碰/明杠:窗口开启期间立即提交
                # 只有快照精确截止可直接判定本地放弃。
                deadline = self._mono_deadline(self._snapshot_deadline(ev))
                if (deadline is None and stamp is not None
                        and stamp + WINDOW_SEC - time.time() <= SUBMIT_EPS):
                    raise _WindowConfirm(
                        phase="response_peng", pending=mirror.pending,
                        round_no=mirror.round_no, legal=claims,
                        source_seq=self._explicit_source_seq(ev), source_ts=stamp,
                        source_watermark=(ev or {}).get("seq"),
                        chosen=act,
                        schedule_deadline=self._confirm_schedule_deadline(stamp),
                        reason="decision_boundary")
                if deadline is not None and deadline - time.monotonic() <= SUBMIT_EPS:
                    self._deadline_abandon(
                        gid, "response_peng", "碰窗精确截止已到",
                        legal=claims, chosen=act, mirror=mirror,
                        deadline=deadline)
                    return None
                self._submit(mirror, gid, act, "response_peng", deadline=deadline,
                             legal=claims)
                return None
            self._submit(mirror, gid, -1, "response_peng")
            passed_explicitly = True
        # 吃窗:仅出牌者的下家;碰窗响应齐或截止到达后提交。
        # 使用统一构造器，快照中的 window_deadline_ms 优先于事件 ts。
        chi = self._make_chi_pending(
            mirror, ev=ev, snap=ev if ev and "phase" in ev else None,
            passed_explicitly=passed_explicitly,
            peng_end_epoch=None)
        if chi is not None:
            # 本窗我方碰/杠选项(空 = 碰窗与本人无关 → 主循环提前抓吃窗快照)
            chi["peng_claims"] = claims
        return chi

    def _mark_window_attempt(self, mirror, phase, attempted_windows):
        key = self._window_key(mirror, phase)
        if key is not None:
            attempted_windows.add(key)
        return key

    def _act_chi(self, mirror, chi, gid):
        """吃窗决策：生产主循环仅由 response_chi 权威快照调用。

        提交前复查精确截止；保留旧等待态字段供离线时序测试使用。
        """
        t0, obs = chi["t0"], chi.get("obs", chi["t0"])
        anchored = chi.get("anchored", True)
        if not mirror.hand_count_ok("response_chi"):
            self._skip_drifted(mirror, gid, "response_chi")
            return
        try:
            g = mirror.build_game("response_chi")
            chi_opts = [a for a in g.legal_actions() if a != -1]
        except MirrorInconsistent as e:
            self._log(f"吃窗构建失败: {e}")
            return
        if not chi_opts:
            return
        act = self._decide_logged(g, mirror, "response_chi", gid)
        self._mark_window_decision(mirror, "response_chi", act)
        if act == -1:
            return
        # 吃窗守卫:超过锚定 T+2+eps(错过关闭点,物理上来不及)则不
        # 提交——仅在确有吃意图且 t0 锚定服务端 ts 时生效(测试桩无
        # ts 走旧语义);锚点偏早使守卫偏保守方向,不误杀可成提交
        ready = chi.get("ready_mono", max(obs, t0) + self.window_wait + 0.05)
        deadline = chi.get("deadline_mono")
        if deadline is None and anchored:
            deadline = t0 + self.window_wait * 2
        now = time.monotonic()
        if deadline is not None and now >= deadline - SUBMIT_EPS:
            self._deadline_abandon(gid, "response_chi", "吃窗截止前已无提交余量",
                                   legal=chi_opts, chosen=act, mirror=mirror,
                                   deadline=deadline)
            return
        wait = ready - now
        if wait > 0:
            time.sleep(wait)
        now = time.monotonic()
        if deadline is not None and now >= deadline - SUBMIT_EPS:
            self._deadline_abandon(gid, "response_chi", "吃窗在等待后已关闭",
                                   legal=chi_opts, chosen=act, mirror=mirror,
                                   deadline=deadline)
            return
        self._submit(mirror, gid, act, "response_chi", deadline=deadline,
                     window_key=chi.get("key"), legal=chi_opts)

    def _submit(self, mirror, gid, act, phase, deadline=None, window_key=None,
                legal=None):
        key = None
        action_attempt_index = 1
        logical_action_id = None
        if phase in ("response_peng", "response_chi"):
            current_key = self._window_key(mirror, phase)
            key = current_key
            # A chi wait-state was keyed from the original discard event.
            # Keep that source identity across a seq=0 rebuild, but only if
            # the rebuilt mirror still points at the same round/seat/tile;
            # never let an old wait-state authorize a new pending discard.
            if (window_key is not None and current_key is not None
                    and self._same_window_identity(window_key, current_key)):
                key = window_key
            attempted = getattr(mirror, "_attempted_windows", None)
            if self._window_was_attempted(mirror, key, attempted):
                self._claim_miss(
                    gid, phase, legal or self._claim_legal(mirror, phase),
                    chosen=act, reason="window_already_attempted",
                    mirror=mirror, deadline=deadline)
                return False
            if attempted is not None and self._strong_window_key(key):
                attempted.add(key)
            elif isinstance(key, WindowAttemptKey):
                legacy_attempts = getattr(mirror, "_legacy_attempts", None)
                if legacy_attempts is not None:
                    legacy_attempts[key] = getattr(
                        mirror, "_legacy_epoch", None)
            if isinstance(key, WindowAttemptKey):
                indices = getattr(mirror, "_window_attempt_indices", None)
                if indices is None:
                    indices = {}
                    mirror._window_attempt_indices = indices
                action_attempt_index = indices.get(key, 0) + 1
                indices[key] = action_attempt_index
                logical_action_id = (
                    f"window:{gid}:{repr(key.as_tuple())}")
        if logical_action_id is None:
            counter = getattr(mirror, "_action_attempt_counter", 0) + 1
            mirror._action_attempt_counter = counter
            logical_action_id = f"action:{gid}:{counter}"
        window_log = {}
        if isinstance(key, WindowAttemptKey):
            window_log = {
                "window_id": key.window_id.as_json(),
                "window_attempt_key": key.as_json(),
                "identity_status": key.window_id.identity_status,
            }
        payload = action_to_payload(
            act, mirror.pending[1] if mirror.pending else None)
        t0 = time.monotonic()
        start_epoch = time.time()
        deadline_epoch = (None if deadline is None else
                          start_epoch + deadline - t0)
        try:
            from .api import Api
            if isinstance(self.api, Api):
                self.api.game_action(gid, payload, deadline=deadline)
            else:
                self.api.game_action(gid, payload)
        except ApiError as e:
            if (phase in ("response_peng", "response_chi")
                    and isinstance(key, WindowAttemptKey)
                    and not self._strong_window_key(key)):
                failed = getattr(mirror, "_legacy_failed", None)
                if failed is not None:
                    failed.add(key)
            if phase in ("response_peng", "response_chi") and act != -1:
                claim_legal = (legal if legal is not None else
                               self._claim_legal(mirror, phase))
                self._claim_miss(
                    gid, phase, claim_legal, chosen=act,
                    reason="action_uncertain" if getattr(e, "uncertain", False)
                    else "action_rejected", payload=payload,
                    status=e.status, code=e.code, deadline=deadline,
                    mirror=mirror)
            if self.recorder is not None:
                self.recorder.action(gid, phase, payload, ok=False,
                                     status=e.status, code=e.code,
                                     latency_ms=_ms(t0), started_at=t0,
                                     started_epoch=start_epoch,
                                     deadline_at=deadline_epoch,
                                     message=getattr(e, "message", ""),
                                     attempts=self._attempts(),
                                     transport=self._transport(),
                                     logical_request_id=logical_action_id,
                                     attempt_index=action_attempt_index,
                                     **window_log)
            if e.status == 409:
                with self._stats_lock:
                    self.stats["err409"] += 1
                    self.stats["response_409"] += 1
                self._log(f"409(动作竞态/失步): {payload}")
            if getattr(e, "uncertain", False):
                with self._stats_lock:
                    self.stats["post_uncertain"] += 1
            # 明确拒绝和传输结果未知都必须重锚；绝不在旧镜像上继续决策。
            raise _ActionResync(
                f"{type(e).__name__} status={e.status} code={e.code}",
                status=e.status, uncertain=getattr(e, "uncertain", False))
        if self.recorder is not None:
            self.recorder.action(gid, phase, payload, ok=True,
                                 latency_ms=_ms(t0), started_at=t0,
                                 started_epoch=start_epoch,
                                 deadline_at=deadline_epoch,
                                 attempts=self._attempts(),
                                 transport=self._transport(),
                                 logical_request_id=logical_action_id,
                                 attempt_index=action_attempt_index,
                                 **window_log)
        with self._stats_lock:
            self.stats["actions"] += 1
            if payload["action"] == "hu":
                self.stats["hu"] += 1
        return True

    def _action_recovery(self, reason):
        """动作失败后由外层循环 seq=0 重建，不重复提交旧动作。"""
        raise _ActionResync(reason)
