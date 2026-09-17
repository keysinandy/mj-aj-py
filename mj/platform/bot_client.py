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

from .api import Api, ApiError
from .mirror import Mirror, MirrorInconsistent
from .actions import action_to_payload
from .proto import API_NAME, parse_event, tidx
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
from .state_fetch import StateFetchCoordinator
from .state_scheduler import StateScheduler
from .tournament import (LIFECYCLE_STATES, TERMINAL_STATES,
                         TournamentContext, TournamentRules)
from .security import redact_exception, redact_text
from .window_confirmation import WindowConfirmation, WindowTiming
from ..replay_debugger.model import stable_id

# Keep the legacy tuple/order for test-room and free-match callers; formal
# lifecycle membership uses the shared TERMINAL_STATES set below.
TERMINAL = ("finished", "closed", "void")
FORMAL_POLL_INTERVAL = 1.0
FORMAL_RETRY_MAX = 8.0
WINDOW_SEC = 1.0  # 碰/吃窗口固定走满时长(提交吃牌须等碰窗结束)
DISCARD_SEC = 3.0
DEADLINE_MARGIN = 0.12  # 为模型串行决策与动作提交预留的本地安全余量
SUBMIT_EPS = 0.05  # 窗口守卫余量:仅剩此余量时物理上来不及提交才放弃
LAZY_POLL_WAIT = 1.2  # 懒轮询:预测无关事件的最长推迟(吃窗 T+2 内须追平)
EAGER_CHI_LEAD = 0.25  # 吃窗快照提前量:临近开窗才抓,避免长时间空占 EDF
EAGER_CHI_GAP = 0.12   # 同一吃窗两次快照抓取的最小间隔(限速 ~8/s)
EAGER_CHI_MAX = 8      # 同一吃窗最多抓取次数(无截止/相位不推进时的兜底界)
MAX_WINDOW_CONFIRM_PENDING_RETRIES = 8
# window-snapshot-identity-decision: deadline-driven confirmation budget.
# Another WINDOW_CONFIRM pull is worth scheduling only while the remaining
# window time still affords the round trip plus the decide+POST margin;
# below that the pending action path decides under the weak epoch key.
CONFIRM_PULL_MARGIN = 0.3   # one /state confirm round-trip estimate
WEAK_DECIDE_MARGIN = 0.12  # decide + POST margin (mirrors DEADLINE_MARGIN)


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
                 schedule_deadline=None, window_key=None,
                 source_origin=None,
                 first_seen_via=None,
                 not_before=None,
                 revision=None,
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
        self.source_origin = source_origin
        self.first_seen_via = first_seen_via
        # Unknown identity / missing exact deadline is retryable, but it must
        # have a bounded lifetime.  A supplied schedule deadline remains the
        # primary bound; the fallback is initialized lazily so fake clocks in
        # tests and callers constructing this object early remain consistent.
        self.retry_deadline = schedule_deadline
        self.pending_retries = 0
        self.observation_budget_exhausted = False
        self.requested_at = None
        # A chi wait-state carries the same logical WindowAttemptKey as the
        # preceding peng phase.  Keeping it on the confirmation object lets
        # both phases use one identity/deadline resolver.
        self.window_key = window_key
        self.reason = reason
        self.confirmation = WindowConfirmation(
            window_key,
            phase,
            timing=WindowTiming(
                not_before=not_before,
                scheduler_deadline=schedule_deadline,
                observation_budget_deadline=self.retry_deadline,
                not_before_source=("chi_ready" if not_before is not None
                                   else "unknown"),
                scheduler_deadline_source="window_schedule",
                observation_budget_source="legacy_retry_deadline",
                exact_deadline_source="authoritative_snapshot"),
            pending=self.pending,
            revision=revision,
            pending_retries=0,
        )


def _ms(t0):
    """monotonic 差值 → 毫秒(不受时钟跳变影响)。"""
    return round((time.monotonic() - t0) * 1000.0, 1)


def _compact_evaluation(evaluation, limit=3):
    """Keep live decision logs bounded without changing the chosen action.

    Offline callers retain the complete ``HandEvaluation`` object.  A live
    record contains the selected candidate, the legacy-best candidate and at
    most ``limit`` additional candidates, together with an explicit count so
    logview never mistakes a compact explanation for a full search.
    """
    if evaluation is None:
        return None
    try:
        from mj.decision.report import sanitize_public
        evaluation = sanitize_public(evaluation)
    except Exception:
        # Logging must never affect an already selected action.  The existing
        # conversion below remains the compatibility fallback.
        pass
    data = (evaluation.as_json() if hasattr(evaluation, "as_json")
            else dict(evaluation) if isinstance(evaluation, dict)
            else evaluation)
    if not isinstance(data, dict):
        return data
    data = dict(data)
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return data
    data["candidate_count"] = len(candidates)
    keep = []
    selected = data.get("best_discard", data.get("selected"))
    if isinstance(data.get("selected"), dict):
        selected = data["selected"].get("tile",
                                      data["selected"].get("action"))
    legacy = data.get("legacy_best")
    for i, item in enumerate(candidates):
        ident = item.get("tile", item.get("action")) if isinstance(item, dict) else None
        if ((ident is not None and ident in (selected, legacy)) or
                (isinstance(item, dict) and item.get("selected"))):
            if i not in keep:
                keep.append(i)
    max_total = len(keep) + max(0, int(limit))
    for i in range(len(candidates)):
        if i not in keep and len(keep) < max_total:
            keep.append(i)
    if len(keep) < len(candidates):
        data["candidates_truncated"] = True
        data["candidates"] = [candidates[i] for i in keep]
    return data


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
        # 场次),首次低于 16/s 限速(与服务端墙同值)。SSE 在场会禁用
        # 挂起(3 房日志 0 次 pending),故本模式不兼容 use_notify
        self.long_poll = long_poll and not use_notify
        self._tid = None
        self._user_id = None
        self._tournament_stop = None
        self.you_cai_bi_kao = False
        self.base = 1
        self.tournament_m = None
        self.tournament_rounds = None
        self.tournament_rules = None
        self.tournament_context = None
        self.tournament_status = None
        self.tournament_stage = None
        self.tournament_qualified = None
        self.tournament_stage_history = []
        self.tournament_termination_reason = None
        self.tournament_warnings = []
        self.tournament_diagnostic = {}
        self._formal_attended = set()
        self._formal_attendance_lost = set()
        self._formal_attendance_attempts = {}
        self._formal_stage_counter = 0
        self._formal_last_status = None
        self._formal_last_stage_key = None
        self._formal_crash_warnings = set()
        self._formal_poll_interval = FORMAL_POLL_INTERVAL
        self._formal_retry_max = FORMAL_RETRY_MAX
        self._decide_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._done_games = set()
        self._game_fails = {}  # gid → 连续异常次数(超限放弃重派)
        self._state_abandon_seen = set()
        self._action_counter_lock = threading.Lock()
        self._action_counters = {}
        self._window_attempt_counters = {}
        self._window_confirm_attempts = {}
        # Runtime lifecycle facts are keyed by a phase-specific logical key;
        # the recorder remains the durable source, while this small ledger
        # prevents late timeout/snapshot callbacks from reopening a closed
        # attempt in the live worker.
        self._window_lifecycle_lock = threading.RLock()
        self._window_lifecycles = {}
        self._uncertain_recoveries = {}
        # A token's Api owns one StateThrottle.  Reuse one scheduler facade
        # for every game worker so candidate admission and transport retries
        # share that same physical permit queue.
        self._state_scheduler = (
            StateScheduler(api.state_throttle)
            if isinstance(api, Api) and api.state_throttle is not None
            else None)
        self.stats = {
            "games": 0, "actions": 0, "hu": 0, "err409": 0, "gaps": 0,
            "auto_played": 0, "mirror_resets": 0, "mirror_drift_resets": 0,
            "state_drift_auto_played": 0,
            "scores": [],
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
            # window-snapshot-identity-decision: 弱键决策/授权计数,与
            # authoritative open 分开,线上对账时可单独归因。
            "window_confirm_weak_open": 0,
            "weak_key_decisions": 0,
            "window_confirm_closed": 0,
            "window_confirm_stale": 0,
            "window_confirm_unconfirmed": 0,
            "window_confirm_miss": 0,
            "confirmation_observation_budget_exhausted": 0,
            # /state 传输诊断在收到响应后立即累计，避免后续动作覆盖
            # Api TLS 上下文。
            "state_attempts": 0,
            "state_retry_429": 0,
            "logical_demands": 0,
            "logical_input_demands": 0,
            "coalesced_demands": 0,
            "successor_requests": 0,
            "physical_state_requests": 0,
            "logical_state_requests": 0,
            "physical_state_attempts": 0,
            "substituted_candidates": 0,
            "cancelled_before_send": 0,
            "suppressed_duplicates": 0,
            "coalesced_or_suppressed": 0,
            "coalescing_ratio": None,
        }

    # ---------- 生命周期(监督线程) ----------

    def run(self, max_games=None, stop=None):
        if self.mode == "tournament":
            return self._run_formal_tournament(max_games=max_games, stop=stop)
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

    # ---------- 正式锦标赛生命周期 ----------

    def configure_tournament(self, context):
        """Install a token-scoped context prepared by the formal runner."""
        if not isinstance(context, TournamentContext):
            raise TypeError("context 必须是 TournamentContext")
        self.tournament_context = context
        self._tid = context.tournament_id
        self._user_id = context.user_id
        if context.rules is not None:
            self._apply_tournament_rules(context.rules)

    def _apply_tournament_rules(self, rules):
        if not isinstance(rules, TournamentRules):
            rules = TournamentRules.from_response(rules)
        self.tournament_rules = rules
        self.you_cai_bi_kao = rules.you_cai_bi_kao
        self.base = rules.base_score
        self.tournament_m = rules.m
        self.tournament_rounds = rules.rounds
        # Only fields with an explicit client meaning are consumed.  Unknown
        # fields stay in neither the BotClient defaults nor global state.
        for key in ("WindowSec", "ResponseWindowSec", "window_sec",
                    "response_window_sec"):
            value = rules.config.get(key)
            if isinstance(value, (int, float)) and value > 0:
                self.window_wait = float(value)
                break

    @staticmethod
    def _formal_auth_error(exc):
        return isinstance(exc, ApiError) and exc.status in (401, 403)

    @staticmethod
    def _formal_transient_error(exc):
        if isinstance(exc, (TimeoutError, OSError, ConnectionError)):
            return True
        if not isinstance(exc, ApiError):
            return False
        return exc.status in (0, 408, 425, 429) or 500 <= exc.status < 600

    def _formal_log_error(self, prefix, exc):
        safe = redact_exception(exc, [getattr(self.api, "token", None)])
        self._log(f"{prefix}: {safe.get('type')}: {safe.get('message')}")

    def _formal_close_recorder(self):
        if self.recorder is None:
            return
        try:
            self.recorder.close_all()
        except Exception:
            pass

    def _shutdown_requested(self):
        return (self._tournament_stop is not None
                and self._tournament_stop.is_set())

    def _formal_fetch_me(self, stop):
        delay = 0.5
        while True:
            if stop is not None and stop.is_set():
                raise RuntimeError("INTERRUPTED")
            try:
                return self.api.me()
            except Exception as exc:
                if self._formal_auth_error(exc):
                    raise
                if not self._formal_transient_error(exc):
                    raise
                self._formal_log_error("me 暂时失败", exc)
                if self._sleep_stop(delay, stop):
                    raise RuntimeError("INTERRUPTED")
                delay = min(self._formal_retry_max, delay * 2.0)

    def _formal_resolve_context(self, stop):
        context = self.tournament_context
        if context is None:
            me = self._formal_fetch_me(stop)
            context = TournamentContext.from_me(
                token_label=self.name,
                server=getattr(self.api, "base", ""),
                response=me)
        if not context.tournament_id:
            raise RuntimeError("TOKEN_NOT_BOUND")
        self.configure_tournament(context)
        if self.tournament_rules is not None:
            return context

        delay = 0.5
        while True:
            if stop is not None and stop.is_set():
                raise RuntimeError("INTERRUPTED")
            try:
                rules = TournamentRules.from_response(self.api.rules())
                self._apply_tournament_rules(rules)
                self.tournament_context = TournamentContext(
                    token_label=context.token_label,
                    server=context.server,
                    tournament_id=context.tournament_id,
                    user_id=context.user_id,
                    active_games=context.active_games,
                    rules=rules)
                return self.tournament_context
            except Exception as exc:
                if self._formal_auth_error(exc):
                    raise
                if not self._formal_transient_error(exc):
                    raise
                self._formal_log_error("rules 拉取失败", exc)
                if self._sleep_stop(delay, stop):
                    raise RuntimeError("INTERRUPTED")
                delay = min(self._formal_retry_max, delay * 2.0)

    @staticmethod
    def _formal_scalar(value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            for key in ("id", "stage_id", "stage", "number", "no"):
                if key in value:
                    return BotClient._formal_scalar(value[key])
        return str(value)

    def _formal_stage_key(self, tournament, status):
        """Return a stable stage key without using poll count as identity."""
        for source_name, source in (("top", tournament),
                                    ("stage", tournament.get("stage")),
                                    ("ranking", tournament.get("ranking"))):
            if not isinstance(source, dict):
                continue
            for key in ("stage_id", "stageId", "stage_no", "stageNo",
                        "stage_index", "stageIndex", "stage", "round_no",
                        "roundNo", "round", "id"):
                if key in source and source[key] is not None:
                    return (source_name, key,
                            self._formal_scalar(source[key]))
        # When the fixture/server omits a stage identifier, keep one fallback
        # key across registering -> stage_open -> running -> stage_done.  Only
        # a stage_open observed after stage_done starts a new attendance
        # boundary; poll count is never used as identity.
        if self._formal_stage_counter == 0:
            self._formal_stage_counter = 1
        elif status == "stage_open" and self._formal_last_status == "stage_done":
            self._formal_stage_counter += 1
        return ("edge", self._formal_stage_counter)

    def _formal_record_stage(self, tournament, status, stage_key):
        self.tournament_status = status
        self.tournament_stage = stage_key
        qualified = tournament.get("qualified")
        self.tournament_qualified = qualified
        transition = {
            "status": status,
            "stage": stage_key,
            "qualified": qualified,
            "stage_crashed": bool(tournament.get("stage_crashed", False)),
        }
        if (not self.tournament_stage_history
                or self.tournament_stage_history[-1] != transition):
            self.tournament_stage_history.append(transition)

    def _formal_attend(self, tid, stage_key):
        if stage_key in self._formal_attended:
            return
        # Reserve the key before making requests: a repeated poll or a
        # TOURNAMENT_STARTED race cannot create an unbounded POST loop.
        self._formal_attended.add(stage_key)
        self._formal_attendance_attempts[stage_key] = (
            self._formal_attendance_attempts.get(stage_key, 0) + 1)
        for operation, label in ((self.api.register, "register"),
                                 (self.api.ready, "ready")):
            try:
                operation(tid)
            except ApiError as exc:
                if exc.code == "TOURNAMENT_STARTED":
                    self.tournament_warnings.append({
                        "reason": "TOURNAMENT_STARTED", "stage": stage_key,
                    })
                elif exc.code == "NOT_QUALIFIED":
                    self._formal_attendance_lost.add(stage_key)
                elif self._formal_auth_error(exc):
                    raise
                elif not self._formal_transient_error(exc):
                    self._formal_log_error(f"{label} 失败", exc)
                else:
                    self._formal_log_error(f"{label} 暂时失败", exc)
            except (ConnectionError, OSError, TimeoutError) as exc:
                self._formal_log_error(f"{label} 暂时失败", exc)

    def _formal_active_games(self, tournament):
        me = self.api.me()
        active = me.get("active_games") if isinstance(me, dict) else None
        active_ids = []
        for item in active or ():
            gid = item.get("game_id") if isinstance(item, dict) else item
            if gid:
                active_ids.append(str(gid))
        mine = tournament.get("my_games")
        if not isinstance(mine, (list, tuple, set, frozenset)):
            return []
        allowed = {str(gid) for gid in mine if gid}
        return list(dict.fromkeys(gid for gid in active_ids if gid in allowed))

    def _formal_spawn_games(self, tournament, workers, stop):
        if stop is not None and stop.is_set():
            return
        for gid in self._formal_active_games(tournament):
            if stop is not None and stop.is_set():
                return
            if gid in self._done_games:
                continue
            thread = workers.get(gid)
            if thread is not None and thread.is_alive():
                continue
            thread = threading.Thread(
                target=self._play_game_safe,
                args=(gid,),
                name=f"{self.name}:{gid}", daemon=True)
            workers[gid] = thread
            thread.start()

    def _run_formal_tournament(self, max_games=None, stop=None):
        self._tournament_stop = stop
        self.tournament_termination_reason = None
        self.tournament_status = None
        self.tournament_stage = None
        self.tournament_qualified = None
        self.tournament_stage_history = []
        self.tournament_warnings = []
        self.tournament_diagnostic = {}
        self._formal_attended.clear()
        self._formal_attendance_lost.clear()
        self._formal_attendance_attempts.clear()
        self._formal_crash_warnings.clear()
        self._formal_stage_counter = 0
        self._formal_last_status = None
        self._formal_last_stage_key = None
        self._done_games.clear()
        self._game_fails.clear()
        workers = {}
        retry_delay = 0.5
        try:
            context = self._formal_resolve_context(stop)
        except RuntimeError as exc:
            reason = str(exc)
            if reason not in ("TOKEN_NOT_BOUND", "INTERRUPTED"):
                reason = "PROTOCOL_FATAL"
            self.tournament_termination_reason = reason
            self._formal_close_recorder()
            return self._formal_result_stats()
        except ApiError as exc:
            self.tournament_termination_reason = (
                "AUTH_FAILED" if self._formal_auth_error(exc)
                else "PROTOCOL_FATAL")
            self._formal_log_error("赛事预检失败", exc)
            self._formal_close_recorder()
            return self._formal_result_stats()
        except Exception as exc:
            self.tournament_termination_reason = "PROTOCOL_FATAL"
            self._formal_log_error("赛事预检失败", exc)
            self._formal_close_recorder()
            return self._formal_result_stats()

        tid = context.tournament_id
        try:
            while stop is None or not stop.is_set():
                if max_games is not None and self.stats.get("games", 0) >= max_games:
                    self.tournament_diagnostic = {
                        "max_games_debug": max_games,
                        "warning": "仅调试；正式锦标赛不要使用，会导致提前离赛",
                    }
                    self.tournament_termination_reason = "INTERRUPTED"
                    break
                try:
                    tournament = self.api.tournament(tid)
                    if not isinstance(tournament, dict):
                        raise ValueError("赛事状态响应必须是对象")
                    status = tournament.get("status")
                    if status not in LIFECYCLE_STATES:
                        raise ValueError(f"未知赛事状态: {status!r}")
                    retry_delay = 0.5
                except Exception as exc:
                    if self._formal_auth_error(exc):
                        self.tournament_termination_reason = "AUTH_FAILED"
                        self._formal_log_error("赛事查询认证失败", exc)
                        break
                    if not self._formal_transient_error(exc):
                        self.tournament_termination_reason = "PROTOCOL_FATAL"
                        self._formal_log_error("赛事查询失败", exc)
                        break
                    self._formal_log_error("赛事查询暂时失败", exc)
                    if self._sleep_stop(retry_delay, stop):
                        self.tournament_termination_reason = "INTERRUPTED"
                        break
                    retry_delay = min(self._formal_retry_max, retry_delay * 2.0)
                    continue

                stage_key = self._formal_stage_key(tournament, status)
                self._formal_record_stage(tournament, status, stage_key)
                self._formal_last_status = status
                self._formal_last_stage_key = stage_key

                if status in TERMINAL_STATES:
                    self.tournament_termination_reason = {
                        "finished": "FINISHED", "closed": "CLOSED",
                        "void": "VOID",
                    }[status]
                    break
                if status == "stage_open" and tournament.get("qualified") is False:
                    self.tournament_termination_reason = "ELIMINATED"
                    break
                if status in ("registering", "stage_open"):
                    self._formal_attend(tid, stage_key)
                if status == "stage_done" and tournament.get("stage_crashed"):
                    warning_key = (stage_key, "stage_crashed")
                    if warning_key not in self._formal_crash_warnings:
                        self._formal_crash_warnings.add(warning_key)
                        self.tournament_warnings.append({
                            "reason": "STAGE_CRASHED_WAITING",
                            "stage": stage_key,
                        })
                        self._log("STAGE_CRASHED_WAITING: 等待赛事恢复")
                if status == "running":
                    try:
                        self._formal_spawn_games(tournament, workers, stop)
                    except Exception as exc:
                        if self._formal_auth_error(exc):
                            self.tournament_termination_reason = "AUTH_FAILED"
                        elif self._formal_transient_error(exc):
                            self._formal_log_error("active game 查询暂时失败", exc)
                            if self._sleep_stop(retry_delay, stop):
                                self.tournament_termination_reason = "INTERRUPTED"
                                break
                            retry_delay = min(self._formal_retry_max,
                                              retry_delay * 2.0)
                            continue
                        else:
                            self.tournament_termination_reason = "PROTOCOL_FATAL"
                        if self.tournament_termination_reason:
                            break
                if self._sleep_stop(self._formal_poll_interval, stop):
                    self.tournament_termination_reason = "INTERRUPTED"
                    break
        except KeyboardInterrupt:
            self.tournament_termination_reason = "INTERRUPTED"
        except ApiError as exc:
            self.tournament_termination_reason = (
                "AUTH_FAILED" if self._formal_auth_error(exc)
                else "PROTOCOL_FATAL")
            self._formal_log_error("赛事生命周期失败", exc)
        except Exception as exc:
            self.tournament_termination_reason = "PROTOCOL_FATAL"
            self._formal_log_error("赛事生命周期失败", exc)
        finally:
            if stop is not None and stop.is_set() \
                    and self.tournament_termination_reason is None:
                self.tournament_termination_reason = "INTERRUPTED"
            for thread in workers.values():
                thread.join(timeout=5)
            self._formal_close_recorder()
        return self._formal_result_stats()

    def _formal_result_stats(self):
        stats = dict(self.stats)
        stats.update({
            "token_label": self.name,
            "user_id": self._user_id,
            "tournament_id": self._tid,
            "termination_reason": self.tournament_termination_reason
                                   or "PROTOCOL_FATAL",
            "final_status": self.tournament_status,
            "final_stage": self.tournament_stage,
            "qualified": self.tournament_qualified,
            "stage_transitions": list(self.tournament_stage_history),
            "tournament_warnings": list(self.tournament_warnings),
            "tournament_diagnostic": dict(self.tournament_diagnostic),
        })
        return stats

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
        # Polling mode still carries a StateDemand for lifecycle accounting,
        # but it has no listener queue to wake the worker.  Preserve the old
        # bounded sleep semantics (especially the deterministic fake-clock
        # path when idle_sleep=0) instead of treating an empty demand queue as
        # an immediate wake.
        if wake is None or sse is None:
            if max_wait is None:
                time.sleep(self.idle_sleep)
            elif self.idle_sleep:
                time.sleep(min(self.idle_sleep, max_wait))
            else:
                time.sleep(max_wait)  # idle_sleep=0(测试):睡满截止不热轮询
            return False
        timeout = (self.notify_fallback_wait
                   if sse is not None and sse.get("alive")
                   else self.idle_sleep)
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
        if mirror is not None and phase in ("response_peng", "response_chi"):
            key = self._window_key(mirror, phase)
            if isinstance(key, WindowAttemptKey) and chosen not in (None, -1):
                self._record_window_lifecycle(
                    gid, key, "submit", state="TERMINAL",
                    outcome="CLIENT_LOSS", loss_stage="SUBMIT",
                    loss_reason="submit_exact_deadline_expired"
                    if "截止" in reason or "关闭" in reason
                    else "submit_unknown",
                    decision_action=chosen,
                    exact_deadline_at=self._epoch_from_mono(deadline),
                    submit_finished_at=time.time())
        with self._stats_lock:
            self.stats["client_deadline_abandons"] += 1

    def _claim_miss(self, gid, phase, legal, chosen=None, reason="",
                    payload=None, status=None, code="", deadline=None,
                    mirror=None, seq=None, pending=None, window_key=None,
                    logical_request_id=None, exact_deadline_at=None,
                    action_posted=None):
        """记录规则允许、但客户端未成功完成的吃/碰/杠机会。

        窗口关联字段按证据可得性落盘：调用方显式给出的 window_key/
        logical_request_id 优先（确认链与动作链各有自己的逻辑 id），
        否则从 mirror 派生；两者皆无（如确认作废路径传 mirror=None）
        则不带窗口身份，离线归因只能落 unknown。
        """
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
        window_id_json = None
        window_key_json = None
        if window_key is None and mirror is not None:
            window_key = self._window_key(mirror, phase)
        if isinstance(window_key, WindowAttemptKey):
            window_key_json = window_key.as_json()
            window_id_json = self._window_id_for_demand(
                gid, window_key=window_key).as_json()
            if logical_request_id is None:
                logical_request_id = (
                    f"window:{gid}:{repr(window_key.as_tuple())}")
            if action_posted is None and mirror is not None:
                action_posted = self._window_was_attempted(
                    mirror, window_key,
                    getattr(mirror, "_attempted_windows", None))
        decision_id = None
        if isinstance(window_key, WindowAttemptKey) and mirror is not None:
            decision_id = (getattr(mirror, "_window_decision_ids", {}) or {}
                           ).get(window_key)
        if self.recorder is not None:
            self.recorder.claim_miss(
                gid, phase, sorted(legal), chosen=chosen, reason=reason,
                payload=payload, status=status, code=code,
                deadline_at=deadline_at, seq=seq,
                pending=(pending if pending is not None else
                         (None if mirror is None else mirror.pending)),
                window_id=window_id_json,
                window_attempt_key=window_key_json,
                logical_request_id=logical_request_id,
                exact_deadline_at=exact_deadline_at,
                action_posted=action_posted,
                decision_id=decision_id)
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
        confirm_attempt_index = getattr(confirm, "attempt_index", 0)
        window_id = self._window_id_for_demand(
            gid, confirm=confirm,
            window_key=getattr(confirm, "window_key", None))
        if window_id is None:
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
                identity_origin=(getattr(confirm, "source_origin", None)
                                 or ("explicit_source_field"
                                     if confirm.source_seq is not None
                                     else "legacy_snapshot")),
                first_seen_via=(getattr(confirm, "first_seen_via", None)
                                or "unknown"),
            )
        if logical_confirm_id is None:
            logical_confirm_id = (
                f"window-confirm:{gid}:{confirm.round_no}:"
                f"{repr(window_id.as_tuple())}:{phase}")
            confirm.logical_request_id = logical_confirm_id
        lifecycle_confirmation = getattr(confirm, "confirmation", None)
        if lifecycle_confirmation is not None:
            lifecycle_confirmation.window_attempt_key = WindowAttemptKey(
                window_id, phase)
            lifecycle_confirmation.observe(outcome)
        now_epoch = time.time()
        if outcome == "requested":
            confirm.requested_at = now_epoch
        if outcome == "requested":
            with self._action_counter_lock:
                confirm_attempt_index = max(
                    confirm_attempt_index,
                    self._window_confirm_attempts.get(logical_confirm_id, 0)) + 1
                self._window_confirm_attempts[logical_confirm_id] = (
                    confirm_attempt_index)
            confirm.attempt_index = confirm_attempt_index
        fields = {
            "phase": phase,
            "outcome": outcome,
            "reason": reason or confirm.reason,
            "pending": (list(confirm.pending)
                         if confirm.pending is not None else None),
            "round_no": confirm.round_no,
            "legal": sorted(legal if legal is not None else confirm.legal),
            "source_seq": window_id.source_discard_seq,
            "window_id": window_id.as_json(),
            "window_attempt_key": WindowAttemptKey(window_id, phase).as_json(),
            "identity_status": window_id.identity_status,
            "identity_origin": window_id.identity_origin,
            "first_seen_via": window_id.first_seen_via,
            "logical_request_id": logical_confirm_id,
            "confirm_request_logical_id": logical_confirm_id,
            "confirm_requested_at": getattr(confirm, "requested_at", None),
            "confirm_response_at": now_epoch,
            "attempt_index": confirm_attempt_index or None,
            "generation": getattr(confirm, "generation", None),
            "revision": (getattr(lifecycle_confirmation, "revision", None)
                         if lifecycle_confirmation is not None else None),
            "source_watermark": confirm.source_watermark,
            "seq": seq,
            "source_observed_at": self._epoch_seconds(confirm.source_ts),
            "estimated_deadline_at": self._epoch_from_mono(
                confirm.schedule_deadline),
        }
        if lifecycle_confirmation is not None:
            fields["timing"] = lifecycle_confirmation.timing.as_json()
        if getattr(confirm, "observation_budget_exhausted", False):
            fields["confirmation_observation_budget_exhausted"] = True
        if isinstance(snap, dict):
            fields["exact_deadline_at"] = self._snapshot_deadline(snap)
            if lifecycle_confirmation is not None:
                lifecycle_confirmation.timing.exact_window_deadline = (
                    fields["exact_deadline_at"])
                lifecycle_confirmation.timing.exact_deadline_source = (
                    "authoritative_snapshot"
                    if fields["exact_deadline_at"] is not None else "missing")
            fields["snapshot_phase"] = snap.get("phase")
            fields["responding_seats"] = snap.get("responding_seats") or []
            fields["deadline_left_ms"] = self._deadline_left_ms(
                fields["exact_deadline_at"])
            if outcome in ("open", "confirmed"):
                fields["authorization_snapshot_seq"] = seq
                fields["authoritative_open_at"] = now_epoch
            if lifecycle_confirmation is not None:
                fields["timing"] = lifecycle_confirmation.timing.as_json()
        # JSONL 记录不需要 null 字段，且旧 fake Recorder 可能只接收
        # 非空字段；保留 pending/round 等稳定字段，丢弃未提供项。
        fields = {key: value for key, value in fields.items()
                  if value is not None}
        with self._stats_lock:
            if outcome == "requested":
                self.stats["window_confirm_requests"] += 1
            elif outcome in ("open", "confirmed"):
                self.stats["window_confirm_open"] += 1
                if outcome == "open" and reason == "weak_key_open":
                    # window-snapshot-identity-decision: authorized under a
                    # weak epoch key; kept separate for online attribution.
                    self.stats["window_confirm_weak_open"] += 1
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
        lifecycle_key = WindowAttemptKey(window_id, phase)
        lifecycle_fields = dict(fields)
        lifecycle_fields.pop("outcome", None)
        if outcome in ("requested", "unconfirmed"):
            self._record_window_lifecycle(
                gid, lifecycle_key, "confirm", state="CONFIRM_PENDING",
                outcome="PENDING", **lifecycle_fields)
        elif outcome == "open":
            self._record_window_lifecycle(
                gid, lifecycle_key, "authorization", state="AUTHORIZED",
                outcome="AUTHORIZED", **lifecycle_fields)
        elif outcome == "confirmed":
            # A confirmed record may be an already-completed action; do not
            # overwrite a stronger SUCCESS/STRATEGY_PASS state.
            self._record_window_lifecycle(
                gid, lifecycle_key, "confirm", state="AUTHORIZED",
                outcome="AUTHORIZED", **lifecycle_fields)
        elif outcome in ("closed", "stale", "expired", "not_responding",
                         "deadline_missing", "no_legal", "miss"):
            self._record_window_terminal(
                gid, lifecycle_key, reason or outcome, phase=phase, seq=seq,
                outcome="TERMINAL")

    @staticmethod
    def _window_lifecycle_token(window_key):
        if not isinstance(window_key, WindowAttemptKey):
            return None
        # Use the phase-independent WindowId tuple plus phase, deliberately
        # excluding diagnostic provenance/fallback fields from the join key.
        return repr(window_key.as_tuple())

    @staticmethod
    def _deadline_left_ms(exact_deadline_at):
        if exact_deadline_at is None:
            return None
        try:
            return round((float(exact_deadline_at) - time.time()) * 1000.0, 1)
        except (TypeError, ValueError):
            return None

    def _record_window_lifecycle(self, gid, window_key, stage, *, state=None,
                                 outcome=None, **fields):
        """Record a monotonic lifecycle transition without reopening it.

        ``window_lifecycle`` is deliberately best-effort: a recorder failure
        must never alter action safety.  A late SUCCESS is allowed to upgrade
        a prior POST_UNCERTAIN because it is reconciliation evidence, but a
        stale confirm/timeout cannot move a closed attempt back to pending.
        """
        token = self._window_lifecycle_token(window_key)
        if token is None:
            return
        closed_states = {
            "POST_OK", "POST_REJECTED", "POST_UNCERTAIN",
            "DECIDED_PASS", "PREEMPTED", "TERMINAL", "SUCCESS",
        }
        with self._window_lifecycle_lock:
            current = self._window_lifecycles.setdefault(
                (gid, token), {"state": "OBSERVED"})
            previous = current.get("state")
            # Do not let delayed state/timeout facts reopen a terminal phase.
            if previous in closed_states and state not in ("SUCCESS", None):
                return
            if state is not None:
                if previous == "POST_UNCERTAIN" and state == "SUCCESS":
                    current["state"] = state
                elif previous not in closed_states:
                    current["state"] = state
            current.update({key: value for key, value in fields.items()
                            if value is not None})
            if outcome is not None:
                current["outcome"] = outcome
            current["stage"] = stage
            snapshot = dict(current)
        writer = (getattr(self.recorder, "window_lifecycle", None)
                  if self.recorder is not None else None)
        if writer is not None:
            try:
                writer(gid, stage=stage, state=snapshot.get("state"),
                       outcome=snapshot.get("outcome"),
                       window_id=(window_key.window_id.as_json()),
                       window_attempt_key=window_key.as_json(),
                       **{key: value for key, value in snapshot.items()
                          if key not in ("state", "outcome", "stage",
                                         "window_id",
                                         "window_attempt_key")
                          and value is not None})
            except Exception:
                pass

    def _set_window_authorization(self, mirror, window_key, snap, seq=None,
                                  legal=None):
        """Attach and persist the exact authorization snapshot for an attempt."""
        if mirror is None or not isinstance(window_key, WindowAttemptKey):
            return None
        exact = self._snapshot_deadline(snap)
        existing = (getattr(mirror, "_window_authorizations", None) or {}
                    ).get(window_key)
        if (existing is not None
                and existing.get("authorization_snapshot_seq") == seq
                and existing.get("authorization_phase") == snap.get("phase")):
            if legal and not existing.get("legal"):
                existing["legal"] = list(legal)
            return existing
        context = {
            "window_id": window_key.window_id.as_json(),
            "window_attempt_key": window_key.as_json(),
            "authorization_snapshot_seq": seq,
            "authorization_phase": snap.get("phase") if isinstance(
                snap, dict) else None,
            "authorization_responding_seats": list(
                (snap or {}).get("responding_seats") or [])
            if isinstance(snap, dict) else None,
            "exact_deadline_at": exact,
            "authoritative_open_at": time.time(),
            "identity_status": window_key.window_id.identity_status,
            "identity_origin": window_key.window_id.identity_origin,
            "first_seen_via": window_key.window_id.first_seen_via,
            "legal": (list(legal) if legal is not None else None),
        }
        contexts = getattr(mirror, "_window_authorizations", None)
        if contexts is None:
            contexts = {}
            mirror._window_authorizations = contexts
        contexts[window_key] = context
        self._record_window_lifecycle(
            getattr(mirror, "_game_id", None), window_key, "authorization",
            state="AUTHORIZED", outcome="AUTHORIZED", **context)
        writer = (getattr(self.recorder, "window_authorization", None)
                  if self.recorder is not None else None)
        if writer is not None:
            try:
                writer(getattr(mirror, "_game_id", None),
                       outcome="authoritative_open", **context,
                       deadline_left_ms=self._deadline_left_ms(exact),
                       observed_at=time.time())
            except Exception:
                pass
        return context

    @staticmethod
    def _window_authorization(mirror, window_key):
        contexts = getattr(mirror, "_window_authorizations", None) or {}
        return contexts.get(window_key, {}) if window_key is not None else {}

    def _record_window_terminal(self, gid, window_key, reason, *, phase=None,
                                seq=None, outcome=None):
        if not isinstance(window_key, WindowAttemptKey):
            return
        self._record_window_lifecycle(
            gid, window_key, "terminal", state="TERMINAL",
            outcome=outcome or "TERMINAL", terminal_reason=reason,
            terminal_observed_at=time.time(), terminal_seq=seq)
        writer = (getattr(self.recorder, "window_terminal", None)
                  if self.recorder is not None else None)
        if writer is not None:
            try:
                writer(gid, window_id=window_key.window_id.as_json(),
                       window_attempt_key=window_key.as_json(),
                       phase=phase or window_key.phase,
                       terminal_reason=reason, terminal_observed_at=time.time(),
                       terminal_seq=seq,
                       outcome=outcome)
            except Exception:
                pass

    def _remember_uncertain_recovery(self, gid, window_key, payload,
                                     mirror):
        if not isinstance(window_key, WindowAttemptKey):
            return
        with self._window_lifecycle_lock:
            pending = self._uncertain_recoveries.setdefault(gid, [])
            pending.append({
                "window_key": window_key,
                "payload": dict(payload or {}),
                "meld_count_before": len(
                    (getattr(mirror, "melds", None) or [])[getattr(
                        mirror, "me", 0)])
                if mirror is not None and getattr(mirror, "melds", None)
                else None,
            })

    def _reconcile_uncertain_snapshot(self, gid, mirror, snap, seq=None):
        """Close an uncertain POST with the first safe authoritative result."""
        with self._window_lifecycle_lock:
            pending = self._uncertain_recoveries.pop(gid, [])
        for item in pending:
            key = item["window_key"]
            before = item.get("meld_count_before")
            melds = getattr(mirror, "melds", None) or []
            after = (len(melds[mirror.me]) if melds
                     and 0 <= getattr(mirror, "me", -1) < len(melds)
                     else None)
            phase = (snap or {}).get("phase") if isinstance(snap, dict) else None
            if before is not None and after is not None and after > before:
                status = "uncertain_reconciled_applied"
            elif phase not in (key.phase, "response_peng", "response_chi"):
                status = "uncertain_reconciled_not_applied"
            else:
                status = "uncertain_reconcile_unknown"
            self._record_window_lifecycle(
                gid, key, "reconciliation", outcome=status,
                reconciliation=status, reconciliation_seq=seq,
                reconciliation_phase=phase,
                reconciliation_observed_at=time.time())

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

    @staticmethod
    def _window_confirm_from_chi(chi):
        """Promote a chi wait-state to the shared confirmation model."""

        if not isinstance(chi, dict):
            return None
        key = chi.get("key")
        window_id = (key.window_id
                     if isinstance(key, WindowAttemptKey) else None)
        source_seq = (window_id.source_discard_seq
                      if window_id is not None else None)
        return _WindowConfirm(
            phase="response_chi",
            pending=chi.get("pending"),
            round_no=chi.get("round_no"),
            legal=chi.get("legal") or (),
            source_seq=source_seq,
            source_ts=chi.get("source_ts"),
            source_watermark=chi.get("source_watermark"),
            schedule_deadline=chi.get("deadline_mono"),
            not_before=chi.get("ready_mono"),
            revision=chi.get("demand_revision"),
            window_key=key,
            source_origin=(window_id.identity_origin
                           if window_id is not None else None),
            first_seen_via=(window_id.first_seen_via
                            if window_id is not None else None),
            reason="chi_confirmation",
        )

    @classmethod
    def _carry_window_confirm_budget(cls, previous, current):
        """Keep one bounded confirmation lifecycle across wait-state rebuilds."""

        if not (isinstance(previous, _WindowConfirm)
                and isinstance(current, _WindowConfirm)):
            return current
        if previous.phase != current.phase:
            return current
        if not cls._same_window_identity(
                getattr(previous, "window_key", None),
                getattr(current, "window_key", None)):
            return current
        current.pending_retries = max(
            getattr(previous, "pending_retries", 0),
            getattr(current, "pending_retries", 0))
        old_deadline = getattr(previous, "retry_deadline", None)
        new_deadline = getattr(current, "retry_deadline", None)
        if old_deadline is not None and new_deadline is not None:
            current.retry_deadline = min(old_deadline, new_deadline)
        elif old_deadline is not None:
            current.retry_deadline = old_deadline
        if getattr(previous, "logical_request_id", None) is not None:
            current.logical_request_id = previous.logical_request_id
        current.attempt_index = getattr(previous, "attempt_index", 0)
        previous_state = getattr(previous, "confirmation", None)
        current_state = getattr(current, "confirmation", None)
        if previous_state is not None and current_state is not None:
            current.confirmation = previous_state.carry_budget(current_state)
            current.confirmation.timing.observation_budget_deadline = (
                current.retry_deadline)
        return current

    @staticmethod
    def _window_confirm_from_reason(reason):
        """Rebuild a resolver input from a persisted StateDemand reason."""

        if not isinstance(reason, dict):
            return None
        phase = reason.get("phase")
        raw_window_id = reason.get("window_id")
        if isinstance(raw_window_id, WindowId):
            window_id = raw_window_id
        elif isinstance(raw_window_id, dict):
            window_id = WindowId(
                game_id=raw_window_id.get("game_id"),
                round_id=raw_window_id.get("round_id"),
                discard_owner=raw_window_id.get("discard_owner"),
                source_discard_seq=raw_window_id.get("source_discard_seq"),
                tile=raw_window_id.get("tile"),
                identity_status=raw_window_id.get(
                    "identity_status", "legacy_unresolved"),
                fallback=raw_window_id.get("fallback"),
                identity_origin=raw_window_id.get(
                    "identity_origin", "unknown"),
                first_seen_via=raw_window_id.get(
                    "first_seen_via", "unknown"),
            )
        else:
            window_id = None
        pending = None
        if window_id is not None and (
                window_id.discard_owner is not None
                and window_id.tile is not None):
            pending = (window_id.discard_owner, window_id.tile)
        key = (WindowAttemptKey(window_id, phase)
               if window_id is not None and phase is not None else None)
        return _WindowConfirm(
            phase=phase,
            pending=pending,
            round_no=(window_id.round_id if window_id is not None else None),
            legal=reason.get("legal") or (),
            source_seq=(window_id.source_discard_seq
                        if window_id is not None else None),
            schedule_deadline=reason.get("deadline"),
            not_before=reason.get("not_before"),
            revision=reason.get("revision"),
            window_key=key,
            source_origin=(window_id.identity_origin
                           if window_id is not None else None),
            first_seen_via=(window_id.first_seen_via
                            if window_id is not None else None),
            reason="demand_confirmation",
        )

    @classmethod
    def _window_confirm_matches(cls, mirror, confirm, snap=None, seq=None):
        """比较确认快照身份，返回 ``MATCH``/``MISMATCH``/``UNKNOWN``。

        同局同家同牌可能再次出现，因此 authoritative source sequence
        是跨重锚的硬条件；legacy fallback 只允许作诊断，不能证明同窗。
        ``UNKNOWN`` 不是窗口已经变化：缺少 source identity 时必须保持
        pending，不能直接写 claim_miss 或把 phase-only 相等当成确认成功。
        """
        if mirror is None or confirm is None:
            return "MISMATCH"
        if mirror.round_no != confirm.round_no:
            return "MISMATCH"
        if mirror.pending is None or confirm.pending is None:
            return "MISMATCH"
        if tuple(mirror.pending) != tuple(confirm.pending):
            return "MISMATCH"
        identity_unknown = False
        expected_key = getattr(confirm, "window_key", None)
        actual_key = cls._window_key(mirror, confirm.phase, snap=snap)
        if isinstance(expected_key, WindowAttemptKey):
            if actual_key is None:
                return "UNKNOWN"
            expected_id = expected_key.window_id
            actual_id = actual_key.window_id
            if (expected_id.round_id, expected_id.discard_owner,
                    expected_id.tile) != (
                        actual_id.round_id, actual_id.discard_owner,
                        actual_id.tile):
                return "MISMATCH"
            if (expected_id.game_id is not None
                    and actual_id.game_id is not None
                    and expected_id.game_id != actual_id.game_id):
                return "MISMATCH"
            if (expected_id.game_id is not None
                    and actual_id.game_id is None):
                identity_unknown = True
            if expected_id.authoritative:
                if not actual_id.authoritative:
                    identity_unknown = True
                elif (str(expected_id.source_discard_seq)
                      != str(actual_id.source_discard_seq)):
                    return "MISMATCH"
            else:
                # A legacy expected key and a legacy actual key are both
                # unresolved; an authoritative actual key cannot prove that
                # it belongs to the old legacy window either.
                identity_unknown = True
        elif confirm.source_seq is not None:
            if actual_key is None or not actual_key.window_id.authoritative:
                identity_unknown = True
            elif (str(actual_key.window_id.source_discard_seq)
                  != str(confirm.source_seq)):
                return "MISMATCH"
        else:
            identity_unknown = True
        snap_source = cls._explicit_source_seq(snap)
        if (confirm.source_seq is not None and snap_source is not None
                and str(snap_source) != str(confirm.source_seq)):
            return "MISMATCH"
        if isinstance(snap, dict) and snap.get("last_discard"):
            try:
                if tidx(snap["last_discard"]) != confirm.pending[1]:
                    return "MISMATCH"
            except (KeyError, TypeError, ValueError):
                return "MISMATCH"
        return "UNKNOWN" if identity_unknown else "MATCH"

    @staticmethod
    def _window_decision_for_key(mirror, key):
        """Read the decision ledger without deriving identity from phase.

        The ledger survives FULL snapshot replacement.  Exact key lookup is
        intentional for authoritative windows; a legacy key is only useful
        for same-epoch diagnostics and must not be promoted to cross-reanchor
        identity.
        """

        if mirror is None or key is None:
            return "unset"
        decisions = getattr(mirror, "_window_decisions", None) or {}
        return decisions.get(key, "unset")

    @classmethod
    def _window_confirm_completed(cls, mirror, confirm):
        """Whether this confirmation already ended in a local decision.

        A successful POST is represented by ``attempted_windows``; a chi
        PASS has no POST but is represented by the decision ledger.  Checking
        this before identity resolution prevents a later draw snapshot from
        turning an already completed window into ``identity_changed`` and a
        false claim miss.
        """

        if mirror is None or confirm is None:
            return False
        key = getattr(confirm, "window_key", None)
        if not isinstance(key, WindowAttemptKey):
            return False
        attempted = getattr(mirror, "_attempted_windows", None) or set()
        if cls._strong_window_key(key) and key in attempted:
            return True
        return cls._window_decision_for_key(mirror, key) == -1

    def _confirm_pull_budget_expired(self, confirm, snap=None):
        """Deadline-driven budget: stop scheduling confirm pulls.

        Another WINDOW_CONFIRM round-trip is only worth scheduling while
        the remaining window time still affords the pull plus the
        decide+POST margin.  The exact snapshot deadline tightens the
        schedule estimate, but only for a same-phase snapshot: the
        response_peng deadline does not bound the following chi window.
        """
        window_end = None
        if (isinstance(snap, dict)
                and snap.get("phase") == getattr(confirm, "phase", None)):
            window_end = self._mono_deadline(self._snapshot_deadline(snap))
        if window_end is None:
            window_end = getattr(confirm, "retry_deadline", None)
        if window_end is None:
            return False
        return (time.monotonic()
                >= window_end - CONFIRM_PULL_MARGIN - WEAK_DECIDE_MARGIN)

    def _window_confirm_retry_expired(self, confirm, snap=None):
        """Bound unresolved confirmation without claiming a server miss."""
        confirm.pending_retries = getattr(confirm, "pending_retries", 0) + 1
        retry_deadline = getattr(confirm, "retry_deadline", None)
        if retry_deadline is None:
            retry_deadline = time.monotonic() + max(
                WINDOW_SEC * 2, self.window_wait * 2, 0.5)
            confirm.retry_deadline = retry_deadline
        expired = (confirm.pending_retries > MAX_WINDOW_CONFIRM_PENDING_RETRIES
                   or time.monotonic() >= retry_deadline
                   or self._confirm_pull_budget_expired(confirm, snap=snap))
        lifecycle_confirmation = getattr(confirm, "confirmation", None)
        if lifecycle_confirmation is not None:
            lifecycle_confirmation.pending_retries = confirm.pending_retries
            lifecycle_confirmation.timing.observation_budget_deadline = (
                retry_deadline)
        if expired and not getattr(confirm, "observation_budget_exhausted", False):
            confirm.observation_budget_exhausted = True
            with self._stats_lock:
                self.stats["confirmation_observation_budget_exhausted"] += 1
            if lifecycle_confirmation is not None:
                lifecycle_confirmation.observation_budget_exhausted = True
                lifecycle_confirmation.observe(
                    "confirmation_observation_budget_exhausted")
        return expired

    def _weak_key_open_observation(self, mirror, snap, confirm):
        """Return ``(weak key, legal)`` when this snapshot may decide now.

        window-snapshot-identity-decision: the confirmation's expected
        identity is itself legacy (a snapshot-first window: no protocol
        field, no observed source event) and this fresh authoritative
        snapshot exposes that pending window -- matching phase, our seat
        responding, an exact deadline that the submit guard can still
        meet, and non-empty legal options.  An authoritative expected
        identity that the snapshot cannot reproduce is weak evidence and
        must stay PENDING; it is never downgraded to a weak decision.
        """
        if not isinstance(snap, dict) or mirror is None or confirm is None:
            return None
        expected_key = getattr(confirm, "window_key", None)
        if (isinstance(expected_key, WindowAttemptKey)
                and expected_key.window_id.authoritative):
            return None
        if getattr(confirm, "source_seq", None) is not None:
            return None
        phase = snap.get("phase")
        if phase != confirm.phase:
            return None
        if mirror.me not in (snap.get("responding_seats") or []):
            return None
        exact = self._snapshot_deadline(snap)
        if exact is None or exact - time.time() <= SUBMIT_EPS:
            return None
        legal = (self._claim_legal(mirror, phase)
                 if phase in ("response_peng", "response_chi") else [])
        if not legal:
            return None
        key = self._window_key(mirror, phase, snap=snap)
        if key is None or self._strong_window_key(key):
            return None
        return key, legal

    def _resolve_window_confirm(self, gid, mirror, snap, confirm, seq=None):
        """统一解析 peng/chi 窗口确认，返回 ``confirmed`` 或最终结果。

        ``confirmed`` 只说明快照允许重新计算动作；动作仍由对应 phase
        的 action path 再次检查精确 deadline 和合法集。两种 phase 必须
        同时满足同一 WindowId、phase、responding seat、精确且未过期的
        deadline 和非空权威合法集。
        """
        if self._window_confirm_completed(mirror, confirm):
            self._record_window_confirm(
                gid, confirm, "confirmed", reason="action_already_completed",
                snap=snap, seq=seq)
            return "confirmed"

        identity = (self._window_confirm_matches(
            mirror, confirm, snap=snap, seq=seq)
            if isinstance(snap, dict) else "MISMATCH")
        if identity == "MISMATCH":
            reason = "new_round" if (mirror is not None and confirm is not None
                                      and mirror.round_no != confirm.round_no) \
                else "identity_changed"
            self._record_window_confirm(gid, confirm, "stale", reason=reason,
                                        snap=snap, seq=seq)
            if (confirm is not None and confirm.legal
                    and confirm.chosen is not None
                    and confirm.chosen != -1):
                self._claim_miss(
                    gid, confirm.phase, confirm.legal, chosen=confirm.chosen,
                    reason="window_confirm_" + reason, mirror=None,
                    seq=(confirm.source_watermark
                         if confirm.source_watermark is not None
                         else (seq if seq is not None else confirm.source_seq)),
                    pending=confirm.pending,
                    window_key=getattr(confirm, "window_key", None),
                    logical_request_id=getattr(
                        confirm, "logical_request_id", None),
                    exact_deadline_at=self._epoch_from_mono(
                        getattr(confirm, "schedule_deadline", None)),
                    action_posted=False)
                self._record_window_confirm(gid, confirm, "miss",
                                            reason=reason, snap=snap, seq=seq)
            return "stale"
        if identity == "UNKNOWN":
            # Decision/evidence split: a snapshot-first window with no
            # protocol identity may be decided under the weak epoch key
            # when this fresh authoritative snapshot exposes the open
            # window.  The window is never promoted to authoritative and
            # stays excluded from strong completeness; a wrong weak-key
            # decision is bounded by the server's 409 validation and the
            # existing same-loop recovery.
            weak = self._weak_key_open_observation(mirror, snap, confirm)
            if weak is not None:
                weak_key, weak_legal = weak
                self._set_window_authorization(
                    mirror, weak_key, snap, seq=seq, legal=weak_legal)
                self._record_window_confirm(
                    gid, confirm, "open", reason="weak_key_open",
                    snap=snap, seq=seq, legal=weak_legal)
                return "confirmed"
            if self._window_confirm_retry_expired(confirm, snap=snap):
                self._record_window_confirm(
                    gid, confirm, "unconfirmed",
                    reason="identity_confirmation_budget_exhausted",
                    snap=snap, seq=seq)
                # The uncertainty is not a server-side miss, but the bounded
                # client confirmation lifecycle is terminal now; otherwise
                # _demand_window_status() would keep this reason PENDING
                # forever.
                return "expired"
            self._record_window_confirm(
                gid, confirm, "unconfirmed", reason="identity_unknown",
                snap=snap, seq=seq)
            return "identity_unconfirmed"

        phase = snap.get("phase")
        seat = mirror.me
        responding = seat in (snap.get("responding_seats") or [])
        exact = self._snapshot_deadline(snap)
        current_legal = (self._claim_legal(mirror, phase)
                         if phase in ("response_peng", "response_chi")
                         else [])
        # response_peng -> response_chi is an ordered transition within the
        # same WindowId.  Seeing the old peng phase in the first authoritative
        # snapshot does not prove that the chi confirmation is closed.
        if (confirm.phase == "response_chi"
                and phase == "response_peng"):
            pending_deadline = (confirm.schedule_deadline
                                if confirm.schedule_deadline is not None
                                else getattr(confirm, "retry_deadline", None))
            retry_expired = self._window_confirm_retry_expired(
                confirm, snap=snap)
            if pending_deadline is None:
                pending_deadline = getattr(confirm, "retry_deadline", None)
            if (pending_deadline is not None
                    and time.monotonic() >= pending_deadline):
                outcome, reason = "expired", "chi_schedule_deadline_expired"
            elif retry_expired:
                outcome, reason = "expired", "confirmation_retry_exhausted"
            else:
                self._record_window_confirm(
                    gid, confirm, "unconfirmed",
                    reason="phase_not_reached", snap=snap, seq=seq,
                    legal=current_legal)
                return "phase_pending"
        elif (confirm.phase == "response_peng"
              and phase == "response_chi"):
            outcome, reason = "closed", "phase_changed"
        elif phase != confirm.phase:
            outcome, reason = "closed", "phase_changed"
        elif not responding:
            outcome, reason = "not_responding", "seat_not_responding"
        elif exact is None:
            outcome, reason = "deadline_missing", "exact_deadline_missing"
        elif exact - time.time() <= SUBMIT_EPS:
            outcome, reason = "expired", "exact_deadline_expired"
        elif not current_legal:
            outcome, reason = "no_legal", "new_legal_set_empty"
        else:
            authorization_key = self._window_key(
                mirror, confirm.phase, snap=snap)
            self._set_window_authorization(
                mirror, authorization_key, snap, seq=seq,
                legal=current_legal)
            self._record_window_confirm(
                gid, confirm, "open", reason="authoritative_open",
                snap=snap, seq=seq, legal=current_legal)
            return "confirmed"

        # 缺少精确 deadline 只能说明无法授权动作，不能推断服务端已
        # 关闭窗口；这类结果保持 unconfirmed，不记最终 miss。
        if outcome in ("deadline_missing", "identity_unconfirmed"):
            if not self._window_confirm_retry_expired(confirm, snap=snap):
                self._record_window_confirm(gid, confirm, "unconfirmed",
                                            reason=reason, snap=snap, seq=seq,
                                            legal=current_legal)
                return "unconfirmed"
            outcome, reason = "expired", "confirmation_retry_exhausted"

        self._record_window_confirm(gid, confirm, outcome, reason=reason,
                                    snap=snap, seq=seq, legal=current_legal)
        # 新快照已证明原碰窗不能再安全提交；只有原镜像确实有候选时
        # 才落最终 miss。预确认阶段绝不写 claim_miss。
        uncertain_terminal = reason in (
            "confirmation_retry_exhausted",
            "identity_confirmation_budget_exhausted",
            "exact_deadline_missing",
            "identity_unknown",
            "deadline_outside_source_window",
            "chi_schedule_deadline_expired",
            "phase_not_reached",
        )
        if (outcome != "no_legal" and not uncertain_terminal
                and confirm.legal and confirm.chosen != -1):
            self._claim_miss(
                gid, confirm.phase, confirm.legal, chosen=confirm.chosen,
                reason="window_confirm_" + reason, mirror=None,
                seq=(confirm.source_watermark
                     if confirm.source_watermark is not None
                     else (seq if seq is not None else confirm.source_seq)),
                pending=confirm.pending,
                window_key=getattr(confirm, "window_key", None),
                logical_request_id=getattr(
                    confirm, "logical_request_id", None),
                exact_deadline_at=self._epoch_from_mono(
                    getattr(confirm, "schedule_deadline", None)),
                action_posted=False)
            self._record_window_confirm(gid, confirm, "miss", reason=reason,
                                        snap=snap, seq=seq,
                                        legal=current_legal)
        return outcome

    def _state_abandon(self, gid, phase, reason, dedupe_key=None):
        """镜像/手牌状态无法安全决策时的交接诊断。"""
        if dedupe_key is not None:
            if dedupe_key in self._state_abandon_seen:
                return
            self._state_abandon_seen.add(dedupe_key)
        handoff = ("本回合交服务端代打"
                   if self.mode != "match" else
                   "客户端无法安全决策,等待权威状态")
        self._log(f"我方{reason}({phase}),{handoff}")
        with self._stats_lock:
            self.stats["client_state_abandons"] += 1
            # A missing/invalid deadline proves only that the client could
            # not make a safe decision.  In production matches the server
            # may still be waiting (or may settle the window later), so do
            # not present this handoff as an observed server auto-play.
            # Keep the legacy count for scripted/replay adapters whose
            # historical fixtures use ``auto_played`` for this handoff.
            if self.mode != "match":
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

        snapshot ``seq`` 是包含式状态 watermark，不能冒充弃牌来源序号。
        对 ``tile_discarded`` 事件，事件自身的 ``seq`` 就是该弃牌事件的
        authoritative source sequence；snapshot 的 ``seq`` 仍永远不接受
        为 source identity。协议显式字段优先，缺失时才使用事件自身 seq，
        再缺失则保留 ``legacy_unresolved`` 弱 fallback。
        """
        if mirror is None or mirror.pending is None:
            return None
        owner, tile = mirror.pending
        source_seq, identity_origin = cls._source_identity(ev)
        first_seen_via = "event" if source_seq is not None else None
        if source_seq is None:
            source_seq, identity_origin = cls._source_identity(snap)
            if source_seq is not None:
                first_seen_via = "snapshot"
        if source_seq is None:
            source_seq = getattr(mirror, "_source_discard_seq", None)
            if source_seq is not None:
                identity_origin = getattr(
                    mirror, "_source_discard_origin", "carried_event_seq")
                first_seen_via = ("reanchor"
                                  if identity_origin == "carried_event_seq"
                                  else "mirror")
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
            # window-snapshot-identity-decision: the pending tile sits at
            # the tail of the discarder's river until it is claimed, so the
            # owner's river length pins the instance within one snapshot
            # epoch.  The meld lengths stay in the weak key because a claim
            # pops the river: without them a claimed-then-repeated same
            # tile would alias two real windows onto one weak key.
            identity_status = "legacy_unresolved"
            fallback = (len(mirror.discards[owner]),
                        tuple(len(m) for m in mirror.melds))
            identity_origin = "legacy_snapshot"
            first_seen_via = "snapshot" if snap is not None else "unknown"
        return WindowAttemptKey(
            WindowId(
                game_id=getattr(mirror, "_game_id", None),
                round_id=mirror.round_no,
                discard_owner=owner,
                source_discard_seq=source_seq,
                tile=tile,
                identity_status=identity_status,
                fallback=fallback,
                identity_origin=identity_origin or "unknown",
                first_seen_via=first_seen_via or "unknown",
            ),
            phase,
        )

    @staticmethod
    def _explicit_source_seq(value):
        """Read a discard source sequence without using snapshot watermark.

        The event's own inclusive ``seq`` is the source sequence when the
        payload is a ``tile_discarded`` event.  A bare snapshot/response
        dictionary's ``seq`` remains only a state watermark.
        """
        return BotClient._source_identity(value)[0]

    @staticmethod
    def _source_identity(value):
        """Return ``(source_seq, origin)`` without using snapshot watermark.

        The origin is intentionally diagnostic: acceptance can distinguish an
        explicit protocol field, a tile event sequence, a sequence carried
        through re-anchor, and a legacy snapshot-only window.
        """
        if not isinstance(value, dict):
            return None, None
        for field in ("source_discard_seq", "last_discard_seq", "discard_seq"):
            if value.get(field) is not None:
                return value[field], "explicit_source_field"
        data = value.get("data")
        if isinstance(data, dict):
            for field in ("source_discard_seq", "last_discard_seq", "discard_seq"):
                if data.get(field) is not None:
                    return data[field], "explicit_source_field"
        if value.get("type") == "tile_discarded" and value.get("seq") is not None:
            return value["seq"], "tile_discard_event_seq"
        return None, None

    @staticmethod
    def _set_source_identity(mirror, source_seq, source_origin=None):
        """Keep the current discard identity on the live mirror as well.

        ``pending_source`` is needed across a full re-anchor, but action and
        decision paths run from the live Mirror.  Maintaining both through
        this single helper prevents an event from being authoritative while
        the immediately-following action silently falls back to an old
        snapshot identity.
        """
        if mirror is None:
            return
        if source_seq is None:
            for name in ("_source_discard_seq", "_source_discard_origin"):
                if hasattr(mirror, name):
                    delattr(mirror, name)
            return
        mirror._source_discard_seq = source_seq
        mirror._source_discard_origin = source_origin or "carried_event_seq"

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
        are deduped by the weak epoch key ``(round_id, discard_owner,
        tile, river tail position)``: within the current snapshot epoch
        unconditionally, and across a re-anchor only when round/owner/tile
        still match and the discard-river tail position is consistent.  A
        transport-failed legacy action remains blocked separately so an
        unknown/409 POST is never blindly replayed.
        """
        if key is None:
            return False
        if cls._strong_window_key(key):
            return key in (attempted_keys or set())
        legacy_attempts = getattr(mirror, "_legacy_attempts", {})
        legacy_failed = getattr(mirror, "_legacy_failed", set())
        if legacy_failed and key in legacy_failed:
            return True
        if key in legacy_attempts:
            if legacy_attempts[key] == getattr(mirror, "_legacy_epoch", None):
                return True
            # Conservative cross-reanchor carry: the river must still pin
            # this weak key's tail.  A same-tile re-discard after a claim
            # pops the river first and changes a meld length, so the key
            # itself differs and no carry happens -- the 409 backstop
            # covers a guessed resubmission instead.
            return cls._weak_key_still_pinned(mirror, key)
        return False

    @classmethod
    def _weak_key_still_pinned(cls, mirror, key):
        """Whether the mirror's river still pins this weak key's discard.

        Cross-reanchor carry requires round/owner/tile to match and the
        pending tile to sit at the recorded tail position of the owner's
        river.  Key equality alone is not enough here because the caller
        may query with a stale wait-state key against a rebuilt mirror.
        """
        if not isinstance(key, WindowAttemptKey):
            return False
        window_id = key.window_id
        fallback = window_id.fallback
        if not isinstance(fallback, tuple) or not fallback:
            return False
        pending = getattr(mirror, "pending", None)
        if pending is None or tuple(pending) != (
                window_id.discard_owner, window_id.tile):
            return False
        if mirror.round_no != window_id.round_id:
            return False
        rivers = getattr(mirror, "discards", None)
        owner = window_id.discard_owner
        if not isinstance(rivers, list) or not isinstance(owner, int) \
                or not (0 <= owner < len(rivers)):
            return False
        river = rivers[owner]
        return (bool(river) and river[-1] == window_id.tile
                and len(river) == fallback[0])

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
            chi_legal = [
                a for a in mirror.build_game("response_chi").legal_actions()
                if a != -1
            ]
            if not chi_legal:
                return None
        except MirrorInconsistent:
            return None
        key = self._window_key(mirror, "response_chi", ev=ev, snap=snap)
        responded_keys = responded_keys or set()
        attempted_keys = attempted_keys or set()
        # 吃窗已成功响应或已经有一个未确定的 POST，不重复提交。
        if self._window_was_attempted(mirror, key, attempted_keys):
            return None
        # A chi PASS is a completed local decision even though it has no
        # physical PASS POST.  Do not recreate the same decision after a
        # later FULL snapshot.
        if self._window_decision_for_key(mirror, key) != "unset":
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
            "legal": chi_legal,
            "source_ts": discard_epoch,
            "source_watermark": (
                ev.get("seq") if isinstance(ev, dict) else None),
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
        22 次作废仅 1 次真持有对子);张数漂移无法评估时不推断损失，
        等待镜像重锚；本窗已本地决策为过(-1)时不算损失。"""
        if not mirror.hand_count_ok("response_peng"):
            # Production match workers must not infer a claim loss from a
            # malformed mirror; they re-anchor and wait for authoritative
            # state instead.  Scripted legacy adapters retain the historical
            # compatibility count so their timeout fixtures remain stable.
            return self.mode != "match"
        if self._window_decision(mirror, "response_peng") == -1:
            return False
        return bool(self._claim_legal(mirror, "response_peng"))

    @staticmethod
    def _claim_legal(mirror, phase):
        """返回规则允许的非 pass 吃/碰/杠动作；无法评估时返回空集。"""
        if mirror is None or not mirror.hand_count_ok(phase):
            return []
        pending = getattr(mirror, "pending", None)
        if pending is None:
            return []
        owner, _tile = pending
        if owner == mirror.me:
            return []
        if phase == "response_chi" and (owner + 1) % 4 != mirror.me:
            # 吃牌只对出牌者下家开放。  Do this before building a
            # synthetic Game so stale pending state cannot be logged as a
            # real legal opportunity.
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
        key = self._window_key(mirror, phase)
        if isinstance(key, WindowAttemptKey):
            self._record_window_terminal(
                gid, key, reason, phase=phase, seq=seq,
                outcome="SERVER_TERMINAL")
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
                    sse["connection_id"] = (
                        int(sse.get("connection_id", 0) or 0) + 1)
                    connection_id = sse["connection_id"]
                    self._record_replay_trace(
                        gid, "sse_connect", {"connectionId": connection_id},
                        causal_parents=(), precision="recorded")
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
                            self._record_sse_frame(
                                gid, connection_id=connection_id,
                                raw_line=line, accepted=False,
                                parse_error="invalid_json",
                                received_monotonic=time.monotonic())
                            continue
                        seq = j.get("seq")
                        # 帧只是水位唤醒信号；重复/倒退水位不应制造额外
                        # /state 拉取，但绝不把它当作本地事件游标。
                        last = sse.get("last_wake_seq")
                        closed = bool(j.get("closed"))
                        wake_enqueued = (
                            seq is None or last is None or seq > last or closed)
                        if wake_enqueued:
                            sse["last_wake_seq"] = seq
                            wake.put((seq, closed))
                        self._record_sse_frame(
                            gid, seq=seq, closed=closed, payload=j,
                            raw_line=line, connection_id=connection_id,
                            previous_wake_seq=last,
                            last_wake_seq=sse.get("last_wake_seq"),
                            accepted=True,
                            wake_enqueued=wake_enqueued,
                            deduplicated=not wake_enqueued,
                            received_monotonic=time.monotonic())
                        if closed:
                            break
                except (OSError, TimeoutError):
                    pass  # 断流(keepalive 超时/对端关闭):退避重连
                finally:
                    try:
                        resp.close()
                    except OSError:
                        pass
                    self._record_replay_trace(
                        gid, "sse_disconnect",
                        {"connectionId": sse.get("connection_id"),
                         "reason": "unknown"}, precision="recorded")
            sse["alive"] = False
            if self._sleep_stop(delay, stop):
                return
            delay = min(delay * 2, 10.0)

    def _record_sse_frame(self, gid, **fields):
        """记录 SSE 帧；兼容尚未升级的外部/测试 Recorder。"""
        recorder = self.recorder
        method = getattr(recorder, "sse_frame", None) \
            if recorder is not None else None
        if method is not None:
            try:
                method(gid, **fields)
            except Exception:
                # 观测不得阻断 SSE 监听或主循环。
                pass
        kind = ("sse_parse_error" if fields.get("parse_error") else
                "sse_closed" if fields.get("closed") else "sse_received")
        self._record_replay_trace(
            gid, kind, dict(fields), seq_no=fields.get("seq"),
            clock_domain="monotonic", precision="recorded")

    def _record_replay_trace(self, gid, kind, payload=None, **fields):
        """Best-effort opt-in execution boundary for the offline trace."""
        recorder = self.recorder
        writer = getattr(recorder, "trace", None) if recorder is not None else None
        if writer is None:
            return None
        try:
            return writer(gid, kind, payload, **fields)
        except Exception:
            # Trace persistence must never change request or action behavior.
            return None

    def _record_reset_boundary(self, rec, gid, reason, mirror):
        if rec is not None:
            rec.reset(gid, reason)
        self._record_replay_trace(
            gid, "reset", {"reason": reason},
            state_before=self._trace_mirror_state(mirror), outcome="RESET")

    @staticmethod
    def _trace_mirror_state(mirror):
        """Serialize supported live Mirror fields with private seats hidden."""
        if mirror is None:
            return None
        players = []
        meld_names = {
            "chow": "CHI", "pong": "PON", "kong_open": "KAN_OPEN",
            "kong_closed": "KAN_CLOSED", "kong_add": "KAN_ADDED",
        }
        for seat in range(4):
            if seat == mirror.me:
                hand = [API_NAME[index] for index, count in enumerate(mirror.my_hand)
                        for _ in range(max(0, int(count)))]
                hand_value = {"status": "KNOWN", "evidence": "RECORDED",
                               "source": "LOCAL_TRACE", "value": hand}
                hand_count = {"status": "KNOWN", "evidence": "RECORDED",
                              "source": "LOCAL_TRACE", "value": len(hand)}
            else:
                hand_value = {"status": "HIDDEN", "evidence": "RECORDED"}
                hand_count = {"status": "UNKNOWN", "evidence": "UNKNOWN"}
            river = [{"tile": API_NAME[tile], "called": False,
                      "discardSeqNo": None, "calledBy": None, "callType": None,
                      "sourceEventId": None, "rawRefs": []}
                     for tile in mirror.discards[seat]]
            melds = [{"meldId": stable_id("mirror-meld", seat, index, kind, tile),
                      "type": meld_names.get(kind, str(kind).upper()),
                      "ownerSeat": seat, "fromSeat": None,
                      "tiles": [API_NAME[tile]], "sourceDiscardEventId": None,
                      "createdSeqNo": None, "updatedSeqNo": None,
                      "rawRefs": [], "parentMeldId": None}
                     for index, (kind, tile) in enumerate(mirror.melds[seat])]
            players.append({"seat": seat, "hand": hand_value,
                            "handCount": hand_count, "river": river,
                            "melds": melds, "flags": {}})
        return {
            "round": {"roundId": getattr(mirror, "_game_id", None),
                       "roundNo": mirror.round_no, "dealerSeat": mirror.dealer,
                       "currentTurn": None, "nextSeat": None,
                       "phase": "RESPONSE" if mirror.pending else None,
                       "remainingTiles": {"status": "KNOWN", "evidence": "RECORDED",
                                           "source": "LOCAL_TRACE",
                                           "value": mirror.live_wall_left()},
                       "liveWallLeft": {"status": "KNOWN", "evidence": "RECORDED",
                                         "source": "LOCAL_TRACE",
                                         "value": mirror.live_wall_left()},
                       "drawOrigin": (
                           {"status": "KNOWN", "evidence": "RECORDED",
                            "source": "LOCAL_TRACE",
                            "value": mirror.draw_origin}
                           if mirror.draw_origin else
                           {"status": "UNKNOWN", "evidence": "UNKNOWN"}),
                       "pending": ({"status": "KNOWN", "evidence": "RECORDED",
                                    "source": "LOCAL_TRACE", "value": list(mirror.pending)}
                                   if mirror.pending else
                                   {"status": "UNKNOWN", "evidence": "UNKNOWN"}),
                       "respondingSeats": {"status": "UNKNOWN", "evidence": "UNKNOWN"},
                       "deadline": {"status": "UNKNOWN", "evidence": "UNKNOWN"},
                       "frozen": {"status": "KNOWN", "evidence": "RECORDED",
                                  "source": "LOCAL_TRACE", "value": mirror.freeze > 0}},
            "players": players, "lastAction": None, "flags": {},
            "evidence": "RECORDED", "rawRefs": [],
        }

    def _play_game_safe(self, gid):
        try:
            self.play_game(gid)
        except Exception as e:
            safe_error = redact_text(
                f"{type(e).__name__}: {e}",
                [getattr(self.api, "token", None)])
            self._log(f"场次 {gid} 异常: {safe_error}")
            with self._stats_lock:
                fails = self._game_fails.get(gid, 0) + 1
                self._game_fails[gid] = fails
            if fails >= 3:
                # 连续 3 次异常:放弃重派(防崩溃循环),落终态记录
                # (match 实测 2026-09-08:502 一次即永久弃局,10 局全被
                # 服务端代打污染积分;有限重派 + seq=0 快照重锚可续打)
                if self.recorder is not None:
                    self.recorder.end(gid, "error",
                                      error=redact_text(
                                          f"{type(e).__name__}: {e}",
                                          [getattr(self.api, "token", None)]))
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
            meta_kwargs = {"mode": self.mode}
            if self.mode == "tournament" and self.tournament_rules is not None:
                meta_kwargs["rules"] = self.tournament_rules.as_dict()
            evaluator = getattr(self.decide, "bot_evaluator", None)
            if evaluator is not None:
                meta_kwargs.update({
                    "evaluator": evaluator,
                    "evaluator_profile": evaluator,
                })
                try:
                    if evaluator in ("shape-v2", "shape_v2", "ev2"):
                        from mj.decision.profile import ProfileSpec
                        meta_kwargs["evaluator_fingerprint"] = \
                            ProfileSpec.shape_v2_discard().fingerprint
                        meta_kwargs["evaluator_kernel"] = \
                            ProfileSpec.shape_v2_discard().kernel_version
                    else:
                        from mj.hand_eval import profile_for
                        meta_kwargs["evaluator_fingerprint"] = \
                            profile_for(evaluator).fingerprint
                except Exception:
                    pass
            try:
                params = inspect.signature(rec.meta).parameters
            except (TypeError, ValueError):
                params = {}
            if not any(p.kind == inspect.Parameter.VAR_KEYWORD
                       for p in params.values()):
                meta_kwargs = {k: v for k, v in meta_kwargs.items()
                               if k in params}
            rec.meta(gid, self.name, self._tid,
                     self.you_cai_bi_kao, self.base, **meta_kwargs)
        # SSE 通知流(v12):帧 = 状态已变信号,唤醒主循环立即拉 /state
        wake = StateDemand(gid=gid)
        sse = stop_l = listener = None
        if self.use_notify:
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
            # A worker can leave through an ApiError, stop event or a test
            # fake before the normal ``finished`` branch has a chance to
            # drain its demand.  Closing is idempotent and preserves the
            # transport/window/game status distinction in the recorder.
            if isinstance(wake, StateDemand) and not wake.closed:
                wake.close("worker_exit")
                wake.finalize_close("worker_exit")

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
        suit_base, pos = tile - (tile % 9), tile % 9
        patterns = ((-2, -1), (-1, 1), (1, 2))
        for left, right in patterns:
            if 0 <= pos + left < 9 and 0 <= pos + right < 9:
                if (hand[suit_base + pos + left] > 0
                        and hand[suit_base + pos + right] > 0):
                    return False
        return True

    def _play_loop(self, gid, wake, sse):
        rec = self.recorder
        # With SSE this is the same per-game object used by the listener; in
        # polling mode it still coordinates logical reasons and physical
        # requests locally.
        demand = wake if isinstance(wake, StateDemand) else StateDemand(gid=gid)
        def on_reconciled(request, snapshot, pending, error):
            # The response application boundary is durable diagnostic data,
            # separate from the frozen dispatch record.  Keep this callback
            # outside StateFetchCoordinator's locks and tolerate legacy fake
            # recorders that do not expose the additive method.
            self._record_state_reconcile(
                rec, gid, request, snapshot, pending, error)
        fetch_coordinator = StateFetchCoordinator(
            gid, demand, scheduler=self._state_scheduler,
            on_reconciled=on_reconciled)
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
        # Carrying an event seq across FULL is safe only while the event
        # stream has been continuous.  A gap means an unseen same-tile
        # discard cannot be ruled out by round/owner/tile alone.
        pending_source_contiguous = False
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
            if self._shutdown_requested():
                demand.close("shutdown")
                demand.finalize_close("shutdown")
                self._record_demand_metrics(demand, demand_seen)
                return
            t0 = time.monotonic()
            state_deadline, stale_dl_used = self._stale_deadline_step(
                state_deadline, stale_dl_used, t0)
            used_request_kind = request_kind
            if used_request_kind is None:
                if window_confirm is not None:
                    used_request_kind = "WINDOW_PENG"
                elif chi_pending is not None:
                    used_request_kind = "WINDOW_CHI"
                elif mirror is None:
                    used_request_kind = "RESYNC"
                else:
                    used_request_kind = "SSE_DELTA"
            plan = self._prepare_state_demand(
                demand, gid, seq, used_request_kind, state_deadline,
                window_confirm, chi_pending,
                coordinator=fetch_coordinator)
            self._record_demand_metrics(demand, demand_seen)
            physical_seq = seq if plan is None else plan.seq
            physical_deadline = (
                plan.effective_deadline
                if plan is not None else state_deadline)
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
            transport_request_id = None
            if plan is not None:
                # The plan is frozen here.  The transport id is allocated at
                # the physical attempt boundary, after which new reasons are
                # reconciled as successor demand rather than mutating seq or
                # mode for this request.
                if fetch_coordinator.active is None:
                    fetch_coordinator.adopt(plan)
                transport_request_id = fetch_coordinator.mark_transport_started()
            operation = fetch_coordinator.active
            state_ticket = (operation.ticket
                            if operation is not None else None)
            candidate_id = (operation.candidate_id
                            if operation is not None else None)
            try:
                if isinstance(self.api, Api):
                    res = self._state(gid, physical_seq, physical_deadline,
                                      request_kind=used_request_kind,
                                      plan=plan,
                                      transport_request_id=transport_request_id,
                                      state_ticket=state_ticket,
                                      candidate_id=candidate_id,
                                      state_throttle=(
                                          self._state_scheduler.throttle
                                          if self._state_scheduler is not None
                                          else None),
                                      cancel_check=lambda: (
                                          fetch_coordinator.cancel_requested()
                                          or self._shutdown_requested()))
                else:
                    # Keep the small fake/server adapter signature used by
                    # replay and unit tests.
                    res = self._state(gid, physical_seq, physical_deadline,
                                      request_kind=used_request_kind)
                state_attempts = self._attempts()
                state_transport = self._transport()
                self._record_state_transport(state_attempts, state_transport)
                if isinstance(demand, StateDemand):
                    physical = (state_transport.get("state_physical_attempts")
                                if isinstance(state_transport, dict) else
                                state_attempts)
                    fetch_coordinator.record_transport_result(physical)
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
                if isinstance(demand, StateDemand):
                    physical = (state_transport.get("state_physical_attempts")
                                if isinstance(state_transport, dict) else
                                state_attempts)
                    fetch_coordinator.record_transport_result(physical)
                ticket = self._throttle_ticket()
                self._record_throttle(ticket)
                if rec is not None:
                    self._record_state_req(
                        rec, gid, seq, status, _ms(t0), state_attempts,
                        state_transport, ticket, used_request_kind,
                        plan=plan, demand=demand, requested_seq=physical_seq,
                        transport_request_id=transport_request_id)
                if e.status == 404:
                    # 场次不可访问(轮次切换/房间回收):视作已结束计数
                    self._log(f"场次 {gid} 已不可访问")
                    with self._stats_lock:
                        self.stats["games"] += 1
                    # Complete the coordinator and close the demand before
                    # writing the terminal marker.  A 404 can arrive while
                    # the final candidate is still reconciling; recording
                    # the end row first would leave an in-flight/PENDING
                    # snapshot as the only evidence and make a normal room
                    # shutdown look like a dirty resource leak.
                    fetch_coordinator.complete(
                        response=None, response_seq=None, error=e)
                    demand.close("inaccessible")
                    demand.finalize_close("inaccessible")
                    self._record_demand_metrics(demand, demand_seen)
                    if rec is not None:
                        self._record_end(
                            rec, gid, "inaccessible",
                            demand=demand.request_snapshot(),
                            demand_source="end",
                            transport_status="partial",
                            window_status="partial",
                            game_status="protocol_skipped")
                    return
                fetch_coordinator.complete(
                    response=None, response_seq=None, error=e)
                raise
            ticket = self._throttle_ticket()
            self._record_throttle(ticket)
            lazy_until = time.monotonic() + LAZY_POLL_WAIT
            if rec is not None:
                self._record_state_req(
                    rec, gid, seq, status, _ms(t0), state_attempts,
                    state_transport, ticket, used_request_kind,
                    summary=self._state_summary(res), plan=plan, demand=demand,
                    requested_seq=physical_seq,
                    transport_request_id=transport_request_id)
            if res.get("finished"):
                if window_confirm is not None:
                    # A finished game is an authoritative terminal boundary,
                    # but it is not a window snapshot.  Do not reinterpret
                    # the missing snapshot as an identity mismatch or emit a
                    # claim miss for a candidate that could not be checked.
                    self._record_window_confirm(
                        gid, window_confirm, "closed",
                        reason="game_finished", seq=res.get("seq"))
                    demand.finish_window_confirm(DEMAND_TERMINAL)
                    window_confirm = None
                elif plan is not None and plan.kind == WINDOW_CONFIRM:
                    demand.finish_window_confirm(DEMAND_TERMINAL)
                fetch_coordinator.complete(
                    response=res, response_seq=res.get("seq"),
                    response_mode=(plan.mode if plan is not None else None),
                    snapshot=res.get("snapshot"))
                demand.close("game_finished")
                demand.finalize_close("game_finished")
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
                        # The current protocol/log stream has no
                        # round_ended settlement marker.  A finished client
                        # loop proves transport/window shutdown only; do not
                        # manufacture game-layer completeness here.
                        game_status="protocol_skipped")
                self._record_demand_metrics(demand, demand_seen)
                return
            if res.get("pending"):
                # A pending long-poll response has no mirror work to apply;
                # its returned watermark is still a valid delta completion.
                if plan is not None:
                    fetch_coordinator.complete(
                        response=res, response_seq=res.get("seq"),
                        response_mode=plan.mode,
                        snapshot=res.get("snapshot"))
                continue
            if res.get("gap"):
                with self._stats_lock:
                    self.stats["gaps"] += 1
                pending_source_contiguous = False
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
                    mirror = self._mirror_from_snapshot(snap, gid=gid)
                    self._record_replay_trace(
                        gid, "checkpoint", {"snapshot": snap},
                        round_no=mirror.round_no, seq_no=res.get("seq", seq),
                        state_after=self._trace_mirror_state(mirror),
                        outcome="SNAPSHOT")
                    self._reconcile_uncertain_snapshot(
                        gid, mirror, snap, seq=res.get("seq", seq))
                    # seq=0 is only a request mode.  RESYNC is satisfied
                    # after the authoritative snapshot has actually rebuilt
                    # the mirror, not merely because the request was FULL.
                    demand.finish_resync(SATISFIED)
                    # /state seq is a stream watermark, not the source
                    # discard identity.  Carry the latest parsed discard
                    # sequence only when this authoritative snapshot still
                    # describes that exact pending tile; otherwise start
                    # without a synthetic identity and let the snapshot's
                    # explicit source field (if any) win.
                    source_seq, source_origin = self._source_identity(snap)
                    if (source_seq is None and pending_source is not None
                            and pending_source_contiguous):
                        (source_round, source_owner, source_tile, source_seq,
                         source_origin) = \
                            pending_source
                        if (source_round != mirror.round_no
                                or mirror.pending != (source_owner,
                                                      source_tile)):
                            source_seq = None
                            source_origin = None
                        elif source_seq is not None:
                            # The event identity survived a seq=0 re-anchor;
                            # preserve an explicit protocol origin, but mark
                            # a bare tile event sequence as carried.
                            if source_origin == "tile_discard_event_seq":
                                source_origin = "carried_event_seq"
                    if source_seq is not None:
                        mirror._source_discard_seq = source_seq
                        mirror._source_discard_origin = (
                            source_origin or "carried_event_seq")
                    mirror._attempted_windows = attempted_windows
                    mirror._window_decisions = window_decisions
                    mirror._legacy_attempts = legacy_attempts
                    mirror._legacy_failed = legacy_failed
                    mirror._legacy_epoch = legacy_epoch
                    next_seat = None  # 快照后动作者未知,懒门转急直至事件重建
                    confirm_outcome = None
                    pending_confirm = window_confirm
                    if pending_confirm is None and plan is not None \
                            and plan.kind == WINDOW_CONFIRM:
                        pending_confirm = self._window_confirm_from_reason(
                            plan.reasons.get(WINDOW_CONFIRM))
                    if pending_confirm is not None:
                        window_confirm = None
                        confirm_outcome = self._resolve_window_confirm(
                            gid, mirror, snap, pending_confirm,
                            seq=res.get("seq", seq))
                        confirm_status = self._demand_window_status(
                            confirm_outcome)
                        demand.finish_window_confirm(confirm_status)
                        if confirm_status == PENDING:
                            # Missing authority is not proof that the window
                            # closed.  Retain the same expected identity for
                            # the next completion instead of silently
                            # downgrading it to a phase-only check.
                            window_confirm = pending_confirm
                    # 快照是新的事实边界；旧批次的 trigger/chi 等待态
                    # 不能跨边界携带。已成功/已尝试的响应身份保留在本
                    # 局循环内，防止 seq=0 后重复 POST。
                    chi_pending = self._act_on_snapshot(
                        mirror, snap, gid,
                        responded_keys=responded_windows,
                        attempted_keys=attempted_windows,
                        pending_confirm=pending_confirm,
                        confirm_outcome=confirm_outcome,
                        snapshot_seq=res.get("seq", seq),
                        # A phase transition during a chi confirmation is
                        # terminal for that old reason, but the same
                        # authoritative snapshot may be the first usable
                        # response_peng fact for the new phase.  Let the
                        # normal action guards inspect it; they still require
                        # responding_seats and an exact deadline.
                        allow_peng=(confirm_outcome in (
                            None, "confirmed", "stale", "closed",
                            "phase_pending")))
                    # A confirmation is a request to make one decision, not
                    # an independent lifecycle.  Once the current snapshot
                    # produced a PASS or a successful physical action, close
                    # the same WindowAttemptKey before the next loop can
                    # create another FULL successor.  The resolver also
                    # performs this check for a later draw snapshot.
                    if (pending_confirm is not None
                            and self._window_confirm_completed(
                                mirror, pending_confirm)):
                        demand.finish_window_confirm(SATISFIED)
                        window_confirm = None
                        confirm_outcome = "confirmed"
                    if chi_pending is not None:
                        next_chi = chi_pending
                        next_confirm = self._window_confirm_from_chi(next_chi)
                        window_confirm = self._carry_window_confirm_budget(
                            pending_confirm, next_confirm)
                        chi_pending = None
                        state_deadline = next_chi.get("deadline_mono")
                        request_kind = "WINDOW_CHI"
                        window_responded = bool(
                            self._window_key(mirror, "response_peng",
                                             snap=snap)
                            in responded_windows)
                        self._wait_wake(wake, sse, max_wait=max(
                            0.0, next_chi["ready_mono"] - time.monotonic()))
                        seq = 0  # 下一次拉取同时承担窗口确认，不另加请求
                    else:
                        state_deadline = None
                    if plan is not None:
                        fetch_coordinator.complete(
                            response=res, response_seq=res.get("seq"),
                            response_mode=plan.mode, snapshot=snap)
                except Exception as ex:
                    if plan is not None and demand.in_flight:
                        fetch_coordinator.complete(
                            response=res, response_seq=res.get("seq"),
                            response_mode=plan.mode, snapshot=snap,
                            error=ex)
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
                        self._record_reset_boundary(
                            rec, gid, f"动作结果需重锚: {ex.reason}", mirror)
                        seq, mirror = 0, None
                        chi_pending = None
                        if ex.reason.startswith("mirror_drift:"):
                            # A malformed hand count invalidates the source
                            # identity as well as the local mirror.
                            pending_source = None
                            pending_source_contiguous = False
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
                    self._record_reset_boundary(
                        rec, gid,
                        f"快照决策异常: {type(ex).__name__}: {ex}", mirror)
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
            # The response has reached the event-application boundary.  Keep
            # the logical request owner until this point; a later action or
            # wake can now safely create a successor.
            applied = mirror is not None
            trigger = None
            batch_seen = set()  # 同批内触发弃牌之后的其他家窗口响应
            window_event = None  # 当前 batch 最后一个仍可响应的弃牌
            active_window_key = None
            peng_timeout = None  # 我方碰窗已由服务端关闭的事件
            chi_timeout = False
            for ev in batch:
                e_seq = ev.get("seq")
                if e_seq is not None:
                    seq = max(seq, e_seq)
                if mirror is None:
                    continue  # 首快照未到,事件留待快照重建
                trace_before = self._trace_mirror_state(mirror)
                trace_payload = {
                    "eventType": ev.get("type"),
                    "seat": ev.get("seat"),
                    "tile": ev.get("tile"),
                    "tiles": (ev.get("data", {}) or {}).get("tiles", []),
                    "input": ev,
                }
                try:
                    e = parse_event(ev)
                    mirror.apply_event(ev)
                except MirrorInconsistent as ex:
                    self._record_replay_trace(
                        gid, "transition", trace_payload,
                        seq_no=e_seq, round_no=mirror.round_no,
                        state_before=trace_before,
                        state_after=self._trace_mirror_state(mirror),
                        outcome="ERROR")
                    applied = False
                    self._log(f"镜像失步({ex}),seq=0 重建")
                    with self._stats_lock:
                        self.stats["mirror_resets"] += 1
                    self._record_reset_boundary(rec, gid, str(ex), mirror)
                    seq, mirror, trigger = 0, None, None
                    pending_source = None
                    pending_source_contiguous = False
                    chi_pending = None
                    state_deadline = None  # 重锚后旧窗截止作废(防滞留虚增 dm)
                    next_seat = None
                    window_responded = True
                    break
                self._record_replay_trace(
                    gid, "transition", trace_payload,
                    seq_no=e_seq, round_no=mirror.round_no,
                    state_before=trace_before,
                    state_after=self._trace_mirror_state(mirror),
                    outcome="COMPLETED")
                self._record_replay_trace(
                    gid, "processing_complete", trace_payload,
                    seq_no=e_seq, round_no=mirror.round_no,
                    state_before=trace_before,
                    state_after=self._trace_mirror_state(mirror),
                    outcome="COMPLETED")
                self._record_timeout(e, mirror.me)
                t = e["type"]
                next_seat = self._next_seat_step(next_seat, e)
                if t == "tile_drawn":
                    # A draw closes the previous discard response window;
                    # retaining its source through a later FULL would allow
                    # a same-tile discard to inherit an old identity.
                    pending_source = None
                    pending_source_contiguous = False
                    self._set_source_identity(mirror, None)
                    active_window_key = None
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
                    source_seq, source_origin = self._source_identity(e)
                    pending_source = (mirror.round_no, e["seat"], e["tile"],
                                      source_seq, source_origin)
                    pending_source_contiguous = source_seq is not None
                    self._set_source_identity(mirror, source_seq, source_origin)
                    active_window_key = self._window_key(
                        mirror, "response_peng", ev=ev)
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
                    if (active_window_key is not None
                            and e.get("seat") != mirror.me):
                        # The opponent's accepted claim is a rule terminal
                        # for both phases of this discard.  This is not a
                        # client loss and must not be emitted as claim_miss.
                        for claim_phase in ("response_peng", "response_chi"):
                            preempt_key = WindowAttemptKey(
                                active_window_key.window_id, claim_phase)
                            self._record_window_lifecycle(
                                gid, preempt_key, "terminal",
                                state="PREEMPTED", outcome="RULE_PREEMPTED",
                                terminal_reason="opponent_claim",
                                terminal_observed_at=time.time(),
                                terminal_seq=e.get("seq"))
                    pending_source = None
                    pending_source_contiguous = False
                    self._set_source_identity(mirror, None)
                    active_window_key = None
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
                    self._record_reset_boundary(
                        rec, gid, "hu_failed:吃碰后弃牌被跳过", mirror)
                    seq, mirror, trigger = 0, None, None
                    pending_source = None
                    pending_source_contiguous = False
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
                    pending_source_contiguous = False
                    self._set_source_identity(mirror, None)
                    active_window_key = None
                    # 本轮已结束(含服务端自动结算的杠开等):同批未消费的
                    # draw/window 触发一律作废,否则仍会按陈旧摸牌触发决策
                    # 并提交动作必 409(match 实测 2026-09-11,b5);批内
                    # round_ended 之后的后续事件仍可正常重建触发。
                    if trigger is not None:
                        self._log(f"轮局结束:作废未消费触发({trigger[0]})")
                        trigger = None
                    state_deadline = None  # 死窗截止滞留只虚增 dm
                    chi_pending = None
                    window_event = None
                    window_responded = True
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
            if plan is not None:
                fetch_coordinator.complete(
                    response=res, response_seq=res.get("seq"),
                    response_mode=plan.mode, snapshot=None,
                    error=None if applied else RuntimeError(
                        "state response application incomplete"))
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
                        next_confirm = self._window_confirm_from_chi(chi_pending)
                        window_confirm = self._carry_window_confirm_budget(
                            window_confirm, next_confirm)
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
                    next_confirm = self._window_confirm_from_chi(chi_pending)
                    window_confirm = self._carry_window_confirm_budget(
                        window_confirm, next_confirm)
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
                            next_confirm = self._window_confirm_from_chi(
                                chi_pending)
                            window_confirm = self._carry_window_confirm_budget(
                                window_confirm, next_confirm)
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
                    self._record_reset_boundary(
                        rec, gid, f"动作结果需重锚: {ex.reason}", mirror)
                    seq, mirror, trigger = 0, None, None
                    chi_pending = None
                    if ex.reason.startswith("mirror_drift:"):
                        pending_source = None
                        pending_source_contiguous = False
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
                self._record_reset_boundary(
                    rec, gid, f"决策异常: {type(ex).__name__}: {ex}", mirror)
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
        started_epoch = time.time()
        key = (self._window_key(mirror, phase)
               if phase in ("response_peng", "response_chi") else None)
        authorization = self._window_authorization(mirror, key)
        deadline_left_at_start_ms = self._deadline_left_ms(
            authorization.get("exact_deadline_at"))
        logical_request_id = None
        attempt_index = None
        if isinstance(key, WindowAttemptKey):
            logical_request_id = f"window:{gid}:{repr(key.as_tuple())}"
            with self._action_counter_lock:
                attempt_index = self._window_attempt_counters.get(
                    (gid, repr(key.as_tuple())), 0) + 1
            self._record_window_lifecycle(
                gid, key, "decision", state="DECIDING",
                outcome="DECIDING", decision_started_at=started_epoch,
                exact_deadline_at=authorization.get("exact_deadline_at"),
                authorization_snapshot_seq=authorization.get(
                    "authorization_snapshot_seq"))
        with self._decide_lock:
            result = self.decide(g, mirror.me)
        evaluation = None
        if (isinstance(result, tuple) and len(result) == 2
                and isinstance(result[0], int)):
            act, evaluation = result
        else:
            act = result
        finished_epoch = time.time()
        decision_result = ("PASS" if act == -1 else "NON_PASS_ACTION")
        if isinstance(key, WindowAttemptKey):
            self._record_window_lifecycle(
                gid, key, "decision", state=("DECIDED_PASS"
                                              if act == -1
                                              else "DECIDED_ACTION"),
                outcome=decision_result, decision_finished_at=finished_epoch,
                decision_action=act,
                exact_deadline_at=authorization.get("exact_deadline_at"),
                deadline_left_at_start_ms=deadline_left_at_start_ms,
                deadline_left_at_finish_ms=self._deadline_left_ms(
                    authorization.get("exact_deadline_at")))
            if key.window_id.identity_status == "legacy_unresolved":
                # window-snapshot-identity-decision: the deciding key was a
                # weak epoch key, not a protocol identity.  Counted apart so
                # acceptance can distinguish weak-key decision outcomes.
                with self._stats_lock:
                    self.stats["weak_key_decisions"] += 1
        if self.recorder is not None:
            material_counts, material_source, material_status = (
                mirror.public_material_projection())
            kwargs = {"digest": {
                "hand": int(sum(mirror.my_hand)),
                "wall": mirror.live_wall_left(),
                "round_no": mirror.round_no,
                "public_material_status": material_status,
                "public_material_source": material_source,
            }}
            if isinstance(key, WindowAttemptKey):
                kwargs.update({
                    "window_id": key.window_id.as_json(),
                    "window_attempt_key": key.as_json(),
                    "identity_status": key.window_id.identity_status,
                    "identity_origin": key.window_id.identity_origin,
                    "first_seen_via": key.window_id.first_seen_via,
                    "logical_request_id": logical_request_id,
                    "attempt_index": attempt_index,
                    "authorization_snapshot_seq": authorization.get(
                        "authorization_snapshot_seq"),
                    "authorization_phase": authorization.get(
                        "authorization_phase"),
                    "authorization_responding_seats": authorization.get(
                        "authorization_responding_seats"),
                    "exact_deadline_at": authorization.get(
                        "exact_deadline_at"),
                    "decision_started_at": started_epoch,
                    "decision_finished_at": finished_epoch,
                    "deadline_left_at_start_ms": deadline_left_at_start_ms,
                    "deadline_left_at_finish_ms": self._deadline_left_ms(
                        authorization.get("exact_deadline_at")),
                    "decision_result": decision_result,
                })
            if evaluation is not None:
                kwargs["evaluation"] = _compact_evaluation(evaluation)
            try:
                params = inspect.signature(self.recorder.decision).parameters
            except (TypeError, ValueError):
                params = {}
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in params.values())
            if not accepts_kwargs:
                kwargs = {key: value for key, value in kwargs.items()
                          if key in params}
            decision_id = self.recorder.decision(
                gid, phase, legal, act, _ms(t0), **kwargs)
            if isinstance(key, WindowAttemptKey):
                mirror._window_decision_ids = getattr(
                    mirror, "_window_decision_ids", {})
                mirror._window_decision_ids[key] = decision_id
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

    @staticmethod
    def _last_transport_attempt(transport, kind="action"):
        if not isinstance(transport, dict):
            return {}
        attempts = transport.get(f"{kind}_attempts") or []
        return attempts[-1] if attempts else {}

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
                identity_origin=source.identity_origin,
                first_seen_via=source.first_seen_via,
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
            identity_origin = (getattr(confirm, "source_origin", None)
                               or "unknown")
        else:
            identity_status = "legacy_unresolved"
            fallback = ("confirm", confirm.round_no, tuple(pending))
            identity_origin = "legacy_snapshot"
        return WindowId(
            game_id=gid,
            round_id=confirm.round_no,
            discard_owner=pending[0],
            source_discard_seq=source_seq,
            tile=pending[1],
            identity_status=identity_status,
            fallback=fallback,
            identity_origin=identity_origin,
            first_seen_via=(getattr(confirm, "first_seen_via", None)
                            or "unknown"),
        )

    def _prepare_state_demand(self, demand, gid, seq, request_kind,
                              deadline, window_confirm, chi_pending,
                              coordinator=None):
        """Merge the current loop intent and choose the physical request."""
        if window_confirm is not None:
            demand.submit_window_confirm(
                self._window_id_for_demand(gid, confirm=window_confirm),
                window_confirm.phase,
                deadline=window_confirm.schedule_deadline,
            )
            window_confirm.generation = demand.generation
            reason = demand.reasons.get(WINDOW_CONFIRM) or {}
            if getattr(window_confirm, "confirmation", None) is not None:
                window_confirm.confirmation.revision = reason.get("revision")
        elif chi_pending is not None:
            demand.submit_window_confirm(
                self._window_id_for_demand(
                    gid, window_key=chi_pending.get("key")),
                "response_chi",
                deadline=chi_pending.get("deadline_mono"),
            )
            chi_pending["demand_generation"] = demand.generation
            reason = demand.reasons.get(WINDOW_CONFIRM) or {}
            chi_pending["demand_revision"] = reason.get("revision")
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
        default_kind = (RESYNC if request_kind == "RESYNC"
                        else WINDOW_CONFIRM
                        if request_kind in ("WINDOW_PENG", "WINDOW_CHI")
                        else SSE_DELTA)
        # Keep the pre-admission candidate separate from the frozen request.
        # A confirmation or RESYNC submitted above can still upgrade this
        # candidate before the admission boundary; no throttle permit or HTTP
        # attempt is consumed by this bookkeeping step.
        if coordinator is not None:
            return coordinator.begin(default_seq=seq, default_kind=default_kind,
                                     deadline=deadline)
        demand.queue_candidate(default_seq=seq, default_kind=default_kind,
                               deadline=deadline)
        return demand.admit_candidate()

    @staticmethod
    def _demand_window_status(outcome):
        if outcome == "confirmed":
            return SATISFIED
        # Missing authoritative fields do not prove that the window closed.
        # Keep the exact expected WindowId/phase pending for one more
        # completion; phase/identity/deadline closure is terminal.
        if outcome in ("unconfirmed", "identity_unconfirmed",
                       "phase_pending"):
            return PENDING
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
        # WINDOW_CONFIRM without a resolver result remains pending.  A
        # phase-only shortcut would incorrectly satisfy chi confirmations.
        return demand.has_pending

    def _state(self, gid, seq, deadline, request_kind=None, plan=None,
               transport_request_id=None, state_ticket=None,
               candidate_id=None, state_throttle=None, cancel_check=None):
        """向真实 Api 传截止；request_kind 留在逻辑请求日志中。"""
        from .api import Api
        if isinstance(self.api, Api):
            kwargs = {"deadline": deadline}
            if plan is not None:
                kwargs.update({
                    "logical_request_id": plan.logical_request_id,
                    "reason": list(plan.reasons),
                    "generation": plan.started_generation,
                    "transport_request_id": transport_request_id,
                    "state_ticket": state_ticket,
                    "candidate_id": candidate_id,
                    "state_throttle": state_throttle,
                    "cancel_check": cancel_check,
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
                          plan=None, demand=None, requested_seq=None,
                          transport_request_id=None):
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
                "candidate_id": plan.candidate_id,
                "candidate_created_at": plan.candidate_created_at,
                "queued_at": plan.queued_at,
                "admitted_at": plan.admitted_at,
                "successor_of": plan.successor_of,
                "transport_request_id": transport_request_id,
                "effective_deadline": plan.effective_deadline,
                "deadline_source": getattr(
                    plan, "deadline_source", "unknown"),
                "reason_revisions": {
                    reason: data.get("revision")
                    for reason, data in plan.reasons.items()
                },
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
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in params.values())
        if not accepts_kwargs and params:
            kwargs = {key: value for key, value in kwargs.items()
                      if key in params}
        rec.req(gid, seq, status, latency_ms, attempts, **kwargs)

    @staticmethod
    def _record_state_reconcile(rec, gid, request, snapshot, pending,
                                 error=None):
        """Record facts known only after a state response was applied.

        This is best effort and signature-filtered so old replay fakes remain
        valid while the real Recorder can persist the full lifecycle view.
        """
        writer = getattr(rec, "state_reconcile", None) if rec is not None else None
        if writer is None or request is None:
            return
        kwargs = {
            "logical_request_id": getattr(request, "logical_request_id", None),
            "evaluated_revisions": getattr(request, "evaluated_revisions", None),
            "satisfied_reasons": list(
                getattr(request, "satisfied_reasons", ()) or ()),
            "response_applied_at": getattr(request, "response_applied_at", None),
            "pending": pending,
            "lifecycle": (snapshot.get("lifecycle")
                           if isinstance(snapshot, dict) else None),
            "error": error,
        }
        try:
            params = inspect.signature(writer).parameters
        except (TypeError, ValueError):
            params = {}
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in params.values())
        if not accepts_kwargs and params:
            kwargs = {key: value for key, value in kwargs.items()
                      if key in params}
        try:
            writer(gid, **kwargs)
        except Exception:
            pass

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
        keys = ("logical_demands", "logical_input_demands",
                "coalesced_demands",
                "successor_requests", "physical_state_requests",
                "logical_state_requests", "physical_state_attempts",
                "substituted_candidates", "cancelled_before_send",
                "suppressed_duplicates", "coalesced_or_suppressed")
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

    def _mirror_from_snapshot(self, snap, gid=None):
        mirror = Mirror(my_seat=snap["seat"], dealer=snap.get("dealer", 0),
                       base=self.base,
                       you_cai_bi_kao=self.you_cai_bi_kao,
                       round_no=snap.get("round_no", 1))
        # The worker's gid is the stable room identity.  Snapshot payloads
        # from older protocol versions may omit game_id; injecting it here
        # prevents WindowId from changing between snapshot and event paths.
        mirror._game_id = gid if gid is not None else snap.get("game_id")
        mirror.apply_snapshot(snap)  # 全量锚定(手牌/公共状态/墙长)
        return mirror

    @staticmethod
    def _confirm_allows_snapshot_phase(confirm, outcome, phase):
        """Gate snapshot actions by the confirmation that requested them."""

        if confirm is None:
            return True
        if outcome == "confirmed" and phase == confirm.phase:
            return True
        # A chi confirmation may legitimately observe the preceding peng
        # phase of the same WindowId; that is pending, not a new action grant.
        if (confirm.phase == "response_chi" and phase == "response_peng"
                and outcome == "phase_pending"):
            return True
        # A peng confirmation is terminal once the same window has advanced
        # to chi, but that snapshot is the authoritative fact for the new
        # phase and may be handled by the chi path.
        if (confirm.phase == "response_peng" and phase == "response_chi"
                and outcome == "closed"):
            return True
        return False

    def _act_on_snapshot(self, mirror, snap, gid, responded_keys=None,
                         attempted_keys=None, allow_peng=True,
                         pending_confirm=None, confirm_outcome=None,
                         snapshot_seq=None):
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
            if not self._confirm_allows_snapshot_phase(
                    pending_confirm, confirm_outcome, phase):
                return None
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
            return self._act_window(mirror, snap, gid,
                                    snapshot_seq=snapshot_seq)
        if phase == "response_chi" \
                and seat in (snap.get("responding_seats") or []):
            if not self._confirm_allows_snapshot_phase(
                    pending_confirm, confirm_outcome, phase):
                return None
            if self._snapshot_deadline(snap) is None:
                key = self._window_key(mirror, "response_chi", snap=snap)
                self._state_abandon(
                    gid, "response_chi", "快照缺少有效窗口截止",
                    dedupe_key=(gid, "response_chi",
                                key.as_tuple() if isinstance(
                                    key, WindowAttemptKey)
                                else (mirror.round_no, mirror.pending)))
                return None
            key = self._window_key(mirror, "response_chi", snap=snap)
            chi = self._make_chi_pending(
                mirror, snap=snap, responded_keys=responded_keys,
                attempted_keys=attempted_keys, phase_hint="response_chi")
            if chi is not None:
                self._set_window_authorization(
                    mirror, key, snap, seq=snapshot_seq,
                    legal=chi.get("legal"))
                self._act_chi(mirror, chi, gid)
            return None
        return None

    def _skip_drifted(self, mirror, gid, phase):
        """手牌张数漂移时停止使用旧镜像并请求一次 FULL 重锚。

        张数不自洽意味着本地无法判断合法动作；把它直接记成
        ``auto_played`` 会把客户端状态错误伪装成服务端代打，并且继续
        使用同一个 Mirror 会在后续窗口重复放大计数。  通过
        ``_ActionResync`` 交回主循环，下一次请求从 seq=0 重建。
        """
        self._log(f"手牌张数 {sum(mirror.my_hand)} 与阶段 {phase} 不符,"
                  f"停止决策并请求 seq=0 重锚")
        with self._stats_lock:
            self.stats["client_state_abandons"] += 1
            self.stats["mirror_drift_resets"] += 1
            self.stats["state_drift_auto_played"] += 1
            # Scripted legacy adapters historically counted this handoff as a
            # compatibility auto-play.  Production match metrics must keep
            # ``auto_played`` reserved for observed/attributed server turns;
            # the explicit drift counter above preserves the diagnostic fact.
            if self.mode != "match":
                self.stats["auto_played"] += 1
        # Keep the deterministic legacy/replay adapter's historical
        # continue-in-place behaviour.  Production match workers opt into the
        # hard re-anchor below via ``mode=\"match\"``; this compatibility
        # branch lets old scripted servers emit their trailing timeout rows
        # and keeps their auto-played fixture counts stable.
        if self.mode != "match":
            return
        raise _ActionResync(
            f"mirror_drift:{phase}:hand_count={sum(mirror.my_hand)}")

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
        # A post-claim ``phase=draw`` snapshot can arrive before the next
        # tile_drawn event.  In particular, a catch-play/freeze snapshot
        # makes Game.legal_actions() expose the not-yet-known drawn tile as
        # ``None``.  That is a wait state, not a decision point: passing it
        # to a strategy can produce a None action and an invalid POST.  Do
        # not reject every draw snapshot with ``drawn=None``: legacy/fake
        # snapshots can still carry a complete actionable hand.
        legal = g.legal_actions()
        if not legal or any(action is None for action in legal):
            return
        act = self._decide_logged(g, mirror, "draw", gid)
        if ev is not None and ev.get("ts") is not None \
                and ev["ts"] + DISCARD_SEC - time.time() <= SUBMIT_EPS:
            self._deadline_abandon(gid, "draw", "弃牌窗在决策后已关闭")
            return
        self._submit(mirror, gid, act, "draw")

    def _act_window(self, mirror, ev, gid, phase=None, snapshot_seq=None):
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
            confirm_key = self._window_key(
                mirror, "response_peng", ev=ev,
                snap=ev if isinstance(ev, dict) and "phase" in ev else None)
            confirm_window = (confirm_key.window_id
                              if isinstance(confirm_key, WindowAttemptKey)
                              else None)
            confirm_source_seq = (confirm_window.source_discard_seq
                                  if confirm_window is not None else None)
            confirm_source_origin = (confirm_window.identity_origin
                                     if confirm_window is not None else
                                     self._source_identity(ev)[1])
            confirm_first_seen = (confirm_window.first_seen_via
                                  if confirm_window is not None else
                                  ("event" if self._source_identity(ev)[0]
                                   is not None else "unknown"))
            # window-snapshot-identity-decision: a snapshot that exposes the
            # pending window with an exact deadline authorizes the decision
            # even under a weak epoch key -- the window is ledgered as
            # legacy_unresolved and never promoted, and a wrong weak-key
            # POST is bounded by the server's 409 validation plus the
            # existing same-loop recovery.  Without this leg a snapshot-
            # first peng window would raise a fresh confirmation per pull
            # and never reach the policy at all.
            snapshot_authoritative = (
                isinstance(ev, dict)
                and ev.get("phase") in ("response_peng", "response_chi")
                and self._snapshot_deadline(ev) is not None
                and mirror.me in (ev.get("responding_seats") or [])
                and (self._strong_window_key(confirm_key)
                     or (isinstance(confirm_key, WindowAttemptKey)
                         and confirm_key.window_id.identity_status
                         == "legacy_unresolved")))
            if snapshot_authoritative:
                self._set_window_authorization(
                    mirror, confirm_key, ev, seq=snapshot_seq,
                    legal=claims)
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
                    source_seq=confirm_source_seq, source_ts=stamp,
                    source_watermark=(ev or {}).get("seq"),
                    source_origin=confirm_source_origin,
                    first_seen_via=confirm_first_seen,
                    schedule_deadline=self._confirm_schedule_deadline(stamp),
                    window_key=confirm_key,
                    reason="catch_play_confirmation")
            # A discard event is a wakeup, not action authorization.  Any
            # non-authoritative event path must obtain phase, responding seat,
            # exact deadline and source identity from a snapshot first.
            if not snapshot_authoritative:
                stamp = self._epoch_seconds((ev or {}).get("ts"))
                raise _WindowConfirm(
                    phase="response_peng", pending=mirror.pending,
                    round_no=mirror.round_no, legal=claims,
                    source_seq=confirm_source_seq, source_ts=stamp,
                    source_watermark=(ev or {}).get("seq"),
                    source_origin=confirm_source_origin,
                    first_seen_via=confirm_first_seen,
                    schedule_deadline=self._confirm_schedule_deadline(stamp),
                    window_key=confirm_key,
                    reason="discard_event_confirmation")
            # 秒级 ts 的 T+1 只是最早可能关闭点：临界时确认快照，
            # 不把估计当成已超时，也不盲发迟到的动作。
            stamp = self._epoch_seconds((ev or {}).get("ts"))
            if (self._snapshot_deadline(ev) is None and stamp is not None
                    and stamp + WINDOW_SEC - time.time() <= SUBMIT_EPS):
                # 尚未完成策略决策，不能判定为“策略想做”。
                raise _WindowConfirm(
                    phase="response_peng", pending=mirror.pending,
                    round_no=mirror.round_no, legal=claims,
                    source_seq=confirm_source_seq, source_ts=stamp,
                    source_watermark=(ev or {}).get("seq"),
                    source_origin=confirm_source_origin,
                    first_seen_via=confirm_first_seen,
                    schedule_deadline=self._confirm_schedule_deadline(stamp),
                    window_key=confirm_key)
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
                        source_seq=confirm_source_seq, source_ts=stamp,
                        source_watermark=(ev or {}).get("seq"),
                        source_origin=confirm_source_origin,
                        first_seen_via=confirm_first_seen,
                        chosen=act,
                        schedule_deadline=self._confirm_schedule_deadline(stamp),
                        window_key=confirm_key,
                        reason="decision_boundary")
                if deadline is not None and deadline - time.monotonic() <= SUBMIT_EPS:
                    self._deadline_abandon(
                        gid, "response_peng", "碰窗精确截止已到",
                        legal=claims, chosen=act, mirror=mirror,
                        deadline=deadline)
                    return None
                self._submit(mirror, gid, act, "response_peng", deadline=deadline,
                             window_key=confirm_key, legal=claims)
                return None
            deadline = self._mono_deadline(self._snapshot_deadline(ev))
            self._submit(mirror, gid, -1, "response_peng", deadline=deadline,
                         window_key=confirm_key, legal=claims)
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
        if self._shutdown_requested():
            return
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
            if self._sleep_stop(wait, self._tournament_stop):
                return
        now = time.monotonic()
        if deadline is not None and now >= deadline - SUBMIT_EPS:
            self._deadline_abandon(gid, "response_chi", "吃窗在等待后已关闭",
                                   legal=chi_opts, chosen=act, mirror=mirror,
                                   deadline=deadline)
            return
        self._submit(mirror, gid, act, "response_chi", deadline=deadline,
                     window_key=chi.get("key"), legal=chi_opts)

    def _next_action_counter(self, gid):
        """Return a session-scoped action attempt number.

        Mirror objects are intentionally replaced after every FULL snapshot;
        keeping this counter on Mirror therefore reuses ids after re-anchor.
        """
        with self._action_counter_lock:
            value = self._action_counters.get(gid, 0) + 1
            self._action_counters[gid] = value
            return value

    def _next_window_attempt_counter(self, gid, key):
        """Return a physical attempt index for one logical window key."""
        token = repr(key.as_tuple())
        with self._action_counter_lock:
            map_key = (gid, token)
            value = self._window_attempt_counters.get(map_key, 0) + 1
            self._window_attempt_counters[map_key] = value
            return value

    def _submit(self, mirror, gid, act, phase, deadline=None, window_key=None,
                legal=None):
        if self._shutdown_requested():
            self._log(f"关闭中，跳过场次 {gid} 的新动作")
            return False
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
                    mirror=mirror, deadline=deadline, window_key=key,
                    action_posted=True)
                return False
            if attempted is not None and self._strong_window_key(key):
                attempted.add(key)
            elif isinstance(key, WindowAttemptKey):
                legacy_attempts = getattr(mirror, "_legacy_attempts", None)
                if legacy_attempts is not None:
                    legacy_attempts[key] = getattr(
                        mirror, "_legacy_epoch", None)
            if isinstance(key, WindowAttemptKey):
                action_attempt_index = self._next_window_attempt_counter(
                    gid, key)
                logical_action_id = (
                    f"window:{gid}:{repr(key.as_tuple())}")
        if logical_action_id is None:
            counter = self._next_action_counter(gid)
            logical_action_id = f"action:{gid}:{counter}"
        authorization = self._window_authorization(mirror, key)
        decision_id = (getattr(mirror, "_window_decision_ids", {}) or {}
                       ).get(key) if isinstance(key, WindowAttemptKey) else None
        window_log = {}
        if isinstance(key, WindowAttemptKey):
            window_log = {
                "window_id": key.window_id.as_json(),
                "window_attempt_key": key.as_json(),
                "identity_status": key.window_id.identity_status,
                "identity_origin": key.window_id.identity_origin,
                "first_seen_via": key.window_id.first_seen_via,
            }
        payload = action_to_payload(
            act, mirror.pending[1] if mirror.pending else None)
        t0 = time.monotonic()
        start_epoch = time.time()
        deadline_left_at_send_ms = (None if deadline is None else round(
            (deadline - t0) * 1000.0, 1))
        deadline_epoch = (None if deadline is None else
                          start_epoch + deadline - t0)
        authorization_age_ms = None
        if authorization.get("authoritative_open_at") is not None:
            try:
                authorization_age_ms = round(
                    (start_epoch - float(
                        authorization["authoritative_open_at"])) * 1000.0, 1)
            except (TypeError, ValueError):
                authorization_age_ms = None
        if isinstance(key, WindowAttemptKey):
            self._record_window_lifecycle(
                gid, key, "post", state="POSTING", outcome="POSTING",
                decision_id=decision_id, post_started_at=start_epoch,
                exact_deadline_at=authorization.get("exact_deadline_at"),
                deadline_left_at_send_ms=deadline_left_at_send_ms)
        try:
            if self._shutdown_requested():
                return False
            from .api import Api
            if isinstance(self.api, Api):
                self.api.game_action(gid, payload, deadline=deadline)
            else:
                self.api.game_action(gid, payload)
        except ApiError as e:
            transport = self._transport()
            last_attempt = self._last_transport_attempt(transport)
            response_epoch = time.time()
            deadline_left_at_response_ms = last_attempt.get(
                "deadline_left_at_response_ms")
            if deadline_left_at_response_ms is None:
                deadline_left_at_response_ms = last_attempt.get(
                    "deadline_left_at_response")
            if deadline_left_at_response_ms is None:
                deadline_left_at_response_ms = self._deadline_left_ms(
                    authorization.get("exact_deadline_at"))
            outcome = ("POST_UNCERTAIN" if getattr(e, "uncertain", False)
                       else "POST_REJECTED")
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
                    mirror=mirror, window_key=key,
                    logical_request_id=logical_action_id,
                    action_posted=True)
            if self.recorder is not None:
                self.recorder.action(gid, phase, payload, ok=False,
                                     status=e.status, code=e.code,
                                     latency_ms=_ms(t0), started_at=t0,
                                     started_epoch=start_epoch,
                                     deadline_at=deadline_epoch,
                                     message=getattr(e, "message", ""),
                                     attempts=self._attempts(),
                                     transport=transport,
                                     logical_request_id=logical_action_id,
                                     attempt_index=action_attempt_index,
                                     decision_id=decision_id,
                                     authorization_snapshot_seq=authorization.get(
                                         "authorization_snapshot_seq"),
                                     authorization_phase=authorization.get(
                                         "authorization_phase"),
                                     authorization_responding_seats=
                                     authorization.get(
                                         "authorization_responding_seats"),
                                     authorization_age_ms=authorization_age_ms,
                                     exact_deadline_at=authorization.get(
                                         "exact_deadline_at"),
                                     deadline_left_at_send_ms=
                                     deadline_left_at_send_ms,
                                     deadline_left_at_response_ms=
                                     deadline_left_at_response_ms,
                                     post_started_at=start_epoch,
                                     post_finished_at=response_epoch,
                                     post_status=outcome,
                                     response_epoch=response_epoch,
                                     server_trace_id=last_attempt.get(
                                         "server_trace_id"),
                                     outcome=outcome,
                                     **window_log)
            if isinstance(key, WindowAttemptKey):
                self._record_window_lifecycle(
                    gid, key, "post_result",
                    state=outcome, outcome=outcome,
                    decision_id=decision_id, post_started_at=start_epoch,
                    post_finished_at=response_epoch,
                    exact_deadline_at=authorization.get(
                        "exact_deadline_at"),
                    authorization_age_ms=authorization_age_ms,
                    deadline_left_at_send_ms=deadline_left_at_send_ms,
                    deadline_left_at_response_ms=deadline_left_at_response_ms,
                    server_trace_id=last_attempt.get("server_trace_id"))
                if getattr(e, "uncertain", False):
                    self._remember_uncertain_recovery(
                        gid, key, payload, mirror)
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
            transport = self._transport()
            last_attempt = self._last_transport_attempt(transport)
            response_epoch = time.time()
            deadline_left_at_response_ms = last_attempt.get(
                "deadline_left_at_response_ms")
            if deadline_left_at_response_ms is None:
                deadline_left_at_response_ms = last_attempt.get(
                    "deadline_left_at_response")
            outcome = "STRATEGY_PASS" if act == -1 else "SUCCESS"
            self.recorder.action(gid, phase, payload, ok=True,
                                 latency_ms=_ms(t0), started_at=t0,
                                 started_epoch=start_epoch,
                                 deadline_at=deadline_epoch,
                                 attempts=self._attempts(),
                                 transport=transport,
                                 logical_request_id=logical_action_id,
                                 attempt_index=action_attempt_index,
                                 decision_id=decision_id,
                                 authorization_snapshot_seq=authorization.get(
                                     "authorization_snapshot_seq"),
                                 authorization_phase=authorization.get(
                                     "authorization_phase"),
                                 authorization_responding_seats=authorization.get(
                                     "authorization_responding_seats"),
                                 authorization_age_ms=authorization_age_ms,
                                 exact_deadline_at=authorization.get(
                                     "exact_deadline_at"),
                                 deadline_left_at_send_ms=
                                 deadline_left_at_send_ms,
                                 deadline_left_at_response_ms=
                                 deadline_left_at_response_ms,
                                 post_started_at=start_epoch,
                                 post_finished_at=response_epoch,
                                 post_status="OK",
                                 response_epoch=response_epoch,
                                 server_trace_id=last_attempt.get(
                                     "server_trace_id"),
                                 outcome=outcome,
                                 **window_log)
        else:
            transport = self._transport()
            last_attempt = self._last_transport_attempt(transport)
            response_epoch = time.time()
            deadline_left_at_response_ms = last_attempt.get(
                "deadline_left_at_response_ms")
            if deadline_left_at_response_ms is None:
                deadline_left_at_response_ms = last_attempt.get(
                    "deadline_left_at_response")
            outcome = "STRATEGY_PASS" if act == -1 else "SUCCESS"
        if isinstance(key, WindowAttemptKey):
            self._record_window_lifecycle(
                gid, key, "post_result",
                state=("STRATEGY_PASS" if act == -1 else "SUCCESS"),
                outcome=outcome, decision_id=decision_id,
                post_started_at=start_epoch, post_finished_at=response_epoch,
                exact_deadline_at=authorization.get("exact_deadline_at"),
                authorization_age_ms=authorization_age_ms,
                deadline_left_at_send_ms=deadline_left_at_send_ms,
                deadline_left_at_response_ms=deadline_left_at_response_ms,
                server_trace_id=last_attempt.get("server_trace_id"))
        with self._stats_lock:
            self.stats["actions"] += 1
            if payload["action"] == "hu":
                self.stats["hu"] += 1
        return True

    def _action_recovery(self, reason):
        """动作失败后由外层循环 seq=0 重建，不重复提交旧动作。"""
        raise _ActionResync(reason)
