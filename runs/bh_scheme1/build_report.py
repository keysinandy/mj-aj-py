"""Build the scheme-1 conclusion from the frozen local evidence files."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def main():
    coarse = read("coarse_512.json")
    fine = read("fine_2048.json")
    fair = read("fair_confirmation_2048.json")
    ablation = read("plus_one_ablation_1024.json")
    performance = read("performance_3x200.json")
    candidate = fair["results"][0]
    isolated = ablation["results"][0]
    perf = performance["results"][0]
    gain = (candidate["score_ci"]["ci_low"] > 0 and
            candidate["trigger"]["override_events"] > 0)
    isolated_gain = isolated["score_ci"]["ci_low"] > 0
    perf_ok = all(perf["gates"].values())
    outcome = {
        "scheme": 1,
        "implementation": "native homogeneous speed frontier + one opt-in Python complete challenger",
        "override_path_open": candidate["trigger"]["override_events"] > 0,
        "score_benefit_established": gain,
        "plus_one_benefit_established": isolated_gain,
        "performance_passed": perf_ok,
        "rust_migration_triggered": gain and not perf_ok,
        "production_enabled": False,
        "fair_confirmation": candidate,
        "plus_one_ablation": isolated,
        "performance": perf,
        "sources": fair["source_sha256"],
        "kernel": fair["kernel"],
        "verification": "94 passed, 16 subtests; strict OpenSpec valid; scoped diff check",
        "limits": [
            "Original coarse/fine kept candidate always including dealer; exploratory only.",
            "Final fair confirmation balances dealer and seats independently and alternates arm order.",
            "A source-seed pair is two games; 2048 games = 1024 independent paired samples.",
            "Scores are the mean of two hero seats against frozen production opponents, not a direct per-event causal return.",
            "Match max wild is post-policy and descriptive; initial hero max wild is fixed before decisions.",
            "2048-game confirmation and 1024-game ablation do not satisfy the pre-existing 4096-game release gate.",
        ],
    }
    (HERE / "summary.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# legacyV2 BigHandIntent 方案 1 评估（2026-10-08）", "",
        f"+1 override 路径：{'已打通' if outcome['override_path_open'] else '未触发'}。"
        f"独立积分收益：{'已确认' if gain else '未确认'}。"
        f"性能门禁：{'通过' if perf_ok else '未通过'}。",
        "默认 BigHandIntent 仍关闭。只有独立收益成立且性能失败才触发 Rust 改造；本次 Rust 判定："
        f"{'满足触发条件' if outcome['rust_migration_triggered'] else '未满足触发条件'}。", "",
        "## 实现与验证", "",
        "Rust v5 仅评价同向听 speed frontier；Python 只补唯一 +1 challenger。"
        "速度胜者由 complete 或原安全 bounds 确定，challenger 必须 complete。"
        "共享剩余 50ms 搜索预算，超时不提交结果，frontier 不超过 3。",
        "`perf_counter` 取代 Windows 15.625ms 分辨率的 coarse monotonic 计时。"
        "实验开关关闭时不补跑。模块回归 94 passed、16 subtests；OpenSpec strict 与 diff check 通过。", "",
        f"Baseline fingerprint：`{candidate['baseline_fingerprint']}`；"
        f"candidate：`{candidate['profile_fingerprint']}`。"
        "历史 F1 fingerprint 1215a0047e95ebe8 先于新增三个版本化 profile 字段，"
        "不是本次字段扩展后的指纹。各证据 JSON 保存源文件 SHA-256。", "",
        "## 积分", "",
        "粗扫四点每点 512 局，精扫按粗扫均值取有 override 的前两点，"
        "每点独立 2048 局。旧扫描庄家总在 candidate 组，故仅作探索记录。"
        "最终确认使用全新种子、均衡庄家与两组 hero 座位，并交替两臂执行顺序。", "",
        "| 阶段 / 配置 | 局数 / 独立对数 | 每 hero 积分差 | 95% CI | override |",
        "|---|---:|---:|---|---:|",
    ]
    for title, data in (("探索粗扫", coarse), ("探索精扫", fine),
                        ("公平确认", fair), ("+1 对同向听消融", ablation)):
        for r in data["results"]:
            ci = r["score_ci"]
            lines.append(f"| {title} / {r['grid_point'].get('label', '')} | "
                         f"{r['games']} / {r['valid_pairs']} | {ci['mean']:+.6f} | "
                         f"[{ci['ci_low']:+.6f}, {ci['ci_high']:+.6f}] | "
                         f"{r['trigger']['override_events']} |")
    lines += ["", "积分是相同 source seed/dealer 下两个 hero 的平均结算差；"
              "生产对手保持冻结。区间按独立 source-seed pair bootstrap，"
              "不能把两局视为两个独立统计样本。消融只关闭 hero 的 +1 路径，"
              "其余 BigHand 门槛和生产对手相同。", "",
              "## 公平确认的白板桶与补跑", "",
              "| 决策前白板数 | 已评估弃牌 | challenger | override | override ‰ |",
              "|---|---:|---:|---:|---:|"]
    for w, bucket in candidate["by_hero_wild_count"].items():
        lines.append(f"| {w} | {bucket['discard_decisions']} | {bucket['challenger_events']} | "
                     f"{bucket['override_events']} | {bucket['override_rate_per_discard']*1000:.3f} |")
    topup = candidate["python_challenger_topup"]
    lines += ["", f"Python 补跑 {topup['complete']}/{topup['attempts']} 完整。"
              f"p50={topup['latency_ms']['p50']:.3f}ms，p95={topup['latency_ms']['p95']:.3f}ms；"
              f"失败原因：`{topup['reasons']}`。"
              f"override 结果：`{candidate['trigger']['override_reasons']}`。", "",
              "| 对局开始时 hero 最大白板数 | 独立对数 | 积分差 | 95% CI |",
              "|---|---:|---:|---|"]
    for w, ci in candidate["by_initial_hero_max_wild"].items():
        lines.append(f"| {w} | {ci['n']} | {ci['mean']:+.6f} | "
                     f"[{ci['ci_low']:+.6f}, {ci['ci_high']:+.6f}] |")
    lines += ["", "完整 JSON 同时保存 candidate 对局过程最大白板数的积分桶与决策白板桶耗时。"
              "过程最大白板数受策略影响，仅是描述性结果；以上初始分桶固定于决策之前，"
              "也不能当作单次 override 的收益。", "",
              "## 同机性能", "",
              "单进程，四个机器人全部使用同一 profile；两 profile 各 3×200 局，"
              "每个匹配种子交替先后执行，包含正常决策与 step，不进行额外反事实审计调用。"
              "HU 窗口的动作选项数单独识别，不误算为普通弃牌 frontier；实际 frontier 最大为 3。", "",
              "| Profile | 弃牌样本 | p50 ms | p95 ms | p99 ms | fallback % |",
              "|---|---:|---:|---:|---:|---:|"]
    for name, result in perf["profiles"].items():
        ms = result["discard_latency_ms"]
        lines.append(f"| {name} | {ms['n']} | {ms['p50']:.4f} | {ms['p95']:.4f} | "
                     f"{ms['p99']:.4f} | {result['fallback_rate']*100:.3f} |")
    lines += ["", f"弃牌 p95 退化 {perf['discard_p95_regression_pct']:+.2f}%；"
              f"每批 elapsed/game 中位数退化 {perf['batch_elapsed_per_game_regression_pct']:+.2f}%。"
              f"门禁（<=10%）：`{perf['gates']}`。", "",
              "2048 局的公平确认和 1024 局消融不替代既有 4096 局发布门禁。"
              "本次按固定协议保留默认关闭；后续应先改进 override 收益判定，再安排确认样本。", "",
              "协议与原始证据：`plan.json`、`coarse_512.json`、`fine_2048.json`、"
              "`fair_confirmation_2048.json`、`plus_one_ablation_1024.json`、"
              "`performance_3x200.json`。各 JSON 保留种子、paired score、profile 和源文件指纹。", ""]
    (HERE / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({key: outcome[key] for key in (
        "override_path_open", "score_benefit_established", "plus_one_benefit_established",
        "performance_passed", "rust_migration_triggered")}, indent=2))


if __name__ == "__main__":
    main()
