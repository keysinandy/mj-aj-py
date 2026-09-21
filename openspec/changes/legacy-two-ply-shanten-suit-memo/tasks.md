# Tasks

## 1. 差分为先

- [ ] 1.1 扩展 Rust/Python shanten 差分对拍：随机手牌 × 财神 0-8 × locked 0-4 × 13/14 张，
  并覆盖张数非法输入的错误口径。
- [ ] 1.2 增加 Stage B 级 parity 基线：固定状态集上记录 100% 完整执行的指标快照，供新内核对比。

## 2. 花色记忆化内核

- [ ] 2.1 实现单花色分解表（base-5 计数打包 → Pareto `(m, t, p, wilds_used)`）与只读构建。
- [ ] 2.2 实现 4 块合并 DP 并接回现有 `score()`/`need_melds` 口径，保留七对与张数校验分支。
- [ ] 2.3 加入 opt-in 开关与回退路径；表构建后只读，可与并行 worker 共享。

## 3. 验证与基准

- [ ] 3.1 通过差分对拍与全套测试（含财神、副露、singleton/短路由）。
- [ ] 3.2 跑 100 状态基准：workers=1/2/4/8 的 raw/E2E p50/p95/p99、每决策 CPU、
  complete/fallback 与 search_used_rate，与并行化基线对比。
- [ ] 3.3 若单线程即满足 raw p95 < 40ms 与 p99 < 48ms，则评估把默认并行度降到 1-2；
  否则保持 opt-in 并在 tasks 记录结论。
