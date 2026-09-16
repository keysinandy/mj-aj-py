"""Release manifest, kill switch and workload gates for a promoted policy.

The release manifest is the only supported way to switch the default runtime:
it pins the checkpoint hash, model manifest and policy-v3 profile, keeps a
rollback checkpoint, and leaves the ``shape-v2 -> legacy`` kill switch
available when it is inactive.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .policy_v3 import (
    PolicyV3Profile,
    PolicyV3Runtime,
    load_policy_value_model,
)
from .profile import fingerprint

RELEASE_SCHEMA = "policy-release-manifest-v1"
RELEASE_GATE_SCHEMA = "policy-release-gate-v1"


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReleaseManifest:
    schema: str = RELEASE_SCHEMA
    version: str = "search-distill-release-v1"
    active: bool = False
    checkpoint: str = ""
    checkpoint_sha256: str = ""
    model_version: str = ""
    model_manifest_fingerprint: str = ""
    policy_v3_profile_fingerprint: str = ""
    feature_contract_fingerprint: str = ""
    belief_profile_fingerprint: str = ""
    search_profile_fingerprint: str = ""
    value_mode: str = "policy-only"
    calibration_fingerprint: str = ""
    kill_switch: str = "shape-v2"
    rollback_checkpoint: str = ""
    rollback_manifest: str = ""
    evidence_fingerprint: str = ""

    def __post_init__(self):
        if self.schema != RELEASE_SCHEMA:
            raise ValueError(f"unsupported release schema: {self.schema}")
        if not str(self.version):
            raise ValueError("release version must not be empty")
        if self.kill_switch not in ("shape-v2", "legacy"):
            raise ValueError("kill switch must be shape-v2 or legacy")
        if self.value_mode not in ("policy-only", "value-v2"):
            raise ValueError("value_mode must be policy-only or value-v2")
        if self.value_mode == "value-v2" and not self.calibration_fingerprint:
            raise ValueError("value-v2 release requires a calibration fingerprint")
        if bool(self.active):
            for name in ("checkpoint", "checkpoint_sha256", "model_version",
                         "model_manifest_fingerprint",
                         "policy_v3_profile_fingerprint"):
                if not str(getattr(self, name)):
                    raise ValueError(f"active release requires {name}")
        object.__setattr__(self, "active", bool(self.active))

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def release_manifest_from_json(data: Mapping[str, Any]) -> ReleaseManifest:
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(ReleaseManifest.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown release manifest fields: " +
                         ", ".join(sorted(unknown)))
    manifest = ReleaseManifest(**value)
    if supplied is not None and supplied != manifest.fingerprint:
        raise ValueError("release manifest fingerprint mismatch")
    return manifest


def build_release_manifest(*, checkpoint, model,
                           profile: PolicyV3Profile | None = None,
                           evidence_fingerprint="", kill_switch="shape-v2",
                           rollback_checkpoint="", rollback_manifest="",
                           value_mode="policy-only", calibration_fingerprint="",
                           active=True, version="search-distill-release-v1"):
    """Pin a promoted checkpoint and profile into an auditable release."""
    if value_mode not in ("policy-only", "value-v2"):
        raise ValueError("value_mode must be policy-only or value-v2")
    checkpoint = Path(checkpoint)
    if not checkpoint.exists():
        raise ValueError(f"checkpoint does not exist: {checkpoint}")
    model_manifest = getattr(model, "manifest", None)
    if model_manifest is None:
        raise ValueError("release model must carry an explicit manifest")
    if getattr(model_manifest, "oracle", True):
        raise ValueError("release model must not be oracle")
    if value_mode == "value-v2" and not getattr(model_manifest, "calibrated", False):
        raise ValueError("value-v2 release requires a calibrated value head")
    if value_mode == "value-v2" and not calibration_fingerprint:
        raise ValueError("value-v2 release requires a calibration fingerprint")
    profile = profile or PolicyV3Profile(
        model_version=model_manifest.model_version,
        feature_contract_fingerprint=model_manifest.feature_contract_fingerprint,
        belief_profile_fingerprint=model_manifest.belief_profile_fingerprint,
        search_profile_fingerprint=model_manifest.search_profile_fingerprint,
        confidence_threshold=0.0, search_level="policy-only-v1",
        calibration_policy=("value-contract-v1" if value_mode == "value-v2"
                            else "policy-only-v1"))
    if value_mode == "policy-only" and (
            profile.calibration_policy != "policy-only-v1"):
        raise ValueError("policy-only release requires policy-only-v1 profile")
    if value_mode == "value-v2" and (
            profile.calibration_policy != "value-contract-v1"):
        raise ValueError("value-v2 release requires value-contract-v1 profile")
    if (profile.model_version and
            profile.model_version != model_manifest.model_version):
        raise ValueError("release profile/model version mismatch")
    if (profile.feature_contract_fingerprint and
            profile.feature_contract_fingerprint !=
            model_manifest.feature_contract_fingerprint):
        raise ValueError("release profile feature contract mismatch")
    return ReleaseManifest(
        version=version, active=bool(active), checkpoint=str(checkpoint),
        checkpoint_sha256=sha256_file(checkpoint),
        model_version=model_manifest.model_version,
        model_manifest_fingerprint=model_manifest.fingerprint,
        policy_v3_profile_fingerprint=profile.fingerprint,
        feature_contract_fingerprint=model_manifest.feature_contract_fingerprint,
        belief_profile_fingerprint=model_manifest.belief_profile_fingerprint,
        search_profile_fingerprint=model_manifest.search_profile_fingerprint,
        value_mode=value_mode,
        calibration_fingerprint=str(calibration_fingerprint),
        kill_switch=kill_switch, rollback_checkpoint=str(rollback_checkpoint),
        rollback_manifest=str(rollback_manifest),
        evidence_fingerprint=str(evidence_fingerprint))


def write_release_manifest(path, **kwargs):
    manifest = build_release_manifest(**kwargs)
    Path(path).write_text(
        json.dumps(manifest.as_json(), ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def load_release_runtime(manifest, *, device="cpu",
                         verify_checkpoint=True):
    """Load the released runtime or fall back to the kill switch.

    An inactive manifest produces a model-less runtime, so the normal
    ``shape-v2 -> legacy`` fallback chain is used and every fallback is
    counted in ``runtime.stats``.
    """
    if isinstance(manifest, (str, Path)):
        manifest = release_manifest_from_json(
            json.loads(Path(manifest).read_text(encoding="utf-8")))
    if not isinstance(manifest, ReleaseManifest):
        raise TypeError("manifest must be a ReleaseManifest or path")
    if not manifest.active:
        return PolicyV3Runtime(
            model=None,
            profile=PolicyV3Profile(
                version="policy-v3", model_version="",
                confidence_threshold=0.0, search_level="policy-only-v1")), False
    checkpoint = Path(manifest.checkpoint)
    if verify_checkpoint:
        if not checkpoint.exists():
            raise ValueError("release checkpoint is missing")
        if sha256_file(checkpoint) != manifest.checkpoint_sha256:
            raise ValueError("release checkpoint hash mismatch")
    model = load_policy_value_model(checkpoint, device=device)
    if model.manifest.fingerprint != manifest.model_manifest_fingerprint:
        raise ValueError("release model manifest fingerprint mismatch")
    if (manifest.value_mode == "value-v2" and
            not getattr(model.manifest, "calibrated", False)):
        raise ValueError("value-v2 release checkpoint is not calibrated")
    profile = PolicyV3Profile(
        model_version=manifest.model_version,
        feature_contract_fingerprint=manifest.feature_contract_fingerprint,
        belief_profile_fingerprint=manifest.belief_profile_fingerprint,
        search_profile_fingerprint=manifest.search_profile_fingerprint,
        confidence_threshold=0.0, search_level="policy-only-v1",
        calibration_policy=("value-contract-v1" if manifest.value_mode == "value-v2"
                            else "policy-only-v1"))
    if profile.fingerprint != manifest.policy_v3_profile_fingerprint:
        raise ValueError("release policy-v3 profile fingerprint mismatch")
    runtime = PolicyV3Runtime(model, profile=profile, device=device)
    if runtime.model_error is not None:
        raise ValueError(f"released model failed validation: {runtime.model_error}")
    return runtime, True


def release_gate_report(stats: Mapping[str, Any], *,
                        require_network_only=True):
    """Verify illegal/NaN/emergency counts for a release workload."""
    checks = {
        "decisions": int(stats.get("decisions", 0)) > 0,
        "illegal_selected": int(stats.get("illegal_selected", 0)) == 0,
        "model_output_errors": int(stats.get("model_output_errors", 0)) == 0,
        "emergency_fallbacks": int(stats.get("emergency", 0)) == 0,
    }
    if require_network_only:
        checks["no_fallbacks"] = int(stats.get("fallbacks", 0)) == 0
    value = {
        "schema": RELEASE_GATE_SCHEMA,
        "passed": all(checks.values()), "checks": checks,
        "stats": dict(stats), "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value
