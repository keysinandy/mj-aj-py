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
import queue
import threading
import time

from .api import ApiError
from .mirror import Mirror, MirrorInconsistent
from .actions import action_to_payload
from .proto import parse_event, tidx

TERMINAL = ("finished", "closed", "void")
WINDOW_SEC = 1.0  # 碰/吃窗口固定走满时长(提交吃牌须等碰窗结束)


def _ms(t0):
    """monotonic 差值 → 毫秒(不受时钟跳变影响)。"""
    return round((time.monotonic() - t0) * 1000.0, 1)


class BotClient:
    """一个令牌一个实例;工作线程并发打 M 场(decide 调用串行加锁)。"""

    def __init__(self, api, name, decide, log=None, window_wait=WINDOW_SEC,
                 idle_sleep=0.35, recorder=None, mode=None,
                 match_retry_wait=10.0, use_notify=False,
                 notify_fallback_wait=15.0, notify_retry_wait=0.5):
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
        self._tid = None
        self.you_cai_bi_kao = False
        self.base = 1
        self._decide_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._done_games = set()
        self.stats = {
            "games": 0, "actions": 0, "hu": 0, "err409": 0, "gaps": 0,
            "auto_played": 0, "mirror_resets": 0, "scores": [],
            "rooms": 0,
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

    def _wait_wake(self, wake, sse):
        """无触发批次时的等待:SSE 在航等帧(长兜底超时),断连期间退回
        idle_sleep 轮询节奏——帧只作唤醒,不当游标(游标纪律见 v12)。"""
        if wake is None:
            time.sleep(self.idle_sleep)
            return
        try:
            wake.get(timeout=self.notify_fallback_wait if sse["alive"]
                     else self.idle_sleep)
        except queue.Empty:
            pass

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
                        wake.put((j.get("seq"), bool(j.get("closed"))))
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
        finally:
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
            wake = queue.Queue()
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

    def _play_loop(self, gid, wake, sse):
        rec = self.recorder
        seq = 0
        mirror = None
        chi_pending = None  # 碰窗响应计数态(吃窗提交前保持)
        while True:
            t0 = time.monotonic()
            try:
                res = self.api.game_state(gid, seq)
                status = 200
            except ApiError as e:
                status = e.status
                if rec is not None:
                    rec.req(gid, seq, status, _ms(t0), self._attempts())
                if e.status == 404:
                    # 场次不可访问(轮次切换/房间回收):视作已结束计数
                    self._log(f"场次 {gid} 已不可访问")
                    with self._stats_lock:
                        self.stats["games"] += 1
                    if rec is not None:
                        rec.end(gid, "inaccessible")
                    return
                raise
            if rec is not None:
                rec.req(gid, seq, status, _ms(t0), self._attempts(),
                        self._state_summary(res))
            if res.get("finished"):
                snap = res.get("snapshot") or {}
                with self._stats_lock:
                    self.stats["games"] += 1
                    if snap.get("scores") is not None:
                        self.stats["scores"].append(snap["scores"])
                self._log(f"场次 {gid} 结束,积分 {snap.get('scores')}")
                if rec is not None:
                    rec.end(gid, "finished", scores=snap.get("scores"))
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
                mirror = self._mirror_from_snapshot(snap)
                self._act_on_snapshot(mirror, snap, gid)
                continue

            if rec is not None and batch:
                rec.events(gid, res.get("seq"), batch)
            trigger = None
            batch_seen = set()  # 同批内触发弃牌之后的其他家窗口响应
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
                    chi_pending = None
                    batch_seen = set()
                    break
                t = e["type"]
                if t == "tile_drawn" and e["seat"] == mirror.me:
                    trigger = ("draw", None)
                    chi_pending = None  # 我方回合推进:旧窗作废
                    batch_seen = set()
                elif t in ("chi", "peng") and e["seat"] == mirror.me:
                    trigger = ("draw", None)  # 吃碰后进入自家弃牌
                    chi_pending = None
                    batch_seen = set()
                elif t == "tile_discarded":
                    if e["seat"] != mirror.me:
                        trigger = ("window", e)
                        batch_seen = set()
                    chi_pending = None  # 任何新弃牌:旧窗作废
                elif t in ("chi", "peng", "gang"):
                    chi_pending = None  # 有人吃/碰/杠:窗口被认领
                    if trigger is not None and trigger[0] == "window":
                        trigger = None  # 同批内认领:窗口已失效
                    batch_seen = set()
                elif t in ("pass", "timeout"):
                    # 碰窗响应(pass 或 response 超时;discard 超时不算)
                    if t == "pass" or e.get("kind") == "response":
                        if (t == "timeout" and e["seat"] == mirror.me
                                and e["data"].get("window") == "chi"):
                            # 我方吃窗被服务端代过:彻底作废
                            # (碰窗显式过/超时后吃窗仍可开,不能清)
                            chi_pending = None
                        else:
                            if chi_pending is not None:
                                chi_pending["seen"].add(e["seat"])
                            if trigger is not None and trigger[0] == "window":
                                batch_seen.add(e["seat"])
            if chi_pending is not None and trigger is None:
                needed = chi_pending["needed"]
                if needed and chi_pending["seen"] >= needed:
                    # 碰窗全部响应完:吃窗(仅当下家),等窗口走满提交
                    self._act_chi(mirror, chi_pending["t0"], gid)
                    chi_pending = None
            if trigger is None:
                self._wait_wake(wake, sse)  # SSE 帧/兜底超时驱动下一轮
                continue
            if trigger[0] == "draw":
                self._act_draw(mirror, gid)
            elif trigger[0] == "window":
                chi_pending = self._act_window(mirror, trigger[1], gid)
                if chi_pending is not None:
                    # 同批内已到的他家窗口响应直接入账(否则要等下批,
                    # 吃窗提交被无谓推迟)
                    chi_pending["seen"] |= batch_seen

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
        mirror.apply_snapshot(snap)  # 全量锚定(手牌/公共状态/墙长)
        return mirror

    def _act_on_snapshot(self, mirror, snap, gid):
        """快照驱动的决策兜底(开局庄家直抽/409 重建后的窗口)。"""
        seat = snap.get("seat", -1)
        phase = snap.get("phase")
        if seat < 0:
            return
        if phase == "draw" and snap.get("turn") == seat:
            self._act_draw(mirror, gid)
        elif phase == "response_peng" \
                and seat in (snap.get("responding_seats") or []):
            self._act_window(mirror, None, gid)
        elif phase == "response_chi" \
                and seat in (snap.get("responding_seats") or []):
            # 快照显示吃窗进行中:窗口已开,直接决策提交
            try:
                g = mirror.build_game("response_chi")
                if any(a != -1 for a in g.legal_actions()):
                    act = self._decide_logged(g, mirror, "response_chi", gid)
                    if act != -1:
                        self._submit(mirror, gid, act, "response_chi")
            except MirrorInconsistent as e:
                self._log(f"吃窗构建失败: {e}")

    def _act_draw(self, mirror, gid):
        """自家弃牌回合(摸牌后或吃碰后)。"""
        try:
            g = mirror.build_game("draw")
        except MirrorInconsistent as e:
            self._log(f"弃牌决策构建失败: {e}")
            return
        act = self._decide_logged(g, mirror, "draw", gid)
        self._submit(mirror, gid, act, "draw")

    def _act_window(self, mirror, ev, gid, phase=None):
        """他家弃牌的碰窗(含明杠);返回吃窗等待态(仅出牌者下家)。

        碰窗立即决策(有碰/明杠选项时);吃窗交由调用方状态机在碰窗
        全部响应后处理(_act_chi)。抓打圈中被冻座位不参与任何反应窗。
        """
        if mirror.freeze > 0 and mirror.me != mirror.freezer:
            return None  # 抓打圈:不能吃碰明杠
        try:
            g = mirror.build_game("response_peng")
            claims = [a for a in g.legal_actions() if a != -1]
        except MirrorInconsistent as e:
            self._log(f"碰窗构建失败: {e}")
            return None
        passed_explicitly = False
        if claims:
            act = self._decide_logged(g, mirror, "response_peng", gid)
            if act != -1:  # 碰/明杠:窗口开启期间立即提交
                self._submit(mirror, gid, act, "response_peng")
                return None
            self._submit(mirror, gid, -1, "response_peng")
            passed_explicitly = True
        # 吃窗:仅出牌者的下家;等碰窗全部响应 + 窗口走满
        if mirror.pending is None \
                or (mirror.pending[0] + 1) % 4 != mirror.me:
            return None
        needed = {mirror.me}
        for o in range(4):
            if o in (mirror.me, mirror.pending[0]):
                continue
            if not (mirror.freeze > 0 and o != mirror.freezer):
                needed.add(o)
        return {"t0": time.time(), "needed": needed,
                "seen": {mirror.me} if passed_explicitly else set()}

    def _act_chi(self, mirror, t0, gid):
        """吃窗决策:等碰窗走满(固定 1s)后提交。"""
        try:
            g = mirror.build_game("response_chi")
            chi_opts = [a for a in g.legal_actions() if a != -1]
        except MirrorInconsistent as e:
            self._log(f"吃窗构建失败: {e}")
            return
        if not chi_opts:
            return
        act = self._decide_logged(g, mirror, "response_chi", gid)
        if act == -1:
            return
        wait = t0 + self.window_wait + 0.05 - time.time()
        if wait > 0:
            time.sleep(wait)
        self._submit(mirror, gid, act, "response_chi")

    def _submit(self, mirror, gid, act, phase):
        payload = action_to_payload(
            act, mirror.pending[1] if mirror.pending else None)
        t0 = time.monotonic()
        try:
            self.api.game_action(gid, payload)
        except ApiError as e:
            if self.recorder is not None:
                self.recorder.action(gid, phase, payload, ok=False,
                                     status=e.status, code=e.code,
                                     latency_ms=_ms(t0),
                                     attempts=self._attempts())
            if e.status != 409:
                raise
            with self._stats_lock:
                self.stats["err409"] += 1
            self._log(f"409(动作竞态/失步): {payload}")
            return
        if self.recorder is not None:
            self.recorder.action(gid, phase, payload, ok=True,
                                 latency_ms=_ms(t0))
        with self._stats_lock:
            self.stats["actions"] += 1
            if payload["action"] == "hu":
                self.stats["hu"] += 1
