//! 杭州麻将 AI 引擎热点内核的 Rust 实现(与 mj/shanten.py 逐行对拍移植)。
//!
//! 只移植纯函数热点:shanten(向听数)、ukeire(进张枚举)与摸牌后
//! 最佳弃牌批量评价。算法、剪枝界、候选剪枝与 Python 版一一对应,
//! 语义由 scripts/rust_parity.py
//! 随机差分验收(对拍口径见该脚本 docstring)。
//!
//! 与 Python 版的已知行为差异(差分脚本不覆盖、调用方不触达):
//! - ukeire 在 s==0 时 Python 走 win.py 的 is_win;Rust 用
//!   shanten(加牌后) == -1 等价判定(等价性由
//!   tests/test_shanten_props.py::test_shanten_minus1_iff_win 保证)。
//!   因此对 14 张暗牌且 s==0 的手,Rust 会因张数校验抛 ValueError
//!   而 Python 不会——真实调用(bot 弃牌候选)只传 13 张手,不触达。
//! - 无记忆化缓存:Rust 单次计算已快于 Python 缓存命中(~22μs)。

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::time::{Duration, Instant};

const W: usize = 33;
type ShantenCacheKey = ([i32; 34], i32);
type FutureCacheKey = ([i32; 34], [i32; 34], i32);
type UkeireCacheKey = ([i32; 34], [i32; 34], i32);
const LEGACY_TWO_PLY_KERNEL_VERSION: &str = "rust-legacy-two-ply-v1";
const WEIGHTED_TWO_PLY_KERNEL_VERSION: &str = "rust-weighted-two-ply-v2";
const WORK_BUDGET_EXCEEDED: &str = "work_budget_exceeded";
const HARD_DEADLINE_EXCEEDED: &str = "hard_deadline";
const STAGE_B_INTERNAL_ERROR: &str = "stage_b_internal_error";
const STAGE_B_WORKER_FAILED: &str = "stage_b_worker_failed";
const DEADLINE_RESERVE_MS: f64 = 2.0;

#[derive(Clone)]
struct FutureDiscard {
    discard: usize,
    shanten: i32,
    tiles: Vec<usize>,
    total: i64,
}

/// Python-facing row for the all-candidate discard frontier.
#[derive(Clone)]
struct FrontierRow {
    discard: usize,
    shanten: i32,
    tiles: Vec<usize>,
    total: i64,
}

#[derive(Clone, Copy)]
struct FutureMetrics {
    shanten: i32,
    ukeire: i64,
}

#[derive(Clone, Copy)]
struct UkeireMetrics {
    ukeire: i64,
    tile_types: i32,
}

#[derive(Default, Clone, Copy)]
struct SearchCounters {
    root_candidates: i64,
    draw_nodes: i64,
    child_nodes: i64,
    shanten_calls: i64,
    ukeire_calls: i64,
    shanten_cache_hits: i64,
    shanten_cache_misses: i64,
    ukeire_cache_hits: i64,
    ukeire_cache_misses: i64,
}

impl SearchCounters {
    /// Sum another set of counters into this one.  Integer sums are
    /// order-independent, so parallel workers aggregate to the same totals as
    /// the sequential path.
    fn merge(&mut self, other: &SearchCounters) {
        self.root_candidates += other.root_candidates;
        self.draw_nodes += other.draw_nodes;
        self.child_nodes += other.child_nodes;
        self.shanten_calls += other.shanten_calls;
        self.ukeire_calls += other.ukeire_calls;
        self.shanten_cache_hits += other.shanten_cache_hits;
        self.shanten_cache_misses += other.shanten_cache_misses;
        self.ukeire_cache_hits += other.ukeire_cache_hits;
        self.ukeire_cache_misses += other.ukeire_cache_misses;
    }
}

/// 未分配自然牌张数 → 该侧最多还能节省的向听数(保守下界,Python
/// shanten.py 同表;rem ∈ [0,14])。
const SAVE: [i32; 15] = [0, 0, 1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 8, 8, 9];

/// m 面子、t 搭子、p 对子、w 财神的向听数(与 shanten.py::_score 对应)。
fn score(mut m: i32, mut t: i32, mut p: i32, mut w: i32, need_melds: i32) -> i32 {
    let sup = w.min(t);
    m += sup;
    w -= sup;
    t -= sup;
    let pair;
    if p >= 1 {
        pair = 1;
        p -= 1;
    } else if w >= 1 {
        pair = 1;
        w -= 1;
    } else {
        pair = 0;
    }
    m += w / 2;
    w %= 2;
    let t_all = t + w + p;
    let gaps = need_melds - m;
    let used_t = t_all.clamp(0, gaps.max(0));
    2 * gaps.max(0) - used_t - pair
}

/// 标准形分解 DFS(与 shanten.py::_std 的 dfs 逐分支对应,含剪枝界)。
fn std_dfs(
    nat: &mut [i32; 33],
    i: usize,
    m: i32,
    t: i32,
    p: i32,
    w: i32,
    rem: i32,
    base: i32,
    floor: i32,
    need_melds: i32,
    best: &mut i32,
) {
    if *best <= floor {
        return;
    }
    // 保守下界:已实现节省 2m+t+p,剩余财神每张最多再省 2,
    // 剩余 rem 张自然牌最多再省 SAVE[rem]
    if base - 2 * m - t - p - 2 * w - SAVE[rem as usize] >= *best {
        return;
    }
    if i >= 33 {
        let s = score(m, t, p, w, need_melds);
        if s < *best {
            *best = s;
        }
        return;
    }
    let c = nat[i];
    if c == 0 {
        std_dfs(nat, i + 1, m, t, p, w, rem, base, floor, need_melds, best);
        return;
    }
    // 刻子(三枚自然牌)
    if c >= 3 {
        nat[i] -= 3;
        std_dfs(
            nat,
            i,
            m + 1,
            t,
            p,
            w,
            rem - 3,
            base,
            floor,
            need_melds,
            best,
        );
        nat[i] += 3;
    }
    // 刻子(两枚 + 1 财神):不可省——score 无"对子+财神成刻"记账,
    // locked 多/gaps 少时严格优于对子+财神作雀头
    if c >= 2 && w >= 1 {
        nat[i] -= 2;
        std_dfs(
            nat,
            i,
            m + 1,
            t,
            p,
            w - 1,
            rem - 2,
            base,
            floor,
            need_melds,
            best,
        );
        nat[i] += 2;
    }
    // 对子(雀头候选)
    if c >= 2 {
        nat[i] -= 2;
        std_dfs(
            nat,
            i,
            m,
            t,
            p + 1,
            w,
            rem - 2,
            base,
            floor,
            need_melds,
            best,
        );
        nat[i] += 2;
    }
    // 顺子(三张连续自然牌)
    if i < 27 && i % 9 <= 6 && nat[i + 1] > 0 && nat[i + 2] > 0 {
        nat[i] -= 1;
        nat[i + 1] -= 1;
        nat[i + 2] -= 1;
        std_dfs(
            nat,
            i,
            m + 1,
            t,
            p,
            w,
            rem - 3,
            base,
            floor,
            need_melds,
            best,
        );
        nat[i] += 1;
        nat[i + 1] += 1;
        nat[i + 2] += 1;
    }
    // 两面搭子
    if i < 27 && i % 9 <= 7 && nat[i + 1] > 0 {
        nat[i] -= 1;
        nat[i + 1] -= 1;
        std_dfs(
            nat,
            i,
            m,
            t + 1,
            p,
            w,
            rem - 2,
            base,
            floor,
            need_melds,
            best,
        );
        nat[i] += 1;
        nat[i + 1] += 1;
    }
    // 坎张搭子
    if i < 27 && i % 9 <= 6 && nat[i + 2] > 0 {
        nat[i] -= 1;
        nat[i + 2] -= 1;
        std_dfs(
            nat,
            i,
            m,
            t + 1,
            p,
            w,
            rem - 2,
            base,
            floor,
            need_melds,
            best,
        );
        nat[i] += 1;
        nat[i + 2] += 1;
    }
    // 孤张(放弃位置 i 剩余的 c 张)
    std_dfs(
        nat,
        i + 1,
        m,
        t,
        p,
        w,
        rem - c,
        base,
        floor,
        need_melds,
        best,
    );
}

fn std_shanten(counts: &[i32; 34], locked: i32) -> i32 {
    let wilds = counts[W];
    let mut nat = [0i32; 33];
    nat.copy_from_slice(&counts[..33]);
    let need_melds = 4 - locked;
    let base = 2 * need_melds;
    let floor = if counts.iter().sum::<i32>() == 13 - 3 * locked {
        0
    } else {
        -1
    };
    let mut best = 9;
    let rem0: i32 = nat.iter().sum();
    std_dfs(
        &mut nat, 0, 0, 0, 0, wilds, rem0, base, floor, need_melds, &mut best,
    );
    best
}

fn chiitoi(counts: &[i32; 34], locked: i32) -> i32 {
    if locked > 0 {
        return 9;
    }
    let wilds = counts[W];
    let mut pairs = 0i32;
    let mut singles = 0i32;
    for i in 0..33 {
        pairs += counts[i] / 2;
        singles += counts[i] % 2;
    }
    // 财神配单张成对;剩余奇数财神可配摸进的任意牌
    let paired = singles.min(wilds);
    pairs += paired;
    let rest = wilds - paired;
    pairs += rest / 2;
    let odd = rest % 2;
    // 任意单张或奇数财神都意味着"摸进一张即可再成一对"
    7 - pairs - i32::from(singles + odd > 0)
}

fn shanten_impl(counts: &[i32; 34], locked: i32) -> Result<i32, String> {
    let n: i32 = counts.iter().sum();
    let need = 13 - 3 * locked;
    if n != need && n != need + 1 {
        return Err(format!("暗牌张数 {n} 与副露不符"));
    }
    let mut s = std_shanten(counts, locked);
    let c = chiitoi(counts, locked);
    if c < s {
        s = c;
    }
    // 13(暗)张手最低听牌(向听 0);-1 只对摸牌后的 14 张成立
    if n == need && s < 0 {
        s = 0;
    }
    Ok(s)
}

fn wildcard_shanten(
    counts: &[i32; 34],
    locked: i32,
    cache: &mut HashMap<ShantenCacheKey, i32>,
) -> Result<i32, String> {
    let key = (*counts, locked);
    if let Some(value) = cache.get(&key).copied() {
        return Ok(value);
    }
    let value = shanten_impl(counts, locked)?;
    cache.insert(key, value);
    Ok(value)
}

/// 无财神时可能降低向听数的摸牌候选(升序;与 shanten.py
/// _ukeire_candidates 对应,数学依据见该处注释)。
fn ukeire_candidates(counts: &[i32; 34]) -> Vec<usize> {
    let mut set = vec![W];
    for t in 0..33 {
        if counts[t] == 0 {
            continue;
        }
        set.push(t); // 同种:对子/刻子/七对
        if t < 27 {
            // 数牌:顺子/两面/坎张只涉及同花色 ±2
            let lo = (t - t % 9) as i32;
            for x in [t as i32 - 2, t as i32 - 1, t as i32 + 1, t as i32 + 2] {
                if lo <= x && x < lo + 9 {
                    set.push(x as usize);
                }
            }
        }
    }
    set.sort_unstable();
    set.dedup();
    set
}

fn ukeire_impl(
    counts: &[i32; 34],
    locked: i32,
    visible: Option<&[i32; 34]>,
) -> Result<(i32, Vec<usize>, i64), String> {
    let s = shanten_impl(counts, locked)?;
    let v = visible.copied().unwrap_or(*counts);
    let left = |t: usize| (4 - v[t]).max(0) as i64;
    if s <= 0 {
        if s == 0 {
            let mut acc = Vec::new();
            for t in 0..34 {
                let mut c2 = *counts;
                c2[t] += 1;
                if shanten_impl(&c2, locked)? == -1 {
                    acc.push(t);
                }
            }
            let total: i64 = acc.iter().map(|&t| left(t)).sum();
            return Ok((s, acc, total));
        }
        return Ok((s, Vec::new(), 0));
    }
    // 有财神不剪枝:财神可配任意新单张成对(七对)或补结构
    let cands: Vec<usize> = if counts[W] > 0 {
        (0..34).collect()
    } else {
        ukeire_candidates(counts)
    };
    let mut acc = Vec::new();
    for t in cands {
        if counts[t] >= 4 {
            continue;
        }
        let mut c2 = *counts;
        c2[t] += 1;
        if shanten_impl(&c2, locked)? < s {
            acc.push(t);
        }
    }
    let total: i64 = acc.iter().map(|&t| left(t)).sum();
    Ok((s, acc, total))
}

fn ukeire_total_impl(
    counts: &[i32; 34],
    locked: i32,
    visible: &[i32; 34],
    shanten_cache: &mut HashMap<ShantenCacheKey, i32>,
) -> Result<(i32, i64), String> {
    let s = wildcard_shanten(counts, locked, shanten_cache)?;
    let left = |t: usize| (4 - visible[t]).max(0) as i64;
    if s <= 0 {
        if s == 0 {
            let mut total = 0i64;
            for t in 0..34 {
                let mut c2 = *counts;
                c2[t] += 1;
                if wildcard_shanten(&c2, locked, shanten_cache)? == -1 {
                    total += left(t);
                }
            }
            return Ok((s, total));
        }
        return Ok((s, 0));
    }
    let cands: Vec<usize> = if counts[W] > 0 {
        (0..34).collect()
    } else {
        ukeire_candidates(counts)
    };
    let mut total = 0i64;
    for t in cands {
        if counts[t] >= 4 {
            continue;
        }
        let mut c2 = *counts;
        c2[t] += 1;
        if wildcard_shanten(&c2, locked, shanten_cache)? < s {
            total += left(t);
        }
    }
    Ok((s, total))
}

fn counted_shanten(
    counts: &[i32; 34],
    locked: i32,
    cache: &mut HashMap<ShantenCacheKey, i32>,
    cache_capacity: usize,
    work_budget: i64,
    hard_deadline: Instant,
    counters: &mut SearchCounters,
) -> Result<i32, String> {
    counters.shanten_calls += 1;
    let key = (*counts, locked);
    if cache_capacity > 0 {
        if let Some(value) = cache.get(&key).copied() {
            counters.shanten_cache_hits += 1;
            return Ok(value);
        }
    }
    if Instant::now() >= hard_deadline {
        return Err(HARD_DEADLINE_EXCEEDED.to_string());
    }
    if counters.shanten_cache_misses >= work_budget {
        return Err(WORK_BUDGET_EXCEEDED.to_string());
    }
    counters.shanten_cache_misses += 1;
    let value = shanten_impl(counts, locked)?;
    if cache_capacity > 0 {
        if cache.len() >= cache_capacity {
            cache.clear();
        }
        cache.insert(key, value);
    }
    Ok(value)
}

fn counted_ukeire_metrics(
    counts: &[i32; 34],
    locked: i32,
    visible: &[i32; 34],
    shanten_cache: &mut HashMap<ShantenCacheKey, i32>,
    ukeire_cache: &mut HashMap<UkeireCacheKey, UkeireMetrics>,
    cache_capacity: usize,
    work_budget: i64,
    hard_deadline: Instant,
    counters: &mut SearchCounters,
) -> Result<UkeireMetrics, String> {
    counters.ukeire_calls += 1;
    let key = (*counts, *visible, locked);
    if cache_capacity > 0 {
        if let Some(value) = ukeire_cache.get(&key).copied() {
            counters.ukeire_cache_hits += 1;
            return Ok(value);
        }
    }
    counters.ukeire_cache_misses += 1;
    if Instant::now() >= hard_deadline {
        return Err(HARD_DEADLINE_EXCEEDED.to_string());
    }
    let s = counted_shanten(
        counts,
        locked,
        shanten_cache,
        cache_capacity,
        work_budget,
        hard_deadline,
        counters,
    )?;
    let left = |t: usize| (4 - visible[t]).max(0) as i64;
    let mut total = 0i64;
    let mut tile_types = 0i32;
    if s <= 0 {
        if s == 0 {
            for t in 0..34 {
                if Instant::now() >= hard_deadline {
                    return Err(HARD_DEADLINE_EXCEEDED.to_string());
                }
                let mut c2 = *counts;
                c2[t] += 1;
                if counted_shanten(
                    &c2,
                    locked,
                    shanten_cache,
                    cache_capacity,
                    work_budget,
                    hard_deadline,
                    counters,
                )? == -1
                {
                    tile_types += 1;
                    total += left(t);
                }
            }
        }
    } else {
        let cands: Vec<usize> = if counts[W] > 0 {
            (0..34).collect()
        } else {
            ukeire_candidates(counts)
        };
        for t in cands {
            if Instant::now() >= hard_deadline {
                return Err(HARD_DEADLINE_EXCEEDED.to_string());
            }
            if counts[t] >= 4 {
                continue;
            }
            let mut c2 = *counts;
            c2[t] += 1;
            if counted_shanten(
                &c2,
                locked,
                shanten_cache,
                cache_capacity,
                work_budget,
                hard_deadline,
                counters,
            )? < s
            {
                tile_types += 1;
                total += left(t);
            }
        }
    }
    let value = UkeireMetrics {
        ukeire: total,
        tile_types,
    };
    if cache_capacity > 0 {
        if ukeire_cache.len() >= cache_capacity {
            ukeire_cache.clear();
        }
        ukeire_cache.insert(key, value);
    }
    Ok(value)
}

/// ``nat``(33 维自然牌) + ``wilds`` 个财神能否恰好组成 ``need`` 个
/// 面子(win.py::_melds 的逐行移植,含 2026-09-11 的顺子三位枚举
/// 修复:最小自然牌 t 可位于顺子第 1/2/3 位,t 位必须用真牌)。
fn melds_complete(nat: &mut [i32; 33], wilds: i32, need: i32) -> bool {
    let mut t = 0usize;
    while t < 33 && nat[t] == 0 {
        t += 1;
    }
    if t == 33 {
        return wilds == 3 * need;
    }
    if need == 0 {
        return false;
    }
    // 刻子分支:k 张 t + (3-k) 个财神
    let kmax = nat[t].min(3);
    let mut k = kmax;
    while k > 0 {
        let w = 3 - k;
        if wilds >= w {
            nat[t] -= k;
            let ok = melds_complete(nat, wilds - w, need - 1);
            nat[t] += k;
            if ok {
                return true;
            }
        }
        k -= 1;
    }
    // 顺子分支:t 是当前最小自然牌,可位于顺子第 1/2/3 位——更小的
    // 起始位只能由财神补(t 之前更小的自然牌必为 0)。
    for s in [t as i32, t as i32 - 1, t as i32 - 2] {
        if s < 0 || s >= 27 || s % 9 > 6 {
            continue;
        }
        for u1 in [1, 0] {
            for u2 in [1, 0] {
                for u3 in [1, 0] {
                    // t 所在位必须用真牌(财神与同值真牌可互换)
                    let units = [u1, u2, u3];
                    if units[(t as i32 - s) as usize] == 0 {
                        continue;
                    }
                    if u1 == 1 && nat[s as usize] == 0 {
                        continue;
                    }
                    if u2 == 1 && nat[(s + 1) as usize] == 0 {
                        continue;
                    }
                    if u3 == 1 && nat[(s + 2) as usize] == 0 {
                        continue;
                    }
                    let w = 3 - u1 - u2 - u3;
                    if wilds < w {
                        continue;
                    }
                    nat[s as usize] -= u1;
                    nat[(s + 1) as usize] -= u2;
                    nat[(s + 2) as usize] -= u3;
                    let ok = melds_complete(nat, wilds - w, need - 1);
                    nat[s as usize] += u1;
                    nat[(s + 1) as usize] += u2;
                    nat[(s + 2) as usize] += u3;
                    if ok {
                        return true;
                    }
                }
            }
        }
    }
    false
}

/// 爆头听快判:任意牌摸上即和牌。与 win.is_baotou_wait 的 34 次
/// is_win 定义等价(差分验收 scripts/rust_parity.py),刻画为:
/// 持 ≥1 财神,且
/// (a) 去一张财神后恰组成 4-locked 个面子(财神可入面子)——任意
///     摸牌 u 以 (u,财神) 雀头入局;
/// (b) locked=0 七对路径:自然单张数 s 满足 wilds ≥ s+1 且
///     (wilds-s-1) 为偶——u 配财神成对、余财神两两自配成 7 对。
/// 无财神不可能听任意(异花色/字牌摸牌无法入局)。
fn baotou_wait_fast(counts: &[i32; 34], locked: i32) -> bool {
    let wilds = counts[W];
    if wilds < 1 {
        return false;
    }
    let need = 4 - locked;
    let mut nat = [0i32; 33];
    nat.copy_from_slice(&counts[..33]);
    if melds_complete(&mut nat, wilds - 1, need) {
        return true;
    }
    if locked == 0 {
        let s: i32 = nat.iter().map(|&c| c % 2).sum();
        if wilds >= s + 1 && (wilds - s - 1) % 2 == 0 {
            return true;
        }
    }
    false
}

/// 爆头听判定(带 (手牌, locked) 记忆化的快判封装)。
fn baotou_wait_impl(
    counts: &[i32; 34],
    locked: i32,
    wait_cache: &mut HashMap<ShantenCacheKey, bool>,
) -> Result<bool, String> {
    let key = (*counts, locked);
    if let Some(&value) = wait_cache.get(&key) {
        return Ok(value);
    }
    let ok = baotou_wait_fast(counts, locked);
    wait_cache.insert(key, ok);
    Ok(ok)
}

/// 爆头进张枚举(与 shanten.py::baotou_ukeire_py 逐口径对应):
/// 摸 t 后存在弃 d 使 S+t-d 为爆头听的 t 集合与未见加权和。
/// S 自身为爆头听时全部牌计入。候选剪枝:爆头听的自然牌必须互相
/// 连接,远处孤立牌唯一途径是 (W,W,t) 刻子 + 第 3 个财神配对——
/// counts[W] >= 3 时不剪(全量),否则用 ukeire_candidates(含财神
/// 本身);弃掉刚摸的 t 只回到原手(已知非爆头听),跳过。
fn baotou_ukeire_impl(
    counts: &[i32; 34],
    locked: i32,
    visible: Option<&[i32; 34]>,
) -> Result<(Vec<usize>, i64), String> {
    let v = visible.copied().unwrap_or(*counts);
    let left = |t: usize| (4 - v[t]).max(0) as i64;
    let mut wait_cache: HashMap<ShantenCacheKey, bool> = HashMap::new();
    if baotou_wait_impl(counts, locked, &mut wait_cache)? {
        let acc: Vec<usize> = (0..34).filter(|&t| counts[t] < 4).collect();
        let total: i64 = acc.iter().map(|&t| left(t)).sum();
        return Ok((acc, total));
    }
    let cands: Vec<usize> = if counts[W] >= 3 {
        (0..34).collect()
    } else {
        ukeire_candidates(counts)
    };
    let mut acc: Vec<usize> = Vec::new();
    for &t in &cands {
        if counts[t] >= 4 {
            continue;
        }
        let mut c2 = *counts;
        c2[t] += 1;
        for d in 0..34 {
            if d == t || c2[d] == 0 {
                continue;
            }
            c2[d] -= 1;
            let ok = baotou_wait_impl(&c2, locked, &mut wait_cache);
            c2[d] += 1;
            if ok? {
                acc.push(t);
                break;
            }
        }
    }
    let total: i64 = acc.iter().map(|&t| left(t)).sum();
    Ok((acc, total))
}

fn best_future_discard_impl_with_cache(
    counts: &[i32; 34],
    locked: i32,
    visible: Option<&[i32; 34]>,
    shanten_cache: &mut HashMap<ShantenCacheKey, i32>,
    include_tiles: bool,
) -> Result<Option<FutureDiscard>, String> {
    let mut best_shanten = 99;
    let mut children: Vec<(usize, [i32; 34])> = Vec::new();
    // First find the minimum shanten for every legal discard.  Computing an
    // ukeire total before this minimum is known wastes the expensive inner
    // draw loop whenever a later tile lowers shanten.
    for d in 0..34 {
        if counts[d] <= 0 {
            continue;
        }
        let mut child = *counts;
        child[d] -= 1;
        let child_s = wildcard_shanten(&child, locked, shanten_cache)?;
        if child_s < best_shanten {
            best_shanten = child_s;
            children.clear();
            children.push((d, child));
        } else if child_s == best_shanten {
            children.push((d, child));
        }
    }
    if children.is_empty() {
        return Ok(None);
    }
    let mut best_discard: Option<usize> = None;
    let mut best_total = -1i64;
    let mut best_child = [0i32; 34];
    for (d, child) in children {
        let total = if let Some(view) = visible {
            ukeire_total_impl(&child, locked, view, shanten_cache)?.1
        } else {
            ukeire_impl(&child, locked, None)?.2
        };
        if total > best_total
            || (total == best_total && (best_discard.is_none() || d < best_discard.unwrap()))
        {
            best_discard = Some(d);
            best_total = total;
            best_child = child;
        }
    }
    let discard = match best_discard {
        Some(value) => value,
        None => return Ok(None),
    };
    let tiles = if include_tiles {
        if let Some(view) = visible {
            ukeire_impl(&best_child, locked, Some(view))?.1
        } else {
            ukeire_impl(&best_child, locked, None)?.1
        }
    } else {
        Vec::new()
    };
    Ok(Some(FutureDiscard {
        discard,
        shanten: best_shanten,
        tiles,
        total: best_total.max(0),
    }))
}

fn discard_frontier_impl(
    counts: &[i32; 34],
    locked: i32,
    visible: Option<&[i32; 34]>,
    legal: Option<&[usize]>,
    include_tiles: bool,
) -> Result<Vec<FrontierRow>, String> {
    let mut cache = HashMap::new();
    discard_frontier_impl_with_cache(counts, locked, visible, legal, include_tiles, &mut cache)
}

fn discard_frontier_impl_with_cache(
    counts: &[i32; 34],
    locked: i32,
    visible: Option<&[i32; 34]>,
    legal: Option<&[usize]>,
    include_tiles: bool,
    cache: &mut HashMap<ShantenCacheKey, i32>,
) -> Result<Vec<FrontierRow>, String> {
    let mut rows = Vec::new();
    let tiles: Vec<usize> = match legal {
        Some(values) => values.to_vec(),
        None => (0..34).filter(|&t| counts[t] > 0).collect(),
    };
    for discard in tiles {
        if discard >= 34 || counts[discard] <= 0 {
            return Err("legal discard is absent from hand".to_string());
        }
        let mut child = *counts;
        child[discard] -= 1;
        let child_s = wildcard_shanten(&child, locked, cache)?;
        let (draw_tiles, total) = if let Some(view) = visible {
            let value = ukeire_impl(&child, locked, Some(view))?;
            (value.1, value.2)
        } else {
            let value = ukeire_impl(&child, locked, None)?;
            (value.1, value.2)
        };
        rows.push(FrontierRow {
            discard,
            shanten: child_s,
            tiles: if include_tiles {
                draw_tiles
            } else {
                Vec::new()
            },
            total,
        });
    }
    Ok(rows)
}

/// 在一张摸牌后的手牌中，找出最低向听的立即弃牌及其 p1。
///
/// Python 前瞻原先先在 Python 侧枚举每张可弃牌，再逐个跨 FFI 调用
/// `ukeire`。这个批量入口把同一层的枚举、向听和进张统计放在一次
/// Rust 调用里；调用方仍负责规则门禁（例如有财必拷）和构造解释用
/// 的子手牌，因此不会改变动作授权语义。
#[pyfunction(signature = (counts, locked=0, visible=None, include_tiles=true))]
fn best_future_discard(
    counts: Vec<i32>,
    locked: i32,
    visible: Option<Vec<i32>>,
    include_tiles: bool,
) -> PyResult<(i32, i32, Vec<i32>, i64)> {
    let arr = to_arr(counts)?;
    let vis = match visible {
        Some(v) => Some(to_arr(v)?),
        None => None,
    };
    let mut shanten_cache = HashMap::new();
    let best = best_future_discard_impl_with_cache(
        &arr,
        locked,
        vis.as_ref(),
        &mut shanten_cache,
        include_tiles,
    )
    .map_err(PyValueError::new_err)?;
    match best {
        Some(value) => Ok((
            value.discard as i32,
            value.shanten,
            if include_tiles {
                value.tiles.into_iter().map(|t| t as i32).collect()
            } else {
                Vec::new()
            },
            value.total,
        )),
        None => Ok((-1, 99, Vec::new(), 0)),
    }
}

/// Enumerate every legal distinct discard.  Unlike best_future_discard this
/// intentionally does not apply the old minimum-shanten filter or wildcard
/// protection; the Python layer decides how a complete value model ranks the
/// returned rows.
#[pyfunction(signature = (counts, locked=0, visible=None, legal_discards=None, include_tiles=true))]
fn discard_frontier(
    counts: Vec<i32>,
    locked: i32,
    visible: Option<Vec<i32>>,
    legal_discards: Option<Vec<i32>>,
    include_tiles: bool,
) -> PyResult<Vec<(i32, i32, Vec<i32>, i64)>> {
    let arr = to_arr(counts)?;
    let vis = match visible {
        Some(v) => Some(to_arr(v)?),
        None => None,
    };
    let legal =
        legal_discards.map(|values| values.into_iter().map(|t| t as usize).collect::<Vec<_>>());
    let rows = discard_frontier_impl(&arr, locked, vis.as_ref(), legal.as_deref(), include_tiles)
        .map_err(PyValueError::new_err)?;
    Ok(rows
        .into_iter()
        .map(|row| {
            (
                row.discard as i32,
                row.shanten,
                row.tiles.into_iter().map(|t| t as i32).collect(),
                row.total,
            )
        })
        .collect())
}

/// Enumerate several post-draw discard frontiers in one native call.
///
/// EV2 visits many 14-tile states with the same public visibility snapshot.
/// Keeping the shanten cache alive across those states removes repeated
/// decomposition work while preserving the exact per-state all-legal
/// frontier contract.  ``visibles`` and ``legal_discards`` are optional
/// parallel vectors; when omitted, the normal per-state defaults apply.
#[pyfunction(signature = (states, locked=0, visibles=None, legal_discards=None, include_tiles=true))]
fn discard_frontier_batch(
    states: Vec<Vec<i32>>,
    locked: i32,
    visibles: Option<Vec<Vec<i32>>>,
    legal_discards: Option<Vec<Vec<i32>>>,
    include_tiles: bool,
) -> PyResult<Vec<Vec<(i32, i32, Vec<i32>, i64)>>> {
    if let Some(ref values) = visibles {
        if values.len() != states.len() {
            return Err(PyValueError::new_err(
                "visibles must have one entry per state",
            ));
        }
    }
    if let Some(ref values) = legal_discards {
        if values.len() != states.len() {
            return Err(PyValueError::new_err(
                "legal_discards must have one entry per state",
            ));
        }
    }
    let mut cache = HashMap::new();
    let mut result = Vec::with_capacity(states.len());
    for (index, values) in states.into_iter().enumerate() {
        let counts = to_arr(values)?;
        let visible = match visibles.as_ref() {
            Some(all) => Some(to_arr(all[index].clone())?),
            None => None,
        };
        let legal = legal_discards.as_ref().map(|all| {
            all[index]
                .iter()
                .map(|&tile| tile as usize)
                .collect::<Vec<_>>()
        });
        let rows = discard_frontier_impl_with_cache(
            &counts,
            locked,
            visible.as_ref(),
            legal.as_deref(),
            include_tiles,
            &mut cache,
        )
        .map_err(PyValueError::new_err)?;
        result.push(
            rows.into_iter()
                .map(|row| {
                    (
                        row.discard as i32,
                        row.shanten,
                        row.tiles.into_iter().map(|tile| tile as i32).collect(),
                        row.total,
                    )
                })
                .collect(),
        );
    }
    Ok(result)
}

/// Return the public-information two-ply frontier used by legacy V1.
///
/// The Python evaluator owns root legality, policy callbacks, explanations and
/// transactional fallback.  This function only performs the hot nested loop:
/// one weighted public draw followed by one legal child discard.  Every draw
/// row includes all children tied on (shanten, ukeire), so Python can apply its
/// existing shape/feed tie-break without asking Rust to call back into Python.
/// Rows are returned as tuples to keep the PyO3 boundary small and compatible
/// with already-installed wheels:
///
/// ``(root_index, complete, improve, future_ukeire,
///     [(draw, weight, child_shanten, child_ukeire, tied_discards)],
///     nodes, cache_hits, reason)``.
#[pyfunction(signature = (roots, root_shantens, visible, legal_masks, locked=0, frozen=false, node_budget=4096, time_budget_ms=8.0, include_best_discards=true))]
fn legacy_two_ply_frontier(
    roots: Vec<Vec<i32>>,
    root_shantens: Vec<i32>,
    visible: Vec<i32>,
    legal_masks: Vec<Vec<i64>>,
    locked: i32,
    frozen: bool,
    node_budget: i64,
    time_budget_ms: f64,
    include_best_discards: bool,
) -> PyResult<
    Vec<(
        i32,
        bool,
        i64,
        i64,
        Vec<(i32, i64, i32, i64, Vec<i32>)>,
        i64,
        i64,
        String,
    )>,
> {
    if roots.is_empty() {
        return Err(PyValueError::new_err("roots must not be empty"));
    }
    if roots.len() != root_shantens.len() || roots.len() != legal_masks.len() {
        return Err(PyValueError::new_err(
            "roots, root_shantens and legal_masks must have equal length",
        ));
    }
    if locked < 0 || locked > 4 {
        return Err(PyValueError::new_err("locked must be between 0 and 4"));
    }
    if node_budget < 0 {
        return Err(PyValueError::new_err("node_budget must be non-negative"));
    }
    if !time_budget_ms.is_finite() || time_budget_ms < 0.0 {
        return Err(PyValueError::new_err(
            "time_budget_ms must be finite and non-negative",
        ));
    }
    let started = Instant::now();
    let time_limit = Duration::from_secs_f64(time_budget_ms / 1000.0);
    let visible = validate_count_vector(&visible, "visible")?;
    let expected = 13 - 3 * locked;
    let mut root_arrays = Vec::with_capacity(roots.len());
    for (index, values) in roots.iter().enumerate() {
        let root = validate_count_vector(values, "root")?;
        if root.iter().sum::<i32>() != expected {
            return Err(PyValueError::new_err(format!(
                "root {index} has an invalid concealed hand size"
            )));
        }
        if root
            .iter()
            .enumerate()
            .any(|(tile, &count)| visible[tile] < count)
        {
            return Err(PyValueError::new_err(format!(
                "visible counts do not contain root {index}"
            )));
        }
        if legal_masks[index].len() != 34 {
            return Err(PyValueError::new_err(format!(
                "legal_masks[{index}] must have 34 draw entries"
            )));
        }
        root_arrays.push(root);
    }

    let mut shanten_cache: HashMap<ShantenCacheKey, i32> = HashMap::new();
    let mut future_cache: HashMap<FutureCacheKey, FutureMetrics> = HashMap::new();
    let mut nodes = 0i64;
    let mut cache_hits = 0i64;
    let mut output = Vec::with_capacity(roots.len());
    let mut exhausted = false;

    for (root_index, root) in root_arrays.iter().enumerate() {
        if exhausted {
            output.push((
                root_index as i32,
                false,
                0,
                0,
                Vec::new(),
                nodes,
                cache_hits,
                "node_budget_exceeded".to_string(),
            ));
            continue;
        }
        let root_s =
            wildcard_shanten(root, locked, &mut shanten_cache).map_err(PyValueError::new_err)?;
        if root_s != root_shantens[root_index] {
            return Err(PyValueError::new_err(format!(
                "root_shantens[{root_index}] does not match root hand"
            )));
        }
        let mut improve = 0i64;
        let mut future_ukeire = 0i64;
        let mut draw_rows: Vec<(i32, i64, i32, i64, Vec<i32>)> = Vec::new();
        let mut complete = true;
        let mut reason = String::new();

        for draw in 0..34 {
            let weight = (4 - visible[draw]).max(0) as i64;
            if weight == 0 {
                continue;
            }
            if nodes >= node_budget || started.elapsed() >= time_limit {
                complete = false;
                reason = if nodes >= node_budget {
                    "node_budget_exceeded"
                } else {
                    "time_budget_exceeded"
                }
                .to_string();
                exhausted = true;
                break;
            }

            let mut next_hand = *root;
            next_hand[draw] += 1;
            let mut visible_after = visible;
            visible_after[draw] += 1;
            if visible_after[draw] > 4 {
                return Err(PyValueError::new_err("visible_after_draw_invalid"));
            }

            let mut mask = legal_masks[root_index][draw];
            if mask < 0 {
                return Err(PyValueError::new_err("legal mask must be non-negative"));
            }
            if frozen {
                mask &= 1i64 << draw;
            }
            let valid_mask = next_hand
                .iter()
                .enumerate()
                .fold(
                    0i64,
                    |acc, (tile, &count)| {
                        if count > 0 {
                            acc | (1i64 << tile)
                        } else {
                            acc
                        }
                    },
                );
            if mask & !valid_mask != 0 || mask == 0 {
                return Err(PyValueError::new_err("invalid future legal discard mask"));
            }

            let mut best_s = i32::MAX;
            let mut children: Vec<(usize, [i32; 34], i32, Option<i64>)> = Vec::new();
            for discard in 0..34 {
                if mask & (1i64 << discard) == 0 {
                    continue;
                }
                if nodes >= node_budget || started.elapsed() >= time_limit {
                    complete = false;
                    reason = if nodes >= node_budget {
                        "node_budget_exceeded"
                    } else {
                        "time_budget_exceeded"
                    }
                    .to_string();
                    exhausted = true;
                    break;
                }
                nodes += 1;
                let mut after = next_hand;
                after[discard] -= 1;
                let key = (after, visible_after, locked);
                if let Some(value) = future_cache.get(&key).copied() {
                    cache_hits += 1;
                    if value.shanten < best_s {
                        best_s = value.shanten;
                    }
                    children.push((discard, after, value.shanten, Some(value.ukeire)));
                } else {
                    let child_s = wildcard_shanten(&after, locked, &mut shanten_cache)
                        .map_err(PyValueError::new_err)?;
                    children.push((discard, after, child_s, None));
                    if child_s < best_s {
                        best_s = child_s;
                    }
                }
            }
            if !complete {
                break;
            }
            if children.is_empty() {
                return Err(PyValueError::new_err("future legal discard set is empty"));
            }
            let mut best_u = -1i64;
            let mut tied = Vec::new();
            for (discard, after, child_s, cached_u) in children {
                if child_s != best_s {
                    continue;
                }
                let child_u = if let Some(value) = cached_u {
                    value
                } else {
                    let value =
                        ukeire_total_impl(&after, locked, &visible_after, &mut shanten_cache)
                            .map_err(PyValueError::new_err)?
                            .1;
                    future_cache.insert(
                        (after, visible_after, locked),
                        FutureMetrics {
                            shanten: child_s,
                            ukeire: value,
                        },
                    );
                    value
                };
                if child_u > best_u {
                    best_u = child_u;
                    tied.clear();
                    tied.push(discard as i32);
                } else if child_u == best_u {
                    tied.push(discard as i32);
                }
            }
            if tied.is_empty() {
                return Err(PyValueError::new_err("future legal discard set is empty"));
            }
            if best_s < root_s {
                improve += weight;
            }
            future_ukeire += weight * best_u.max(0);
            if include_best_discards {
                draw_rows.push((draw as i32, weight, best_s, best_u.max(0), tied));
            }
        }

        output.push((
            root_index as i32,
            complete,
            if complete { improve } else { 0 },
            if complete { future_ukeire } else { 0 },
            if complete { draw_rows } else { Vec::new() },
            nodes,
            cache_hits,
            reason,
        ));
    }
    Ok(output)
}

type WeightedDrawRow = (i32, i64, i32, i64, i32, Vec<i32>);
type StageADrawRow = (i32, i64, i32, Vec<i32>);
type WeightedRootRow = (
    i32,
    bool,
    bool,
    (i64, i64, i64, i64, i64, i64),
    Vec<WeightedDrawRow>,
    (i64, i64, i64, i64, i64, i64, i64, i64, i64, i64, i64),
    i64,
    String,
);

/// One Stage B work unit: child ukeire for a single ``(root, draw)`` pair,
/// using the best-shanten child discards already found by Stage A.
struct StageBUnit {
    root_index: usize,
    order: usize,
    draw: usize,
    weight: i64,
    child_s: i32,
    best_discards: Vec<i32>,
}

/// Result of one Stage B work unit.  ``order`` restores the Stage A row order
/// so parallel aggregation reproduces the sequential row sequence exactly.
struct StageBOutcome {
    root_index: usize,
    order: usize,
    draw: usize,
    weight: i64,
    child_s: i32,
    best_u: i64,
    best_types: i32,
    tied: Vec<i32>,
}

/// Resolve the Stage B worker count: explicit request, then
/// ``MJ_KERNELS_THREADS``, then available parallelism capped at 8.  The result
/// is clamped to the number of work units, so a single unit never spawns
/// threads.
fn resolve_worker_count(requested: i64, units: usize) -> usize {
    if units <= 1 {
        return 1;
    }
    let value = if requested > 0 {
        requested as usize
    } else {
        std::env::var("MJ_KERNELS_THREADS")
            .ok()
            .and_then(|raw| raw.trim().parse::<usize>().ok())
            .filter(|count| *count > 0)
            .unwrap_or_else(|| {
                std::thread::available_parallelism()
                    .map(|count| count.get())
                    .unwrap_or(1)
                    .min(8)
            })
    };
    value.max(1).min(units)
}

/// Deterministic abort precedence: deadline, then work budget, then whatever
/// the first worker reported.
fn pick_stage_b_reason(reasons: &[String]) -> Option<String> {
    if reasons.iter().any(|reason| reason == HARD_DEADLINE_EXCEEDED) {
        return Some(HARD_DEADLINE_EXCEEDED.to_string());
    }
    if reasons.iter().any(|reason| reason == WORK_BUDGET_EXCEEDED) {
        return Some(WORK_BUDGET_EXCEEDED.to_string());
    }
    reasons.first().cloned()
}

/// Run Stage B units against caller-owned caches.  The sequential path passes
/// the Stage-A-warmed caches, every worker passes its own; sharing the unit
/// semantics keeps both paths from drifting apart.  With an atomic cursor the
/// units are pulled dynamically, so a slow draw cannot leave a shard idle.
#[allow(clippy::too_many_arguments)]
fn run_stage_b_units(
    units: &[StageBUnit],
    cursor: Option<&std::sync::atomic::AtomicUsize>,
    root_arrays: &[[i32; 34]],
    visible: &[i32; 34],
    locked: i32,
    cache_capacity: usize,
    work_budget: i64,
    hard_deadline: Instant,
    shanten_cache: &mut HashMap<ShantenCacheKey, i32>,
    ukeire_cache: &mut HashMap<UkeireCacheKey, UkeireMetrics>,
    counters: &mut SearchCounters,
    outcomes: &mut Vec<StageBOutcome>,
) -> Option<String> {
    let mut position = 0usize;
    loop {
        let index = match cursor {
            Some(cursor) => cursor.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
            None => position,
        };
        if index >= units.len() {
            return None;
        }
        position = index + 1;
        let unit = &units[index];
        if Instant::now() >= hard_deadline {
            return Some(HARD_DEADLINE_EXCEEDED.to_string());
        }
        let mut visible_after = *visible;
        visible_after[unit.draw] += 1;
        let mut next_hand = root_arrays[unit.root_index];
        next_hand[unit.draw] += 1;
        let mut best_u = -1i64;
        let mut best_types = 0i32;
        let mut tied = Vec::new();
        for &discard in &unit.best_discards {
            if Instant::now() >= hard_deadline {
                return Some(HARD_DEADLINE_EXCEEDED.to_string());
            }
            let discard = discard as usize;
            let mut after = next_hand;
            after[discard] -= 1;
            let metrics = match counted_ukeire_metrics(
                &after,
                locked,
                &visible_after,
                shanten_cache,
                ukeire_cache,
                cache_capacity,
                work_budget,
                hard_deadline,
                counters,
            ) {
                Ok(value) => value,
                Err(reason) => return Some(reason),
            };
            if metrics.ukeire > best_u
                || (metrics.ukeire == best_u && metrics.tile_types > best_types)
            {
                best_u = metrics.ukeire;
                best_types = metrics.tile_types;
                tied.clear();
                tied.push(discard as i32);
            } else if metrics.ukeire == best_u && metrics.tile_types == best_types {
                tied.push(discard as i32);
            }
        }
        if tied.is_empty() {
            return Some(STAGE_B_INTERNAL_ERROR.to_string());
        }
        outcomes.push(StageBOutcome {
            root_index: unit.root_index,
            order: unit.order,
            draw: unit.draw,
            weight: unit.weight,
            child_s: unit.child_s,
            best_u,
            best_types,
            tied,
        });
    }
}

/// Return the staged, probability-weighted online frontier.
///
/// Stage A evaluates only child shanten.  Stage B is entered only when the
/// improvement bounds cannot decide the root, avoiding the expensive child
/// ukeire DFS for the common easy-to-rank states.  Stage B may run its
/// ``(root, draw)`` units in parallel workers; the aggregation order is fixed,
/// so the reported metrics never depend on the worker count.
#[pyfunction(signature = (roots, root_shantens, visible, legal_masks, locked=0, frozen=false, node_budget=100000, soft_budget_ms=40.0, hard_budget_ms=50.0, cache_capacity=8192, min_partial_coverage=0.90, include_best_discards=true, workers=0))]
fn weighted_two_ply_frontier(
    roots: Vec<Vec<i32>>,
    root_shantens: Vec<i32>,
    visible: Vec<i32>,
    legal_masks: Vec<Vec<i64>>,
    locked: i32,
    frozen: bool,
    node_budget: i64,
    soft_budget_ms: f64,
    hard_budget_ms: f64,
    cache_capacity: i64,
    min_partial_coverage: f64,
    include_best_discards: bool,
    workers: i64,
) -> PyResult<Vec<WeightedRootRow>> {
    if roots.is_empty() {
        return Err(PyValueError::new_err("roots must not be empty"));
    }
    if roots.len() != root_shantens.len() || roots.len() != legal_masks.len() {
        return Err(PyValueError::new_err(
            "roots, root_shantens and legal_masks must have equal length",
        ));
    }
    if locked < 0 || locked > 4 {
        return Err(PyValueError::new_err("locked must be between 0 and 4"));
    }
    if node_budget < 0 {
        return Err(PyValueError::new_err("node_budget must be non-negative"));
    }
    if !soft_budget_ms.is_finite()
        || !hard_budget_ms.is_finite()
        || soft_budget_ms < 0.0
        || hard_budget_ms < 0.0
        || soft_budget_ms > hard_budget_ms
    {
        return Err(PyValueError::new_err(
            "soft/hard budgets must be finite and ordered",
        ));
    }
    if !min_partial_coverage.is_finite() || !(0.0..=1.0).contains(&min_partial_coverage) {
        return Err(PyValueError::new_err(
            "min_partial_coverage must be between 0 and 1",
        ));
    }
    let cache_capacity = usize::try_from(cache_capacity)
        .map_err(|_| PyValueError::new_err("cache_capacity must be non-negative"))?;
    let started = Instant::now();
    let soft_limit = Duration::from_secs_f64(soft_budget_ms / 1000.0);
    let internal_hard_ms = (hard_budget_ms - DEADLINE_RESERVE_MS).max(0.0);
    let hard_limit = Duration::from_secs_f64(internal_hard_ms / 1000.0);
    let hard_deadline = started + hard_limit;
    let visible = validate_count_vector(&visible, "visible")?;
    let expected = 13 - 3 * locked;
    let mut root_arrays = Vec::with_capacity(roots.len());
    for (index, values) in roots.iter().enumerate() {
        let root = validate_count_vector(values, "root")?;
        if root.iter().sum::<i32>() != expected {
            return Err(PyValueError::new_err(format!(
                "root {index} has an invalid concealed hand size"
            )));
        }
        if root
            .iter()
            .enumerate()
            .any(|(tile, &count)| visible[tile] < count)
        {
            return Err(PyValueError::new_err(format!(
                "visible counts do not contain root {index}"
            )));
        }
        if legal_masks[index].len() != 34 {
            return Err(PyValueError::new_err(format!(
                "legal_masks[{index}] must have 34 draw entries"
            )));
        }
        root_arrays.push(root);
    }

    let remaining: Vec<i64> = (0..34)
        .map(|tile| (4 - visible[tile]).max(0) as i64)
        .collect();
    let total_weight: i64 = remaining.iter().sum();
    let mut draw_order: Vec<usize> = (0..34).filter(|&tile| remaining[tile] > 0).collect();
    draw_order.sort_by(|&a, &b| remaining[b].cmp(&remaining[a]).then_with(|| a.cmp(&b)));

    let mut shanten_cache: HashMap<ShantenCacheKey, i32> = HashMap::new();
    let mut ukeire_cache: HashMap<UkeireCacheKey, UkeireMetrics> = HashMap::new();
    let mut counters = SearchCounters {
        root_candidates: root_arrays.len() as i64,
        ..SearchCounters::default()
    };
    let work_budget = node_budget.max(root_arrays.len() as i64);
    // Root validation is a legality check, not speculative search.  Keep it
    // outside the online hard deadline so a zero/very small budget still
    // returns a transactional row instead of a PyO3 exception.
    let root_validation_deadline = Instant::now() + Duration::from_secs(24 * 60 * 60);
    let mut root_shanten_values = Vec::with_capacity(root_arrays.len());
    for (index, root) in root_arrays.iter().enumerate() {
        let value = counted_shanten(
            root,
            locked,
            &mut shanten_cache,
            cache_capacity,
            work_budget,
            root_validation_deadline,
            &mut counters,
        )
        .map_err(PyValueError::new_err)?;
        if value != root_shantens[index] {
            return Err(PyValueError::new_err(format!(
                "root_shantens[{index}] does not match root hand"
            )));
        }
        root_shanten_values.push(value);
    }

    let mut accumulators: Vec<WeightedRootAccumulator> = root_shanten_values
        .iter()
        .map(|_| WeightedRootAccumulator::new(total_weight))
        .collect();
    let mut hard_exhausted = false;
    let soft_stopped = false;
    let mut stage_a_winner = None;

    'stage_a: for &draw in &draw_order {
        if counters.shanten_cache_misses >= work_budget {
            break;
        }
        if Instant::now() >= hard_deadline {
            hard_exhausted = true;
            break;
        }
        for root_index in 0..root_arrays.len() {
            if Instant::now() >= hard_deadline {
                hard_exhausted = true;
                break 'stage_a;
            }
            if counters.shanten_cache_misses >= work_budget {
                break 'stage_a;
            }
            let all_covered = accumulators.iter().all(|acc| {
                acc.coverage() >= min_partial_coverage
                    || (acc.total_weight == 0 && acc.covered_weight == 0)
            });
            if Instant::now() >= started + soft_limit && all_covered {
                if let Some(winner) = strict_improvement_winner(&accumulators) {
                    stage_a_winner = Some(winner);
                    break 'stage_a;
                }
                // Coverage alone is not a decision certificate.  Keep racing
                // until the internal hard deadline so the remaining draw mass
                // can either separate the bounds or finish Stage A.
            }

            let weight = remaining[draw];
            let mut next_hand = root_arrays[root_index];
            next_hand[draw] += 1;
            let mut visible_after = visible;
            visible_after[draw] += 1;
            if visible_after[draw] > 4 {
                return Err(PyValueError::new_err("visible_after_draw_invalid"));
            }
            let mut mask = legal_masks[root_index][draw];
            if mask < 0 {
                return Err(PyValueError::new_err("legal mask must be non-negative"));
            }
            if frozen {
                mask &= 1i64 << draw;
            }
            let valid_mask = next_hand
                .iter()
                .enumerate()
                .fold(
                    0i64,
                    |acc, (tile, &count)| {
                        if count > 0 {
                            acc | (1i64 << tile)
                        } else {
                            acc
                        }
                    },
                );
            if mask & !valid_mask != 0 || mask == 0 {
                return Err(PyValueError::new_err("invalid future legal discard mask"));
            }

            counters.draw_nodes += 1;
            let mut best_s = i32::MAX;
            let mut best_discards = Vec::new();
            let mut branch_reason = None;
            for discard in 0..34 {
                if mask & (1i64 << discard) == 0 {
                    continue;
                }
                if Instant::now() >= hard_deadline {
                    branch_reason = Some(HARD_DEADLINE_EXCEEDED.to_string());
                    break;
                }
                counters.child_nodes += 1;
                let mut after = next_hand;
                after[discard] -= 1;
                let child_s = match counted_shanten(
                    &after,
                    locked,
                    &mut shanten_cache,
                    cache_capacity,
                    work_budget,
                    hard_deadline,
                    &mut counters,
                ) {
                    Ok(value) => value,
                    Err(reason) => {
                        branch_reason = Some(reason);
                        break;
                    }
                };
                if child_s < best_s {
                    best_s = child_s;
                    best_discards.clear();
                    best_discards.push(discard as i32);
                } else if child_s == best_s {
                    best_discards.push(discard as i32);
                }
            }
            if let Some(reason) = branch_reason {
                accumulators[root_index].reason = reason.clone();
                if reason == WORK_BUDGET_EXCEEDED || reason == HARD_DEADLINE_EXCEEDED {
                    if reason == HARD_DEADLINE_EXCEEDED {
                        hard_exhausted = true;
                    }
                    break 'stage_a;
                }
                continue;
            }
            if best_discards.is_empty() {
                return Err(PyValueError::new_err("future legal discard set is empty"));
            }
            let accumulator = &mut accumulators[root_index];
            accumulator.covered_weight += weight;
            if best_s < root_shanten_values[root_index] {
                accumulator.improve_weight += weight;
            } else {
                accumulator.maintain_weight += weight;
            }
            accumulator
                .stage_a_rows
                .push((draw as i32, weight, best_s, best_discards));

            if let Some(winner) = strict_improvement_winner(&accumulators) {
                stage_a_winner = Some(winner);
                break 'stage_a;
            }
        }
    }

    let stage_a_complete = !hard_exhausted
        && !soft_stopped
        && counters.shanten_cache_misses < work_budget
        && accumulators
            .iter()
            .all(|acc| acc.covered_weight >= acc.total_weight);
    if stage_a_winner.is_none() && stage_a_complete && total_weight > 0 {
        let max_improve = accumulators
            .iter()
            .map(|acc| acc.improve_weight)
            .max()
            .unwrap_or(0);
        let winners: Vec<usize> = accumulators
            .iter()
            .enumerate()
            .filter_map(|(index, acc)| (acc.improve_weight == max_improve).then_some(index))
            .collect();
        if winners.len() == 1 {
            stage_a_winner = winners.first().copied();
        }
    }

    let mut stage_b_complete = total_weight == 0;
    let mut resolved_workers: i64 = 1;
    let mut stage_b_entered = false;
    if stage_a_winner.is_none() && stage_a_complete && total_weight > 0 {
        stage_b_entered = true;
        let mut units: Vec<StageBUnit> = Vec::new();
        for (root_index, accumulator) in accumulators.iter().enumerate() {
            for (order, (draw, weight, child_s, best_discards)) in
                accumulator.stage_a_rows.iter().enumerate()
            {
                units.push(StageBUnit {
                    root_index,
                    order,
                    draw: *draw as usize,
                    weight: *weight,
                    child_s: *child_s,
                    best_discards: best_discards.clone(),
                });
            }
        }
        // Keep the units of one draw adjacent: the swapped root/discard pair
        // shares a child hand, so neighbours usually reuse the same worker
        // cache even when units are handed out dynamically.
        units.sort_by_key(|unit| (unit.draw, unit.root_index));
        let worker_count = resolve_worker_count(workers, units.len());
        resolved_workers = worker_count as i64;
        let mut outcomes: Vec<StageBOutcome> = Vec::with_capacity(units.len());
        let stage_b_aborted = if worker_count <= 1 {
            run_stage_b_units(
                &units,
                None,
                &root_arrays,
                &visible,
                locked,
                cache_capacity,
                work_budget,
                hard_deadline,
                &mut shanten_cache,
                &mut ukeire_cache,
                &mut counters,
                &mut outcomes,
            )
        } else {
            let remaining_budget = (work_budget - counters.shanten_cache_misses).max(0);
            let root_arrays_ref: &[[i32; 34]] = &root_arrays;
            let visible_ref: &[i32; 34] = &visible;
            let units_ref: &[StageBUnit] = &units;
            let cursor = std::sync::atomic::AtomicUsize::new(0);
            let cursor_ref = &cursor;
            let results: Vec<(Vec<StageBOutcome>, SearchCounters, Option<String>)> =
                std::thread::scope(|scope| {
                    let handles: Vec<_> = (0..worker_count)
                        .map(|_| {
                            scope.spawn(move || {
                                let mut worker_shanten: HashMap<ShantenCacheKey, i32> =
                                    HashMap::new();
                                let mut worker_ukeire: HashMap<UkeireCacheKey, UkeireMetrics> =
                                    HashMap::new();
                                let mut worker_counters = SearchCounters::default();
                                let mut worker_outcomes = Vec::new();
                                let aborted = run_stage_b_units(
                                    units_ref,
                                    Some(cursor_ref),
                                    root_arrays_ref,
                                    visible_ref,
                                    locked,
                                    cache_capacity,
                                    remaining_budget,
                                    hard_deadline,
                                    &mut worker_shanten,
                                    &mut worker_ukeire,
                                    &mut worker_counters,
                                    &mut worker_outcomes,
                                );
                                (worker_outcomes, worker_counters, aborted)
                            })
                        })
                        .collect();
                    handles
                        .into_iter()
                        .map(|handle| match handle.join() {
                            Ok(value) => value,
                            Err(_) => (
                                Vec::new(),
                                SearchCounters::default(),
                                Some(STAGE_B_WORKER_FAILED.to_string()),
                            ),
                        })
                        .collect()
                });
            let mut reasons: Vec<String> = Vec::new();
            for (worker_outcomes, worker_counters, aborted) in results {
                outcomes.extend(worker_outcomes);
                counters.merge(&worker_counters);
                if let Some(reason) = aborted {
                    reasons.push(reason);
                }
            }
            // Workers only see their own misses, so the shared work budget is
            // enforced once more on the aggregated total.
            if counters.shanten_cache_misses >= work_budget {
                reasons.push(WORK_BUDGET_EXCEEDED.to_string());
            }
            pick_stage_b_reason(&reasons)
        };
        match stage_b_aborted.as_deref() {
            Some(STAGE_B_INTERNAL_ERROR) => {
                return Err(PyValueError::new_err("future legal discard set is empty"))
            }
            Some(STAGE_B_WORKER_FAILED) => {
                return Err(PyValueError::new_err("weighted stage B worker failed"))
            }
            _ => {}
        }
        if let Some(reason) = stage_b_aborted {
            for accumulator in &mut accumulators {
                accumulator.reason = reason.clone();
                accumulator.future_ukeire = 0;
                accumulator.future_ukeire_types = 0;
                accumulator.draw_rows.clear();
            }
            if reason == HARD_DEADLINE_EXCEEDED {
                hard_exhausted = true;
            }
        } else {
            outcomes.sort_by_key(|outcome| (outcome.root_index, outcome.order));
            for outcome in outcomes {
                let accumulator = &mut accumulators[outcome.root_index];
                accumulator.future_ukeire += outcome.weight * outcome.best_u.max(0);
                accumulator.future_ukeire_types +=
                    outcome.weight * i64::from(outcome.best_types);
                accumulator.draw_rows.push((
                    outcome.draw as i32,
                    outcome.weight,
                    outcome.child_s,
                    outcome.best_u.max(0),
                    outcome.best_types,
                    outcome.tied,
                ));
            }
            stage_b_complete = true;
        }
    }

    if let Some(_winner) = stage_a_winner {
        for accumulator in &mut accumulators {
            accumulator.reason = "future_ukeire_skipped".to_string();
            accumulator.future_ukeire = 0;
            accumulator.future_ukeire_types = 0;
            accumulator.draw_rows.clear();
        }
    } else if hard_exhausted || soft_stopped || counters.shanten_cache_misses >= work_budget {
        for accumulator in &mut accumulators {
            if accumulator.reason.is_empty() {
                accumulator.reason = if counters.shanten_cache_misses >= work_budget {
                    WORK_BUDGET_EXCEEDED.to_string()
                } else if soft_stopped {
                    "soft_deadline".to_string()
                } else {
                    HARD_DEADLINE_EXCEEDED.to_string()
                };
            }
        }
    }

    let elapsed_us = started.elapsed().as_micros() as i64;
    let mut output = Vec::with_capacity(accumulators.len());
    for (index, accumulator) in accumulators.into_iter().enumerate() {
        let complete = stage_b_complete
            && accumulator.covered_weight >= accumulator.total_weight
            && accumulator.reason.is_empty();
        let committed = accumulator.covered_weight > 0 || accumulator.total_weight == 0;
        let draw_rows = if stage_b_complete && accumulator.reason.is_empty() {
            if include_best_discards {
                accumulator.draw_rows
            } else {
                Vec::new()
            }
        } else if include_best_discards {
            accumulator
                .stage_a_rows
                .into_iter()
                .map(|(draw, weight, child_s, tied)| (draw, weight, child_s, -1, -1, tied))
                .collect()
        } else {
            Vec::new()
        };
        let improve = accumulator.improve_weight;
        let maintain = accumulator.maintain_weight;
        let future_ukeire = if complete {
            accumulator.future_ukeire
        } else {
            0
        };
        let future_types = if complete {
            accumulator.future_ukeire_types
        } else {
            0
        };
        output.push((
            index as i32,
            committed,
            complete,
            (
                accumulator.covered_weight,
                accumulator.total_weight,
                improve,
                maintain,
                future_ukeire,
                future_types,
            ),
            draw_rows,
            (
                counters.root_candidates,
                counters.draw_nodes,
                counters.child_nodes,
                counters.shanten_calls,
                counters.ukeire_calls,
                counters.shanten_cache_hits,
                counters.shanten_cache_misses,
                counters.ukeire_cache_hits,
                counters.ukeire_cache_misses,
                resolved_workers,
                if stage_b_entered { 1 } else { 0 },
            ),
            elapsed_us,
            accumulator.reason,
        ));
    }
    Ok(output)
}

struct WeightedRootAccumulator {
    covered_weight: i64,
    total_weight: i64,
    improve_weight: i64,
    maintain_weight: i64,
    future_ukeire: i64,
    future_ukeire_types: i64,
    stage_a_rows: Vec<StageADrawRow>,
    draw_rows: Vec<WeightedDrawRow>,
    reason: String,
}

impl WeightedRootAccumulator {
    fn new(total_weight: i64) -> Self {
        Self {
            covered_weight: 0,
            total_weight,
            improve_weight: 0,
            maintain_weight: 0,
            future_ukeire: 0,
            future_ukeire_types: 0,
            stage_a_rows: Vec::new(),
            draw_rows: Vec::new(),
            reason: String::new(),
        }
    }

    fn coverage(&self) -> f64 {
        if self.total_weight <= 0 {
            1.0
        } else {
            self.covered_weight as f64 / self.total_weight as f64
        }
    }
}

fn strict_improvement_winner(accumulators: &[WeightedRootAccumulator]) -> Option<usize> {
    for (index, candidate) in accumulators.iter().enumerate() {
        let lower = candidate.improve_weight;
        let winner = accumulators.iter().enumerate().all(|(other, value)| {
            other == index
                || lower > value.improve_weight + (value.total_weight - value.covered_weight).max(0)
        });
        if winner {
            return Some(index);
        }
    }
    None
}

fn validate_count_vector(values: &[i32], name: &str) -> PyResult<[i32; 34]> {
    let array: [i32; 34] = values
        .to_vec()
        .try_into()
        .map_err(|_| PyValueError::new_err(format!("{name} must have 34 entries")))?;
    if array.iter().any(|&value| !(0..=4).contains(&value)) {
        return Err(PyValueError::new_err(format!(
            "{name} contains a count outside 0..4"
        )));
    }
    Ok(array)
}

fn to_arr(counts: Vec<i32>) -> PyResult<[i32; 34]> {
    counts
        .try_into()
        .map_err(|_| PyValueError::new_err("counts 必须是 34 维"))
}

/// 向听数;张数不符抛 ValueError(与 mj.shanten.shanten 同口径)。
#[pyfunction(signature = (counts, locked=0))]
fn shanten(counts: Vec<i32>, locked: i32) -> PyResult<i32> {
    let arr = to_arr(counts)?;
    shanten_impl(&arr, locked).map_err(PyValueError::new_err)
}

/// 进张枚举:返回 (向听数, 进张种类列表, 进张总张数)。
/// visible=None 时按手牌自身折算(与 mj.shanten.ukeire 同口径)。
#[pyfunction(signature = (counts, locked, visible=None))]
fn ukeire(
    counts: Vec<i32>,
    locked: i32,
    visible: Option<Vec<i32>>,
) -> PyResult<(i32, Vec<i32>, i64)> {
    let arr = to_arr(counts)?;
    let vis = match visible {
        Some(v) => Some(to_arr(v)?),
        None => None,
    };
    let (s, acc, total) = ukeire_impl(&arr, locked, vis.as_ref()).map_err(PyValueError::new_err)?;
    Ok((s, acc.iter().map(|&t| t as i32).collect(), total))
}

/// 爆头进张枚举:返回 (进张种类升序列表, 未见加权总数)。
/// 语义与 mj.shanten.baotou_ukeire 对应(随机差分见 scripts/rust_parity.py)。
#[pyfunction(signature = (counts, locked=0, visible=None))]
fn baotou_ukeire(
    counts: Vec<i32>,
    locked: i32,
    visible: Option<Vec<i32>>,
) -> PyResult<(Vec<i32>, i64)> {
    let arr = to_arr(counts)?;
    let vis = match visible {
        Some(v) => Some(to_arr(v)?),
        None => None,
    };
    let (acc, total) =
        baotou_ukeire_impl(&arr, locked, vis.as_ref()).map_err(PyValueError::new_err)?;
    Ok((acc.into_iter().map(|t| t as i32).collect(), total))
}

#[pymodule]
fn mj_kernels(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(shanten, m)?)?;
    m.add_function(wrap_pyfunction!(ukeire, m)?)?;
    m.add_function(wrap_pyfunction!(baotou_ukeire, m)?)?;
    m.add_function(wrap_pyfunction!(best_future_discard, m)?)?;
    m.add_function(wrap_pyfunction!(discard_frontier, m)?)?;
    m.add_function(wrap_pyfunction!(discard_frontier_batch, m)?)?;
    m.add_function(wrap_pyfunction!(legacy_two_ply_frontier, m)?)?;
    m.add_function(wrap_pyfunction!(weighted_two_ply_frontier, m)?)?;
    m.add_function(wrap_pyfunction!(legacy_two_ply_kernel_version, m)?)?;
    m.add_function(wrap_pyfunction!(weighted_two_ply_kernel_version, m)?)?;
    Ok(())
}

#[pyfunction]
fn legacy_two_ply_kernel_version() -> &'static str {
    LEGACY_TWO_PLY_KERNEL_VERSION
}

#[pyfunction]
fn weighted_two_ply_kernel_version() -> &'static str {
    WEIGHTED_TWO_PLY_KERNEL_VERSION
}
