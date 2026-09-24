"""Mini-Suphx 训练契约层 (P0)。

冻结本 change 的版本化策略演进契约:
- ``MiniSuphxRunManifest``  一次 campaign/run 的身份与不可变输入指纹;
- ``PolicyManifest``         一个冻结 policy_N 版本(版本发布制);
- ``RolloutManifest``        一份 rollout shard 的完整 provenance;
- ``OpponentPoolManifest``   对手池的代际组分与权重。

统一 value contract ``round-score-v2-normalized`` 正是现有 models.value-v2
的数值定义 ``clip(score/96,-1,1)``(= 设计 §3 的 ``clip(score/24,-4,4)/4``),
这里只给出稳定命名与校验,不重复实现 tanh head。

特征契约(R3):v1 训练线统一使用 ``public-v1``(75 公共平面);
``oracle-v1``(75 公共 + 16 oracle)只在 Champion-v1 冻结后的 Oracle Guiding
阶段使用;``big-hand-shadow-v1`` 为 metadata-only,不计入网络输入。任何跨
public/oracle 的补零或尽量加载都会被 fail-loud 拒绝。

所有 fingerprint 使用 :func:`mj.decision.profile.fingerprint`(canonical JSON,
稳定键序)。manifest 携带 ``schema`` + ``version`` 字段,任何旧 checkpoint/rollout
的 value/feature contract 归属不同时,构造/merge 必须显式拒绝。
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict, dataclass, field
import subprocess
from typing import Any, Mapping, Sequence

from ..decision.profile import fingerprint
from ..models.policy_value import (
    VALUE_CONTRACT_SCHEMA,
    ValueTransformContract,
    value_contract_from_json,
)

RUN_SCHEMA = "minisuphx-run-v1"
POLICY_SCHEMA = "minisuphx-policy-v1"
ROLLOUT_SCHEMA = "minisuphx-rollout-v1"
POOL_SCHEMA = "minisuphx-opponent-pool-v1"

VALUE_CONTRACT = "round-score-v2-normalized"   # = models.value-v2, scale=96
ACTION_SCOPE_DISCARD = "discard-only-v1"
_ACTION_SCOPES = (ACTION_SCOPE_DISCARD,)       # v1 阶梯:discard -> +PONG-> +CHOW

MODEL_ARCH = "resnet-6x128"

# ===== feature contracts (R3: public-only v1, oracle only post-Champion-v1) ====
#
# 历史命名把含混的 "planes-91" 直接当训练特征契约,但 v1 的 BC-v1/DAgger/PPO
# 与线上推理实际用 75 公共平面(MahjongDiscardEnv.extract(oracle=False))。
# 这里冻结三个显式契约,网络输入维度一经声明即绑定指纹,不允许靠补零或尽量
# 加载在 public(75)/oracle(91) 之间伪装成同一 policy(R3 MUST fail-loud)。

FEATURE_PUBLIC = "public-v1"                # n_planes=75, scalars=8, runtime
FEATURE_ORACLE = "oracle-v1"                # n_planes=91 (+16 oracle), runtime
FEATURE_BIG_HAND_SHADOW = "big-hand-shadow-v1"   # metadata-only, 无 runtime tensor

# v1 训练线默认公共特征契约;只有在 Champion-v1 冻结后才允许 oracle-v1。
FEATURE_CONTRACT = FEATURE_PUBLIC


@dataclass(frozen=True)
class FeatureContract:
    """一个冻结的 NN 输入特征契约,身份由 name+维度+verbose 共同决定。

    ``runtime`` 为 False 时仅表示 shard metadata / hard-state / 分桶身份
    (如 ``big-hand-shadow-v1``),不得承载网络输入张量。
    """

    name: str
    n_planes: int
    n_scalars: int
    oracle_planes: int
    display: str
    runtime: bool
    schema: str = "minisuphx-feature-contract-v1"

    def __post_init__(self):
        if self.schema != "minisuphx-feature-contract-v1":
            raise ValueError(f"unsupported feature contract schema: {self.schema!r}")
        if not self.name:
            raise ValueError("feature contract name must not be empty")
        if int(self.n_planes) < 0 or int(self.n_scalars) < 0:
            raise ValueError("feature dimensions must be non-negative")
        if int(self.oracle_planes) < 0:
            raise ValueError("oracle plane count must be non-negative")
        if self.runtime:
            if int(self.n_planes) == 0 or int(self.n_scalars) == 0:
                raise ValueError("runtime feature contract must describe a tensor")
            if bool(self.oracle_planes) != (self.name == FEATURE_ORACLE):
                raise ValueError(
                    "oracle feature contract must be the only one carrying "
                    "oracle planes")
        elif int(self.n_planes) != 0 or int(self.n_scalars) != 0:
            raise ValueError(
                "non-runtime (metadata-only) feature contract must not claim "
                "a tensor dimension")
        object.__setattr__(self, "n_planes", int(self.n_planes))
        object.__setattr__(self, "n_scalars", int(self.n_scalars))
        object.__setattr__(self, "oracle_planes", int(self.oracle_planes))

    @property
    def total_planes(self) -> int:
        return int(self.n_planes + self.oracle_planes)

    def payload(self) -> dict:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)

    def as_json(self) -> dict:
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


FEATURE_CONTRACTS: dict[str, FeatureContract] = {
    FEATURE_PUBLIC: FeatureContract(
        name=FEATURE_PUBLIC, n_planes=75, n_scalars=8, oracle_planes=0,
        display="planes-75-scalars-8", runtime=True),
    FEATURE_ORACLE: FeatureContract(
        name=FEATURE_ORACLE, n_planes=75, n_scalars=8, oracle_planes=16,
        display="planes-91-oracle16-scalars-8", runtime=True),
    FEATURE_BIG_HAND_SHADOW: FeatureContract(
        name=FEATURE_BIG_HAND_SHADOW, n_planes=0, n_scalars=0, oracle_planes=0,
        display="metadata-only", runtime=False),
}


def feature_contract(name: str) -> FeatureContract:
    """按 name 取冻结特征契约;未知契约 fail-loud,不允许静默近似。"""
    try:
        return FEATURE_CONTRACTS[str(name)]
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"unknown feature contract {name!r}; known: "
            f"{', '.join(sorted(FEATURE_CONTRACTS))}") from exc


def require_feature_contract(name: str, expected: str = FEATURE_CONTRACT) -> None:
    """校验一个(可能来自 checkpoint/rollout/配置)的特征契约身份。"""
    if str(name) != expected:
        raise ValueError(
            f"feature contract mismatch: got {name!r}, expected {expected!r}")


def verify_feature_planes(name: str, n_planes: int) -> None:
    """校验网络/观测张量的平面数与该契约一致,避免 75/91 补零互称同一 policy。"""
    contract = feature_contract(name)
    if not contract.runtime:
        raise ValueError(
            f"feature contract {name!r} is metadata-only and has no tensor width")
    if int(n_planes) != contract.total_planes:
        raise ValueError(
            f"feature contract {name!r} declares {contract.total_planes} planes "
            f"but a tensor with {int(n_planes)} planes was supplied")

# ===== value contract ==========================================================

def value_contract(version: str = VALUE_CONTRACT,
                   scale: float = 96.0) -> ValueTransformContract:
    """构造统一的 ``round-score-v2-normalized`` 数值契约。"""
    return ValueTransformContract(
        version=version, score_units="hero_round_score_points",
        scale=scale, clip_normalized=1.0,
        output_activation="tanh", inverse_transform="multiply-scale-v1")


def require_value_contract(data: Mapping[str, Any],
                           expected: str = VALUE_CONTRACT) -> None:
    """校验一个(可能来自 checkpoint/rollout/配置的)value contract JSON。"""
    contract = value_contract_from_json(data)
    if contract.version != expected:
        raise ValueError(
            f"value contract mismatch: got {contract.version!r}, "
            f"expected {expected!r}")


# ===== seed domains (任务 1.4) =================================================

@dataclass(frozen=True)
class SeedDomain:
    """训练/验证/最终测试的冻结种子域。final_test 任何训练/DAgger/PPO 禁止使用。"""

    train_lo: int = 0
    train_hi: int = 29999          # 30k legacy BC games
    validation_lo: int = 1_000_000
    validation_hi: int = 1_001_999
    final_test_lo: int = 2_000_000
    final_test_hi: int = 2_003_999

    def __post_init__(self):
        for lo, hi, name in (
                (self.train_lo, self.train_hi, "train"),
                (self.validation_lo, self.validation_hi, "validation"),
                (self.final_test_lo, self.final_test_hi, "final_test")):
            if int(lo) > int(hi):
                raise ValueError(f"invalid {name} seed range: [{lo},{hi}]")

    def contains(self, seed: int) -> str | None:
        if self.train_lo <= seed <= self.train_hi:
            return "train"
        if self.validation_lo <= seed <= self.validation_hi:
            return "validation"
        if self.final_test_lo <= seed <= self.final_test_hi:
            return "final-test"
        return None


# ===== run manifest ============================================================

@dataclass(frozen=True)
class MiniSuphxRunManifest:
    """一次 campaign/run 的不可变身份与输入指纹。"""

    run_id: str
    schema: str = RUN_SCHEMA
    git_commit: str = ""
    generation: int = 0
    value_contract: str = VALUE_CONTRACT
    feature_contract: str = FEATURE_CONTRACT
    model_arch: str = MODEL_ARCH
    action_scope: str = ACTION_SCOPE_DISCARD
    seed_domain: SeedDomain = field(default_factory=SeedDomain)
    bc_anchor_fingerprint: str = ""
    allow_final_test: bool = False
    created_at: float = field(default_factory=lambda: 0.0)

    def __post_init__(self):
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if self.action_scope not in _ACTION_SCOPES:
            raise ValueError(f"unknown action scope: {self.action_scope!r}")
        if self.value_contract != VALUE_CONTRACT:
            raise ValueError(f"unknown value contract: {self.value_contract!r}")
        feature_contract(self.feature_contract)  # fail-loud on unknown contract
        if self.allow_final_test:
            raise ValueError(
                "training campaigns MUST NOT allow final-test seeds")
        if self.seed_domain.contains(0) not in (None, "train"):
            raise ValueError("seed domain crosses reserved ranges")

    def payload(self) -> dict:
        value = asdict(self)
        value["seed_domain"] = dataclasses.asdict(self.seed_domain)
        return value

    def to_dict(self) -> dict:
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)


# ===== policy manifest =========================================================

@dataclass(frozen=True)
class PolicyManifest:
    """一个冻结 policy_N。policy version + model/value/scope 指纹共同定身份。"""

    policy_version: int
    generation: int
    git_commit: str
    schema: str = POLICY_SCHEMA
    model_arch: str = MODEL_ARCH
    action_scope: str = ACTION_SCOPE_DISCARD
    value_contract: str = VALUE_CONTRACT
    feature_contract: str = FEATURE_CONTRACT
    bc_anchor_fingerprint: str = ""
    checkpoint_sha256: str = ""
    opponent_pool_fingerprint: str = ""

    def __post_init__(self):
        if not isinstance(self.policy_version, int) or self.policy_version < 0:
            raise ValueError("policy_version must be a non-negative int")
        if self.value_contract != VALUE_CONTRACT:
            raise ValueError(f"unknown value contract: {self.value_contract!r}")
        feature_contract(self.feature_contract)  # fail-loud on unknown contract

    def payload(self) -> dict:
        return asdict(self)

    def to_dict(self) -> dict:
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)


# ===== rollout manifest ========================================================

@dataclass(frozen=True)
class RolloutManifest:
    """一份 rollout shard 的完整 provenance;缺 policy 身份即不可 merge。"""

    campaign_id: str
    job_id: str
    worker_id: str
    policy_version: int
    policy_fingerprint: str
    git_commit: str
    schema: str = ROLLOUT_SCHEMA
    generation: int = 0
    value_contract: str = VALUE_CONTRACT
    action_scope: str = ACTION_SCOPE_DISCARD
    feature_contract: str = FEATURE_CONTRACT
    opponent_pool_fingerprint: str = ""
    transition_count: int = 0
    artifact_sha256: str = ""
    seed_start: int = 0

    def __post_init__(self):
        if not self.policy_fingerprint:
            raise ValueError("rollout missing policy fingerprint")
        if not self.job_id or not self.campaign_id:
            raise ValueError("rollout missing job/campaign identity")
        if self.transition_count < 0:
            raise ValueError("transition_count must be non-negative")
        if self.value_contract != VALUE_CONTRACT:
            raise ValueError(f"value contract mismatch: {self.value_contract!r}")
        feature_contract(self.feature_contract)  # fail-loud on unknown contract

    def payload(self) -> dict:
        return asdict(self)

    def to_dict(self) -> dict:
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)


# ===== opponent pool ===========================================================

@dataclass(frozen=True)
class OpponentComponent:
    """对手池的一个组分(固定锚点或可学习代际)。"""

    name: str
    version: str
    weight: float
    kind: str = "legacy"           # legacy | bc | rl | search | champion
    layer: str = "discard"         # discard | reaction

    def __post_init__(self):
        if self.weight < 0:
            raise ValueError(f"opponent weight must be >=0: {self.weight}")

    def payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OpponentPoolManifest:
    """对手池指纹。pool 组分/权重变化必须产生新 fingerprint 与新代际身份。"""

    generation: int
    schema: str = POOL_SCHEMA
    components: tuple[OpponentComponent, ...] = ()

    def __post_init__(self):
        anchor_legacy = sum(
            c.weight for c in self.components if c.kind == "legacy")
        anchor_bc = sum(
            c.weight for c in self.components if c.kind == "bc")
        if sum(c.weight for c in self.components) <= 0:
            raise ValueError("opponent pool must have positive total weight")
        if anchor_legacy < 0.2:
            raise ValueError(
                f"opponent pool must keep legacy >= 20% (got {anchor_legacy:.2%})")
        if self.generation > 0 and anchor_bc < 0.1:
            raise ValueError(
                f"opponent pool must keep BC anchor >= 10% past Gen0 "
                f"(got {anchor_bc:.2%})")

    def payload(self) -> dict:
        value = asdict(self)
        value["components"] = [c.payload() for c in self.components]
        return value

    def to_dict(self) -> dict:
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)

    @staticmethod
    def gen0():
        return OpponentPoolManifest(generation=0, components=(
            OpponentComponent("legacy", "legacy-v1", 1.0, kind="legacy"),
        ))

    @staticmethod
    def gen1(bc_fingerprint: str, rl_version: str = "rl-gen1"):
        return OpponentPoolManifest(generation=1, components=(
            OpponentComponent("legacy", "legacy-v1", 0.6, kind="legacy"),
            OpponentComponent("bc", bc_fingerprint, 0.2, kind="bc"),
            OpponentComponent("rl", rl_version, 0.2, kind="rl"),
        ))


# ===== baseline freeze helper (任务 1.1) =======================================

def git_head(repo: str = ".") -> str:
    """当前 checkout 的 git commit(用于 manifest 身份)。失败回退空串。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
            check=True, text=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""