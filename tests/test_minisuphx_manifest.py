"""P0 契约层:manifest / value contract / seed domains / action scope / league anchor。"""

import pytest

from mj.training import minisuphx_manifest as m


# ===== feature contracts (task 13.1) ===========================================

def test_feature_contract_registry_is_frozen():
    public = m.feature_contract(m.FEATURE_PUBLIC)
    oracle = m.feature_contract(m.FEATURE_ORACLE)
    shadow = m.feature_contract(m.FEATURE_BIG_HAND_SHADOW)

    # v1 训练线默认公共特征契约
    assert m.FEATURE_CONTRACT == m.FEATURE_PUBLIC
    assert public.n_planes == 75 and public.n_scalars == 8
    assert public.oracle_planes == 0
    assert public.runtime is True

    assert oracle.n_planes == 75 and oracle.n_scalars == 8
    assert oracle.oracle_planes == 16
    assert oracle.total_planes == 91
    assert oracle.runtime is True

    # metadata-only 契约不得声称网络输入张量
    assert shadow.n_planes == 0 and shadow.n_scalars == 0
    assert shadow.runtime is False

    # 身份由 name+维度共同决定;public 与 oracle 必须是不同指纹
    assert public.fingerprint != oracle.fingerprint
    assert oracle.fingerprint != shadow.fingerprint
    # 每个契约指纹可重放
    assert public.fingerprint == m.feature_contract(m.FEATURE_PUBLIC).fingerprint


def test_feature_contract_unknown_fails_loud():
    with pytest.raises(ValueError, match="unknown feature contract"):
        m.feature_contract("planes-91-oracle-16-scalars-8")   # 旧含混命名必须拒绝
    with pytest.raises(ValueError, match="unknown feature contract"):
        m.feature_contract("oracle+1 dropped")


def test_require_feature_contract_mismatch_rejects():
    m.require_feature_contract(m.FEATURE_PUBLIC, expected=m.FEATURE_PUBLIC)
    with pytest.raises(ValueError, match="feature contract mismatch"):
        m.require_feature_contract(m.FEATURE_ORACLE, expected=m.FEATURE_PUBLIC)


def test_verify_feature_planes_rejects_pad_trick():
    # 75 公共特征禁止冒充 91 oracle 网络(反之亦然)——R3 不允许补零伪装
    m.verify_feature_planes(m.FEATURE_PUBLIC, 75)
    m.verify_feature_planes(m.FEATURE_ORACLE, 91)
    with pytest.raises(ValueError, match="declares 75 planes"):
        m.verify_feature_planes(m.FEATURE_PUBLIC, 91)     # 75->91 补零伪装被拒绝
    with pytest.raises(ValueError, match="declares 91 planes"):
        m.verify_feature_planes(m.FEATURE_ORACLE, 75)     # 91->75 截断伪装被拒绝
    with pytest.raises(ValueError, match="metadata-only"):
        m.verify_feature_planes(m.FEATURE_BIG_HAND_SHADOW, 0)


def test_manifests_default_to_public_v1():
    run = m.MiniSuphxRunManifest(run_id="x", git_commit="g")
    assert run.feature_contract == m.FEATURE_PUBLIC
    pol = m.PolicyManifest(policy_version=0, generation=0, git_commit="g")
    assert pol.feature_contract == m.FEATURE_PUBLIC
    roll = m.RolloutManifest(
        campaign_id="c", job_id="j", worker_id="w", policy_version=0,
        git_commit="g", policy_fingerprint="pf")
    assert roll.feature_contract == m.FEATURE_PUBLIC


def test_manifests_reject_unknown_feature_contract():
    with pytest.raises(ValueError, match="unknown feature contract"):
        m.MiniSuphxRunManifest(run_id="x", git_commit="g",
                               feature_contract="oracle-v2")
    with pytest.raises(ValueError, match="unknown feature contract"):
        m.PolicyManifest(policy_version=0, generation=0, git_commit="g",
                         feature_contract="planes-91-oracle-16-scalars-8")


def test_feature_contract_is_identity_bearing_in_manifest():
    a = m.MiniSuphxRunManifest(run_id="r", git_commit="g")
    b = m.MiniSuphxRunManifest(run_id="r", git_commit="g",
                               feature_contract=m.FEATURE_ORACLE)
    assert a.fingerprint != b.fingerprint


def test_value_contract_matches_design():
    c = m.value_contract()
    assert c.version == "round-score-v2-normalized"
    assert c.scale == 96.0
    assert c.clip_normalized == 1.0      # clip(score/96, -1, 1)


def test_require_value_contract_rejects_old_scale():
    wrong = {"version": "value-v2", "score_units": "hero_round_score_points",
             "scale": 4.0, "clip_normalized": 1.0,
             "output_activation": "tanh", "inverse_transform": "multiply-scale-v1"}
    with pytest.raises(ValueError):
        m.require_value_contract(wrong, expected="round-score-v2-normalized")


def test_fingerprints_stable_and_change():
    a = m.MiniSuphxRunManifest(run_id="run1", git_commit="abc")
    b = m.MiniSuphxRunManifest(run_id="run1", git_commit="abc")
    assert a.fingerprint == b.fingerprint
    c = m.MiniSuphxRunManifest(run_id="run1", git_commit="abd")
    assert c.fingerprint != a.fingerprint


def test_final_test_forbidden_in_training():
    with pytest.raises(ValueError):
        m.MiniSuphxRunManifest(run_id="bad", allow_final_test=True)
    with pytest.raises(ValueError):
        m.MiniSuphxRunManifest(run_id="bad", action_scope="all-109")


def test_seed_domain_bounds():
    sd = m.SeedDomain()
    assert sd.contains(0) == "train"
    assert sd.contains(100) == "train"
    assert sd.contains(1_000_001) == "validation"
    assert sd.contains(2_000_100) == "final-test"
    assert sd.contains(500_000) is None      # 保留间隙,避免误入
    train_lo, val_hi = sd.train_lo, sd.validation_hi
    assert train_lo <= sd.train_hi < sd.validation_lo


def test_opponent_pool_anchors():
    gen0 = m.OpponentPoolManifest.gen0()
    assert gen0.components[0].kind == "legacy"
    assert round(sum(c.weight for c in gen0.components), 6) == 1.0
    gen1 = m.OpponentPoolManifest.gen1(bc_fingerprint="bc1")
    assert round(sum(c.weight for c in gen1.components), 6) == 1.0
    assert gen1.components[0].kind == "legacy"
    assert gen1.components[0].weight == 0.6
    assert gen1.fingerprint != gen0.fingerprint

    # legacy < 20% 拒绝
    with pytest.raises(ValueError):
        m.OpponentPoolManifest(generation=1, components=(
            m.OpponentComponent("legacy", "legacy-v1", 0.1, kind="legacy"),
            m.OpponentComponent("bc", "x", 0.6, kind="bc"),
            m.OpponentComponent("rl", "y", 0.3, kind="rl")))
    # Gen>0 无 BC>=10% 拒绝
    with pytest.raises(ValueError):
        m.OpponentPoolManifest(generation=1, components=(
            m.OpponentComponent("legacy", "legacy-v1", 0.9, kind="legacy"),
            m.OpponentComponent("rl", "y", 0.1, kind="rl")))


def test_policy_manifest_identity():
    p = m.PolicyManifest(policy_version=0, generation=0, git_commit="abc",
                         bc_anchor_fingerprint="anchor1")
    assert p.fingerprint
    assert p.action_scope == "discard-only-v1"
    with pytest.raises(ValueError):
        m.PolicyManifest(policy_version=-1, generation=0, git_commit="x")


def test_rollout_manifest_requires_policy_identity():
    base = dict(campaign_id="c", job_id="j", worker_id="w", policy_version=0,
                git_commit="abc")
    with pytest.raises((ValueError, TypeError)):
        m.RolloutManifest(**base)      # 缺 policy_fingerprint 必须失败
    r = m.RolloutManifest(**base, policy_fingerprint="pf")
    assert r.fingerprint