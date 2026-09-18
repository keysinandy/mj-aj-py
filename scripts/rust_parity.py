#!/usr/bin/env python3
"""Rust 内核(mj_kernels)差分验收:与 mj/shanten.py 随机对拍 + 基准。

用法:
    python3 -m pip install -e rust/    # 构建 mj_kernels 扩展
    python3 scripts/rust_parity.py [--n 500] [--e2e]

对拍口径:
- shanten:locked 0..4 × {13 张暗牌, 14 张暗牌} × {有/无财神},含张数
  不符的 ValueError 对拍。
- ukeire:仅 need 尺寸手牌(bot 弃牌候选的真实入参口径)× {有/无财神}
  × 随机 visible(含 None)。三元组 (s, acc, total) 逐位相等,acc
  必须同为升序列表。
- 已知不覆盖:14 张暗牌且 s==0 的 ukeire(Rust 走张数校验会抛
  ValueError,Python 走 win.py is_win 不抛)——真实调用不触达,
  见 rust/src/lib.rs 模块注释。

--e2e:把 Rust 内核 monkeypatch 进 mj.bot 跑同种子自博弈,对照
纯 Python 版的对局轨迹与吞吐(轨迹必须逐局同 winner/mult)。
"""

import argparse
import random
import sys
import time

sys.path.insert(0, ".")

from mj.tiles import W
import mj.shanten as py_sh


def hand(rng, n, wilds):
    """随机 n 张手牌,含 wilds 张财神(同种 ≤4;超出 n 自动钳制)。"""
    wilds = min(wilds, n)
    c = [0] * 34
    pool = [t for t in range(33) for _ in range(4)]  # 无财神部分
    for t in rng.sample(pool, n - wilds):
        c[t] += 1
    c[W] = wilds
    return c


def vis_variant(rng, c):
    """随机已见计数(≥ 手牌自身,再加若干他家已见)。"""
    v = list(c)
    for t in rng.sample(range(34), 8):
        v[t] += rng.randrange(3)
    return v


def parity_shanten(rs, rng, n):
    checked = 0
    for locked in range(5):
        need = 13 - 3 * locked
        for extra in (0, 1):
            for wilds in (0, 1, 2):
                for _ in range(n):
                    c = hand(rng, need + extra, wilds)
                    assert rs.shanten(c, locked) == py_sh.shanten_py(c, locked), \
                        f"shanten 不符: {c} locked={locked}"
                    checked += 1
    # 张数不符(相对该 locked 的非法值):两侧都应抛 ValueError
    for locked in (0, 1, 3):
        need = 13 - 3 * locked
        for bad_n in (need - 1, need + 2):
            c = [0] * 34
            c[0] = bad_n
            for fn in (rs.shanten, py_sh.shanten_py):
                try:
                    fn(c, locked)
                    raise AssertionError(f"张数 {bad_n}/locked={locked} 未抛 ValueError ({fn})")
                except ValueError:
                    pass
    return checked


def parity_ukeire(rs, rng, n):
    checked = 0
    for locked in range(5):
        need = 13 - 3 * locked
        for wilds in (0, 1, 2):
            for _ in range(n):
                c = hand(rng, need, wilds)
                for vis in (None, vis_variant(rng, c)):
                    a = rs.ukeire(c, locked, vis)
                    b = py_sh.ukeire_py(c, locked, vis)
                    assert a == b, f"ukeire 不符: {c} locked={locked} vis={vis}\n  rust={a}\n  py  ={b}"
                    assert a[1] == sorted(a[1]), f"acc 非升序: {a}"
                    checked += 1
    return checked


def parity_baotou(rs, rng, n):
    """爆头进张(baotou_ukeire):Rust 快判刻画(去财神后面子组/七对
    路径)与 Python 34 次 is_win 定义版逐位对拍,含 W>=3 不剪枝分支
    与随机 visible。"""
    checked = 0
    for locked in range(5):
        need = 13 - 3 * locked
        for wilds in (0, 1, 2, 3, 4):
            for _ in range(n):
                c = hand(rng, need, wilds)
                for vis in (None, vis_variant(rng, c)):
                    a = rs.baotou_ukeire(c, locked, vis)
                    b = py_sh.baotou_ukeire_py(c, locked, vis)
                    assert a == b, f"baotou_ukeire 不符: {c} locked={locked} vis={vis}\n  rust={a}\n  py  ={b}"
                    assert a[0] == sorted(a[0]), f"acc 非升序: {a}"
                    checked += 1
    return checked


def bench(rs, rng, n):
    hands = []
    for _ in range(n):
        locked = rng.randrange(2)
        hands.append((hand(rng, 13 - 3 * locked, rng.randrange(3)), locked))

    py_sh.clear_caches()
    t0 = time.perf_counter()
    for c, k in hands:
        py_sh.shanten_py(c, k)
    dt_py = time.perf_counter() - t0

    t0 = time.perf_counter()
    for c, k in hands:
        rs.shanten(c, k)
    dt_rs = time.perf_counter() - t0
    print(f"shanten : Python {dt_py/n*1e6:7.2f}μs/次   Rust {dt_rs/n*1e6:6.2f}μs/次   "
          f"加速 {dt_py/dt_rs:5.1f}x")

    py_sh.clear_caches()
    t0 = time.perf_counter()
    for c, k in hands:
        py_sh.ukeire_py(c, k)
    dt_py = time.perf_counter() - t0
    t0 = time.perf_counter()
    for c, k in hands:
        rs.ukeire(c, k)
    dt_rs = time.perf_counter() - t0
    print(f"ukeire  : Python {dt_py/n*1e6:7.2f}μs/次   Rust {dt_rs/n*1e6:6.2f}μs/次   "
          f"加速 {dt_py/dt_rs:5.1f}x")


def e2e(rs, games):
    """Rust 内核注入 mj.bot 跑自博弈,对照纯 Python 版轨迹与吞吐。

    注:默认路径已接入调度器(mj/shanten.py),Python 侧须显式用
    shanten_py/ukeire_py,否则对照两边都是 Rust。
    """
    import mj.bot as bot
    from mj.game import Game

    def run(sh_fn, uk_fn):
        saved = (bot.shanten, bot.ukeire)
        bot.shanten, bot.ukeire = sh_fn, uk_fn
        try:
            out = []
            t0 = time.perf_counter()
            for s in range(games):
                g = Game(seed=s)
                while not g.done:
                    g.step(bot.choose_action(g, g.current_seat()))
                out.append(g.result if g.result is None else
                            (g.result[0], g.result[1]))
            return out, time.perf_counter() - t0
        finally:
            bot.shanten, bot.ukeire = saved

    py_out, py_dt = run(py_sh.shanten_py, py_sh.ukeire_py)
    print(f"Python 内核: {games} 局 {py_dt:.1f}s  {games/py_dt:.2f} 局/秒")

    rs_out, rs_dt = run(rs.shanten, rs.ukeire)
    print(f"Rust   内核: {games} 局 {rs_dt:.1f}s  {games/rs_dt:.2f} 局/秒  "
          f"加速 {py_dt/rs_dt:.2f}x")
    if rs_out != py_out:
        for i, (a, b) in enumerate(zip(py_out, rs_out)):
            if a != b:
                print(f"轨迹分歧 @seed={i}: py={a} rust={b}")
                break
        sys.exit("对局轨迹不一致!")
    print("对局轨迹:逐局一致(winner/mult)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300, help="每类随机手牌数")
    ap.add_argument("--bench", type=int, default=2000, help="基准手牌数")
    ap.add_argument("--e2e", action="store_true", help="自博弈端到端对照")
    args = ap.parse_args()

    try:
        import mj_kernels as rs
    except ImportError:
        sys.exit("未找到 mj_kernels;先在 rust/ 下执行 maturin develop --release")

    rng = random.Random(20260911)
    n1 = parity_shanten(rs, rng, args.n)
    print(f"shanten 对拍通过: {n1} 手(locked×张数×财神 全交叉)")
    n2 = parity_ukeire(rs, rng, args.n)
    print(f"ukeire 对拍通过: {n2} 手(含 None/随机 visible,acc 升序断言)")
    n3 = parity_baotou(rs, rng, max(60, args.n // 3))
    print(f"baotou_ukeire 对拍通过: {n3} 手(locked×财神 0..4 × None/随机 visible)")

    print(f"\n基准(随机 13 张手 {args.bench} 副,Python 冷缓存):")
    bench(rs, random.Random(99), args.bench)

    if args.e2e:
        print()
        e2e(rs, 30)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
