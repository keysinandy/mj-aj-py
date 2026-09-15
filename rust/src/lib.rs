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

const W: usize = 33;
type ShantenCacheKey = ([i32; 34], i32);

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
        std_dfs(nat, i, m + 1, t, p, w, rem - 3, base, floor, need_melds, best);
        nat[i] += 3;
    }
    // 刻子(两枚 + 1 财神):不可省——score 无"对子+财神成刻"记账,
    // locked 多/gaps 少时严格优于对子+财神作雀头
    if c >= 2 && w >= 1 {
        nat[i] -= 2;
        std_dfs(nat, i, m + 1, t, p, w - 1, rem - 2, base, floor, need_melds, best);
        nat[i] += 2;
    }
    // 对子(雀头候选)
    if c >= 2 {
        nat[i] -= 2;
        std_dfs(nat, i, m, t, p + 1, w, rem - 2, base, floor, need_melds, best);
        nat[i] += 2;
    }
    // 顺子(三张连续自然牌)
    if i < 27 && i % 9 <= 6 && nat[i + 1] > 0 && nat[i + 2] > 0 {
        nat[i] -= 1;
        nat[i + 1] -= 1;
        nat[i + 2] -= 1;
        std_dfs(nat, i, m + 1, t, p, w, rem - 3, base, floor, need_melds, best);
        nat[i] += 1;
        nat[i + 1] += 1;
        nat[i + 2] += 1;
    }
    // 两面搭子
    if i < 27 && i % 9 <= 7 && nat[i + 1] > 0 {
        nat[i] -= 1;
        nat[i + 1] -= 1;
        std_dfs(nat, i, m, t + 1, p, w, rem - 2, base, floor, need_melds, best);
        nat[i] += 1;
        nat[i + 1] += 1;
    }
    // 坎张搭子
    if i < 27 && i % 9 <= 6 && nat[i + 2] > 0 {
        nat[i] -= 1;
        nat[i + 2] -= 1;
        std_dfs(nat, i, m, t + 1, p, w, rem - 2, base, floor, need_melds, best);
        nat[i] += 1;
        nat[i + 2] += 1;
    }
    // 孤张(放弃位置 i 剩余的 c 张)
    std_dfs(nat, i + 1, m, t, p, w, rem - c, base, floor, need_melds, best);
}

fn std_shanten(counts: &[i32; 34], locked: i32) -> i32 {
    let wilds = counts[W];
    let mut nat = [0i32; 33];
    nat.copy_from_slice(&counts[..33]);
    let need_melds = 4 - locked;
    let base = 2 * need_melds;
    let floor = if counts.iter().sum::<i32>() == 13 - 3 * locked { 0 } else { -1 };
    let mut best = 9;
    let rem0: i32 = nat.iter().sum();
    std_dfs(&mut nat, 0, 0, 0, 0, wilds, rem0, base, floor, need_melds, &mut best);
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
    if counts[W] == 0 {
        return shanten_impl(counts, locked);
    }
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
            || (total == best_total
                && (best_discard.is_none() || d < best_discard.unwrap()))
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
    let mut rows = Vec::new();
    let mut cache = HashMap::new();
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
        let child_s = wildcard_shanten(&child, locked, &mut cache)?;
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
            tiles: if include_tiles { draw_tiles } else { Vec::new() },
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
        &arr, locked, vis.as_ref(), &mut shanten_cache, include_tiles)
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
    let legal = legal_discards.map(|values| {
        values.into_iter().map(|t| t as usize).collect::<Vec<_>>()
    });
    let rows = discard_frontier_impl(
        &arr, locked, vis.as_ref(), legal.as_deref(), include_tiles)
        .map_err(PyValueError::new_err)?;
    Ok(rows.into_iter().map(|row| (
        row.discard as i32,
        row.shanten,
        row.tiles.into_iter().map(|t| t as i32).collect(),
        row.total,
    )).collect())
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
fn ukeire(counts: Vec<i32>, locked: i32, visible: Option<Vec<i32>>) -> PyResult<(i32, Vec<i32>, i64)> {
    let arr = to_arr(counts)?;
    let vis = match visible {
        Some(v) => Some(to_arr(v)?),
        None => None,
    };
    let (s, acc, total) = ukeire_impl(&arr, locked, vis.as_ref())
        .map_err(PyValueError::new_err)?;
    Ok((s, acc.iter().map(|&t| t as i32).collect(), total))
}

#[pymodule]
fn mj_kernels(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(shanten, m)?)?;
    m.add_function(wrap_pyfunction!(ukeire, m)?)?;
    m.add_function(wrap_pyfunction!(best_future_discard, m)?)?;
    m.add_function(wrap_pyfunction!(discard_frontier, m)?)?;
    Ok(())
}
