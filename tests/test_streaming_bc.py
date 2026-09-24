"""Streaming BC trainer (task 13.6 + 13.2):ShardStreamingDataset / loader /
resume guard / public-v1 direct training。"""

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from mj.training import streaming_bc as s
from mj.training.minisuphx_manifest import (
    FEATURE_ORACLE, FEATURE_PUBLIC,
)

N_PLANES, N_SCALARS, N_ACTIONS = 75, 8, 109


def _write_shard(dir_, name, n, seed):
    rng = np.random.default_rng(seed)
    planes = rng.random((n, N_PLANES, 34), dtype=np.float32).astype(np.float16)
    scalars = rng.random((n, N_SCALARS), dtype=np.float32)
    mask = np.zeros((n, N_ACTIONS), dtype=bool)
    action = rng.integers(0, 34, size=n)
    mask[np.arange(n), action] = True
    seat = rng.integers(0, 4, size=n).astype(np.int8)
    score = rng.integers(-96, 96, size=(n, 4))
    np.savez(Path(dir_) / name, planes=planes, scalars=scalars, mask=mask,
             action=action.astype(np.int16), seat=seat, score=score)


@pytest.fixture
def data_dir(tmp_path):
    d = str(tmp_path / "data")
    Path(d).mkdir()
    for k in range(4):
        _write_shard(d, f"shard_{k:05d}.npz", 20 + k, seed=k)
    return d


def test_dataset_indexing_and_sample_shapes(data_dir):
    ds = s.ShardStreamingDataset(data_dir, augmented=True, seed=1)
    assert len(ds) == sum(20 + k for k in range(4))
    p, sc, m, a, t = ds[0]
    assert p.shape == (N_PLANES, 34)
    assert p.dtype == np.float32
    assert sc.shape == (N_SCALARS,)
    assert m.shape == (N_ACTIONS,)
    assert bool(m[a])
    assert -1.0 <= t <= 1.0
    # 增广后动作仍合法
    assert bool(m[a])
    # 越界
    with pytest.raises(IndexError):
        ds[len(ds)]


def test_dataset_is_streaming_and_caches_bounded(data_dir):
    ds = s.ShardStreamingDataset(data_dir, augmented=False, seed=1)
    for i in range(0, len(ds), 7):
        ds[i]
    # LRU 只缓存少量分片(默认 2),不常驻全量
    assert len(ds._cache) <= 2


def test_augment_direction_matches_features(data_dir):
    from mj.features import SUIT_PERMS, augment_sample

    ds = s.ShardStreamingDataset(data_dir, augmented=False, seed=5)
    p, _sc, _m, a, _t = ds[7]
    if a >= 34:
        a = int(_m.nonzero()[0][0])
    perm = SUIT_PERMS[1]
    p2, m2, a2 = s._augment_sample(p.copy(), _m.copy(), a, perm)
    p_ref, m_ref, a_ref = augment_sample(p.copy(), _m.copy(), a, perm)
    assert a2 == a_ref
    np.testing.assert_array_equal(m2, m_ref)
    np.testing.assert_array_equal(p2[..., 0], p_ref[..., 0])
    assert a2 < 34 and m2[a2]


def test_shard_flags_subset_and_loader_partition(data_dir):
    from mj.training.streaming_bc import build_dataloaders

    train, val = build_dataloaders(data_dir, batch_size=13, seed=0, val_pct=0.25,
                                   num_workers=0, pin_memory=False)
    n_train = len(train.dataset)
    n_val = len(val.dataset)
    total = 20 + 21 + 22 + 23
    assert n_train + n_val == total
    # val 保留至少一个分片
    assert n_val > 0
    assert n_train > 0
    # train 不打散单分片:样本都能被 loader 产出且批张量形状正确
    batch = next(iter(train))
    assert batch["planes"].shape[1:] == (N_PLANES, 34)
    assert batch["mask"].shape[1] == N_ACTIONS
    assert batch["action"].dtype == torch.int64


def test_dataset_fingerprint_with_and_without_manifest(data_dir):
    fp_plain = s.dataset_fingerprint(data_dir, teacher="legacy-v2-offline")
    # 写 manifest 后身份冻结为 manifest 内容
    manifest = {
        "schema": "minisuphx-bc-data-v1",
        "feature_contract": FEATURE_PUBLIC,
        "evaluator": "legacy-v2-offline",
        "scope": "all-root",
        "you_cai_bi_kao": False,
        "seed_domain": {"train_seed_lo": 0, "train_seed_hi": 100, "games": 20},
        "games": 20,
        "n_shards": 4,
        "git_commit": "abc",
    }
    path = Path(data_dir) / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    fp_manifest = s.dataset_fingerprint(data_dir, teacher="legacy-v2-offline")
    # manifest 指纹与 teacher 绑定,且与无 manifest 时不同
    assert fp_manifest != fp_plain
    assert fp_manifest == s.dataset_fingerprint(data_dir, teacher="legacy-v2-offline")
    # teacher(标签来源)不同 → 指纹不同
    assert fp_manifest != s.dataset_fingerprint(data_dir, teacher="legacy")


def test_train_spec_identity_excludes_epochs():
    spec1 = s.TrainSpec("d", FEATURE_PUBLIC, "t", 6, 128, 10, 512, 0.1, 0)
    spec2 = s.TrainSpec("d", FEATURE_PUBLIC, "t", 6, 128, 20, 512, 0.1, 0)
    spec3 = s.TrainSpec("d", FEATURE_ORACLE, "t", 6, 128, 10, 512, 0.1, 0)
    assert spec1.spec_fingerprint == spec2.spec_fingerprint  # 轮数可续
    assert spec1.spec_fingerprint != spec3.spec_fingerprint


def test_load_checkpoint_rejects_mismatches(tmp_path):
    d = str(tmp_path / "data")
    Path(d).mkdir()
    _write_shard(d, "shard_00000.npz", 32, seed=0)
    spec = s.TrainSpec(d, FEATURE_PUBLIC, "t", 2, 32, 3, 16, 0.1, 0)
    fp = s.dataset_fingerprint(d, "t")
    net = s.build_model(spec, torch.device("cpu"))
    opt = torch.optim.Adam(net.parameters(), lr=1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=3)
    ck_path = str(tmp_path / "last.pt")
    s.save_checkpoint(ck_path, net, opt, sched, global_step=10, epoch=2,
                      best_val=0.5, spec=spec, dataset_fp=fp,
                      source_state={}, rng={})

    # 同 spec 可加载
    s.load_checkpoint(ck_path, spec, fp, torch.device("cpu"))

    # 特征契约不符 → 拒绝(75↔91 伪装防护)
    bad_fc = s.TrainSpec(d, FEATURE_ORACLE, "t", 2, 32, 3, 16, 0.1, 0)
    with pytest.raises(ValueError, match="feature contract"):
        s.load_checkpoint(ck_path, bad_fc, fp, torch.device("cpu"))

    # 数据集/teacher 指纹不符 → 拒绝
    with pytest.raises(ValueError, match="dataset"):
        s.load_checkpoint(ck_path, spec, "wrong-fp", torch.device("cpu"))

    # 超参/seed 变更 → 拒绝
    bad_spec = s.TrainSpec(d, FEATURE_PUBLIC, "t", 2, 32, 3, 16, 0.1, 7)
    with pytest.raises(ValueError, match="spec"):
        s.load_checkpoint(ck_path, bad_spec, fp, torch.device("cpu"))


def test_bc_data_manifest_roundtrip(tmp_path):
    from types import SimpleNamespace

    from mj.bc_data import _write_manifest
    from mj.training.minisuphx_manifest import FEATURE_PUBLIC

    d = str(tmp_path / "data")
    Path(d).mkdir()
    _write_shard(d, "shard_00000.npz", 8, seed=0)
    args = SimpleNamespace(
        out=d, evaluator="legacyV2-offline", scope="all-root",
        you_cai_bi_kao=False, allow_search_fallback=False,
        seed0=0, games=1, per_shard=8, n_shards=1)
    _write_manifest(args, total_samples=8, n_shards=1)

    with open(Path(d) / "manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["feature_contract"] == FEATURE_PUBLIC
    assert manifest["evaluator"] == "legacyV2-offline"
    assert manifest["fingerprint"]
    # streaming trainer 读取 manifest 后指纹稳定,且语义不为空
    fp1 = s.dataset_fingerprint(d, "legacyV2-offline")
    fp2 = s.dataset_fingerprint(d, "legacyV2-offline")
    assert fp1 == fp2
    assert "no-shards" not in fp1
    # teacher(标签来源)不同 → 训练身份不同(即便 manifest 相同)
    assert s.dataset_fingerprint(d, "legacy") != fp1


def test_end_to_end_train_and_resume(tmp_path, capsys):
    d = str(tmp_path / "data")
    Path(d).mkdir()
    for k in range(3):
        _write_shard(d, f"shard_{k:05d}.npz", 24, seed=k)
    out = str(tmp_path / "out")
    rv = s.main([
        "--data", d, "--out", out, "--epochs", "1",
        "--blocks", "1", "--width", "8", "--bs", "16",
        "--workers", "0", "--device", "cpu",
    ])
    assert rv == 0
    assert (Path(out) / "best.pt").exists()
    assert (Path(out) / "last.pt").exists()
    assert (Path(out) / "config.json").exists()
    cfg = json.loads((Path(out) / "config.json").read_text(encoding="utf-8"))
    assert cfg["feature_contract_display"] == "planes-75-scalars-8"

    # resume 续训到 epoch 3(轮数可延长),身份校验通过
    rv2 = s.main([
        "--data", d, "--out", out, "--epochs", "3",
        "--blocks", "1", "--width", "8", "--bs", "16",
        "--workers", "0", "--device", "cpu",
        "--resume", str(Path(out) / "last.pt"),
    ])
    assert rv2 == 0

    # 试图用不同特征契约续训 → fail-loud(不静默续训)
    bad_out = str(tmp_path / "badout")
    with pytest.raises(ValueError, match="only supports|declares"):
        s.main(["--data", d, "--out", bad_out, "--feature", FEATURE_ORACLE,
                "--resume", str(Path(out) / "last.pt")])