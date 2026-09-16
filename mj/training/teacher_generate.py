"""Search-teacher dataset generation with deterministic resume.

The generator runs source trajectories with a declared policy and frozen
opponent population, then labels every multi-action hero state with the
adaptive information-set teacher.  Only public/hero information plus the
versioned belief posterior enters an information state; sampled hidden worlds
stay inside the search simulator.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..belief import BeliefProfile, BeliefState, InformationHistory
from ..belief.events import history_from_game
from ..bot import choose_action
from ..decision.context import PublicDecisionContext
from ..game import Game
from ..models.opponent_policy import HeuristicLikelihoodPolicy
from ..search import InformationSetSearch, SearchError, SearchProfile
from ..shanten import shanten
from .distillation_profile import (
    FROZEN_SPLITS,
    OpponentPopulationProfile,
    SearchDistillationProfile,
    feature_contract_fingerprint,
    special_state_tags,
)
from .search_data import (
    SearchDataset,
    SearchSample,
    feature_fingerprint,
    read_search_dataset,
    work_identity,
    write_search_dataset,
)
from .teacher_budget import (
    TeacherBudgetProfile,
    TeacherEvidence,
    forced_decision,
    run_decision,
    start_decision,
)

REFERENCE_SCHEMA = "search-reference-context-v1"
GENERATION_SCHEMA = "search-teacher-generation-v1"
HEURISTIC_EVALUATORS = ("legacy", "shape-v1", "shape-v2")


def _flat_action(action):
    from ..features import action_to_flat
    return action_to_flat(int(action))


def split_for_seed(seed, *, splits: Mapping[str, Mapping[str, int]] | None = None):
    """Which frozen split range owns a source-game seed."""
    for name, row in (splits or FROZEN_SPLITS).items():
        start = int(row["seed_start"])
        if start <= int(seed) < start + int(row["games"]):
            return name
    return None


class HeuristicPolicy:
    """Deterministic frozen heuristic trajectory policy."""

    def __init__(self, evaluator="shape-v2"):
        if evaluator not in HEURISTIC_EVALUATORS:
            raise ValueError(f"unsupported heuristic evaluator: {evaluator}")
        self.evaluator = evaluator
        self.version = f"heuristic:{evaluator}"

    def choose(self, game, seat):
        return int(choose_action(game, seat, evaluator=self.evaluator))


class CheckpointPolicy:
    """Manifest-bearing PolicyValueNet checkpoint as a trajectory policy."""

    def __init__(self, path, device="cpu"):
        from ..decision.policy_v3 import load_policy_value_model

        self.path = str(path)
        self.model = load_policy_value_model(self.path, device=device)
        manifest = self.model.manifest
        self.version = (f"checkpoint:{manifest.model_version}:"
                        f"{manifest.fingerprint}")

    def choose(self, game, seat):
        legal = tuple(int(action) for action in game.legal_actions())
        distribution = self.model.predict_game(game, seat, legal_actions=legal)
        actions = tuple(int(action) for action in distribution.actions)
        if set(actions) != set(legal) or len(actions) != len(legal):
            raise ValueError("checkpoint policy distribution mismatches legal set")
        best, best_key = None, None
        for order, (action, probability) in enumerate(zip(
                actions, distribution.probabilities)):
            key = (float(probability), -order)
            if best_key is None or key > best_key:
                best, best_key = action, key
        return int(best)


def policy_from_source(source):
    """Create a trajectory policy from a declared source string."""
    source = str(source)
    if source.startswith("checkpoint:"):
        return CheckpointPolicy(source.split(":", 1)[1])
    if source.startswith("policy_value:"):
        return CheckpointPolicy(source.split(":", 1)[1])
    if source.startswith("heuristic:"):
        return HeuristicPolicy(source.split(":", 1)[1])
    if source in HEURISTIC_EVALUATORS:
        return HeuristicPolicy(source)
    raise ValueError(f"unsupported policy source: {source!r}")


class PopulationChooser:
    """Deterministic per-seat opponent member from the frozen population."""

    def __init__(self, population: OpponentPopulationProfile, *,
                 policies: Mapping[str, Any] | None = None):
        self.population = population
        self.policies = dict(policies or {})
        for name, _ in population.members:
            if name not in self.policies:
                self.policies[name] = policy_from_source(name)

    def resolve(self, *, generation, source_group, seat):
        name = self.population.member_for(
            generation=generation, source_group=source_group, seat=seat)
        return name, self.policies[name], self.population.version_of(name)


@dataclass(frozen=True)
class SourceGameSpec:
    seed: int
    hero_seat: int = 0
    dealer: int = 0
    you_cai_bi_kao: bool = False

    def __post_init__(self):
        if int(self.seed) < 0:
            raise ValueError("source game seed must be non-negative")
        if int(self.hero_seat) not in range(4) or int(self.dealer) not in range(4):
            raise ValueError("hero_seat/dealer must be seat indices")

    @property
    def source_group(self):
        return f"game:{int(self.seed)}"

    @property
    def split(self):
        return split_for_seed(self.seed)

    def as_json(self):
        return {"seed": int(self.seed), "hero_seat": int(self.hero_seat),
                "dealer": int(self.dealer),
                "you_cai_bi_kao": bool(self.you_cai_bi_kao),
                "source_group": self.source_group, "split": self.split}


def scheduled_specs(*, seed_start, games, ycbk_variants=(False,),
                    rotate_seats=True):
    """Balanced seat/dealer schedule: seat=i%4; dealer=(i//4)%4."""
    specs = []
    for index in range(int(games)):
        for you_cai_bi_kao in ycbk_variants:
            specs.append(SourceGameSpec(
                seed=int(seed_start) + index,
                hero_seat=(index % 4) if rotate_seats else 0,
                dealer=(index // 4) % 4,
                you_cai_bi_kao=bool(you_cai_bi_kao)))
    return specs


@dataclass(frozen=True)
class DecisionSnapshot:
    context: PublicDecisionContext
    history: InformationHistory
    belief: BeliefState
    planes: Any
    scalars: Any
    legal_actions: tuple[int, ...]
    tags: tuple[str, ...]
    shanten: int | None
    wall_remaining: int
    phase: str
    dealer: int
    hero_seat: int
    you_cai_bi_kao: bool
    opponent_version: str
    baseline_disagreement: bool


def decision_snapshot(game, seat, *, belief_profile, hero_action,
                      baseline_action=None, opponent_version=""):
    """Capture the information state of one hero decision point."""
    from ..models.policy_value import extract_value_features

    seat = int(seat)
    context = PublicDecisionContext.from_game_complete(game, seat)
    history = history_from_game(game)
    belief = BeliefState(context, history=history, profile=belief_profile)
    planes, scalars = extract_value_features(context, history=history,
                                             belief=belief)
    hand = list(game.hands[seat])
    drawn = game.drawn[seat]
    if drawn is not None and hand[drawn] > 0:
        hand[drawn] -= 1
    return DecisionSnapshot(
        context=context, history=history, belief=belief, planes=planes,
        scalars=scalars,
        legal_actions=tuple(int(action) for action in game.legal_actions()),
        tags=special_state_tags(game, seat),
        shanten=int(shanten(hand, len(game.melds[seat]))),
        wall_remaining=int(game.live_wall_left()),
        phase=str(game.phase), dealer=int(game.dealer), hero_seat=seat,
        you_cai_bi_kao=bool(getattr(game, "you_cai_bi_kao", False)),
        opponent_version=str(opponent_version),
        baseline_disagreement=(baseline_action is not None and
                               int(baseline_action) != int(hero_action)))


def _evidence_from(result, *, baseline_disagreement=False, critical=False):
    ranked = sorted((float(value) for value in result.q_by_action.values()),
                    reverse=True)
    q_gap = None
    if len(ranked) > 1:
        q_gap = ranked[0] - ranked[1]
    total_visits = sum(int(value) for value in result.visit_counts.values())
    top1_share = (max(int(value) for value in result.visit_counts.values()) /
                  total_visits if total_visits else None)
    variances = [float(value) for value in result.variance_by_action.values()]
    mean_variance = sum(variances) / len(variances) if variances else None
    return TeacherEvidence(
        status=result.status, ambiguous=bool(result.ambiguous), q_gap=q_gap,
        top1_visit_share=top1_share, mean_variance=mean_variance,
        baseline_disagreement=bool(baseline_disagreement), critical=bool(critical))


def _common_sample_kwargs(snapshot, *, source_group, generation,
                          policy_version_source, search_fingerprint,
                          budget_profile):
    return {
        "source_group": str(source_group),
        "generation": int(generation),
        "policy_version_source": str(policy_version_source),
        "opponent_policy_version": snapshot.opponent_version,
        "search_fingerprint": str(search_fingerprint),
        "leaf_version": "terminal-rollout-v1",
        "planes": snapshot.planes,
        "scalars": snapshot.scalars,
        "special_state_tags": snapshot.tags,
        "teacher_seed": int(budget_profile.search_seed),
        "phase": snapshot.phase,
        "dealer": snapshot.dealer,
        "hero_seat": snapshot.hero_seat,
        "you_cai_bi_kao": snapshot.you_cai_bi_kao,
        "shanten": snapshot.shanten,
        "wall_remaining": snapshot.wall_remaining,
    }


def _sample_without_evidence(snapshot, *, decision, status, forced, **kwargs):
    mask = [False] * 109
    for action in snapshot.legal_actions:
        mask[_flat_action(action)] = True
    visits = {}
    if forced:
        visits[_flat_action(snapshot.legal_actions[0])] = 1
    return SearchSample(
        context_hash=snapshot.context.context_hash,
        history_hash=snapshot.history.history_hash,
        legal_mask=tuple(mask), visit_counts=visits, q_by_action={},
        root_value=None, simulations=0, ambiguous=False, confidence=None,
        belief_fingerprint=snapshot.belief.fingerprint,
        forced=bool(forced), teacher_budget_tier=int(decision.tier),
        teacher_requested_simulations=int(decision.simulations),
        teacher_completed_simulations=0, teacher_failed_simulations=0,
        teacher_status=status, teacher_stop_reason=decision.stop_reason,
        feature_fingerprint=feature_fingerprint(snapshot.planes,
                                                snapshot.scalars),
        **kwargs)


def label_snapshot(snapshot, *, source_group, generation, policy_version_source,
                   budget_profile: TeacherBudgetProfile,
                   search_profile: SearchProfile,
                   belief_profile: BeliefProfile,
                   actor_policy=None,
                   completed_work_ids: Iterable[str] = (),
                   critical_tags: Sequence[str] | None = None):
    """Teacher-label one snapshot; returns ``None`` when resume skips it."""
    # Bind the effective belief profile before deriving any identity: the
    # resumed dataset stores the bound search fingerprint, so the resume key
    # must use the same profile.
    profile = replace(
        search_profile,
        belief_profile_fingerprint=snapshot.belief.profile.fingerprint)
    work = work_identity(
        source_group=source_group,
        context_hash=snapshot.context.context_hash,
        history_hash=snapshot.history.history_hash,
        teacher_seed=budget_profile.search_seed,
        search_fingerprint=profile.fingerprint)
    if work in set(completed_work_ids):
        return None
    common = _common_sample_kwargs(
        snapshot, source_group=source_group, generation=generation,
        policy_version_source=policy_version_source,
        search_fingerprint=profile.fingerprint,
        budget_profile=budget_profile)
    legal_count = len(snapshot.legal_actions)
    if legal_count == 1:
        decision = forced_decision(budget_profile, source_key=work)
        if not decision.sanity:
            return None
        return _sample_without_evidence(
            snapshot, decision=decision, status="forced-sanity", forced=True,
            **common)
    tags = set(critical_tags if critical_tags is not None
               else budget_profile.critical_tags)
    critical = bool(set(snapshot.tags) & tags)
    decision = start_decision(
        budget_profile, baseline_disagreement=snapshot.baseline_disagreement,
        critical=critical, source_key=work)
    actor = actor_policy or HeuristicLikelihoodPolicy(
        temperature=1.0, version=search_profile.actor_policy_version)
    result = None
    final = decision
    while True:
        simulations = budget_profile.budget(decision.tier)
        try:
            search = InformationSetSearch(
                snapshot.context, history=snapshot.history,
                belief=snapshot.belief, actor_policy=actor, profile=profile)
            result = search.run(simulation_budget=simulations)
        except SearchError as exc:
            unsupported = replace(
                decision, status="unsupported",
                stop_reason=f"search_error:{type(exc).__name__}")
            return _sample_without_evidence(
                snapshot, decision=unsupported, status="unsupported",
                forced=False, **common)
        follow = run_decision(
            budget_profile, decision,
            _evidence_from(result,
                           baseline_disagreement=snapshot.baseline_disagreement,
                           critical=critical))
        if not follow.escalated:
            final = follow
            break
        decision = follow
    result_sample_kwargs = dict(common)
    result_sample_kwargs.pop("search_fingerprint", None)
    result_sample_kwargs.pop("leaf_version", None)
    return SearchSample.from_search_result(
        result, teacher_tier=final.tier, teacher_status=final.status,
        teacher_stop_reason=final.stop_reason, **result_sample_kwargs)


@dataclass
class GameResult:
    spec: SourceGameSpec
    samples: list
    reference_rows: list
    error: str | None = None

    @property
    def ok(self):
        return self.error is None


@dataclass(frozen=True)
class GenerationConfig:
    generation: int = 0
    policy_source: str = "heuristic:shape-v2"
    population: OpponentPopulationProfile = field(
        default_factory=OpponentPopulationProfile)
    budget_profile: TeacherBudgetProfile = field(
        default_factory=TeacherBudgetProfile)
    search_profile: SearchProfile = field(default_factory=SearchProfile)
    belief_profile: BeliefProfile = field(default_factory=BeliefProfile)
    disagreement_source: str | None = None
    reference_mode: bool = False
    reference_simulations: int = 16000

    def __post_init__(self):
        if int(self.generation) < 0:
            raise ValueError("generation must be non-negative")
        if int(self.reference_simulations) not in (8000, 16000):
            raise ValueError("reference simulations must be 8000 or 16000")


def run_source_game(spec: SourceGameSpec, config: GenerationConfig,
                    *, completed_work_ids: Iterable[str] = (),
                    critical_tags: Sequence[str] | None = None):
    """Run one trajectory and teacher-label its hero decision states."""
    samples = []
    reference_rows = []
    error = None
    try:
        hero_policy = policy_from_source(config.policy_source)
        baseline = (policy_from_source(config.disagreement_source)
                    if config.disagreement_source else None)
        population = PopulationChooser(config.population)
        game = Game(seed=spec.seed, dealer=spec.dealer,
                    you_cai_bi_kao=spec.you_cai_bi_kao)
        snapshots = []
        while not game.done:
            actor = int(game.current_seat())
            legal = tuple(int(action) for action in game.legal_actions())
            if not legal:
                raise RuntimeError("non-terminal state has no legal actions")
            if actor == spec.hero_seat:
                action = hero_policy.choose(game, actor)
                if action not in legal:
                    raise RuntimeError("hero policy selected an illegal action")
                baseline_action = (baseline.choose(game, actor)
                                   if baseline is not None else None)
                opponent_versions = []
                for other in range(4):
                    if other == actor:
                        continue
                    _, _, member_version = population.resolve(
                        generation=config.generation,
                        source_group=spec.source_group, seat=other)
                    opponent_versions.append(member_version)
                snapshots.append(decision_snapshot(
                    game, actor, belief_profile=config.belief_profile,
                    hero_action=action, baseline_action=baseline_action,
                    opponent_version="|".join(opponent_versions)))
            else:
                _, policy, member_version = population.resolve(
                    generation=config.generation,
                    source_group=spec.source_group, seat=actor)
                action = policy.choose(game, actor)
                if action not in legal:
                    raise RuntimeError("population policy selected an illegal action")
            game.step(action)
        for snapshot in snapshots:
            if config.reference_mode:
                sample = _reference_sample(
                    snapshot, config, source_group=spec.source_group,
                    policy_version_source=hero_policy.version,
                    critical_tags=critical_tags,
                    completed_work_ids=completed_work_ids)
                if sample is None:
                    continue
                reference_rows.append({
                    "schema": REFERENCE_SCHEMA,
                    "source_group": spec.source_group,
                    "generation": int(config.generation),
                    "policy_version_source": hero_policy.version,
                    "context": snapshot.context.as_json(),
                    "history": snapshot.history.as_json(),
                    "sample": sample.as_json(include_features=True),
                })
            else:
                sample = label_snapshot(
                    snapshot, source_group=spec.source_group,
                    generation=config.generation,
                    policy_version_source=hero_policy.version,
                    budget_profile=config.budget_profile,
                    search_profile=config.search_profile,
                    belief_profile=config.belief_profile,
                    completed_work_ids=completed_work_ids,
                    critical_tags=critical_tags)
                if sample is None:
                    continue
            score = float(game.scores[spec.hero_seat])
            samples.append(replace(sample, actual_round_score=score,
                                   terminal_reward=score))
    except Exception as exc:  # pragma: no cover - surfaced in GameResult
        error = f"{type(exc).__name__}:{exc}"
    return GameResult(spec, samples, reference_rows, error)


def _reference_sample(snapshot, config, *, source_group,
                      policy_version_source, critical_tags=None,
                      completed_work_ids=()):
    """Run the frozen high-budget reference search for one snapshot.

    Forced states are excluded: they carry no strategic choice and would
    waste the most expensive teacher budget.  The main loop also skips them
    before calling this function; the guard keeps direct callers honest.
    """
    if len(snapshot.legal_actions) <= 1:
        return None
    profile = replace(
        config.search_profile,
        version=f"search-v1-reference-{int(config.reference_simulations)}",
        simulation_budget=int(config.reference_simulations),
        belief_profile_fingerprint=snapshot.belief.profile.fingerprint)
    work = work_identity(
        source_group=source_group,
        context_hash=snapshot.context.context_hash,
        history_hash=snapshot.history.history_hash,
        teacher_seed=config.budget_profile.search_seed,
        search_fingerprint=profile.fingerprint)
    if work in set(completed_work_ids):
        return None
    actor = HeuristicLikelihoodPolicy(
        temperature=1.0, version=config.search_profile.actor_policy_version)
    try:
        search = InformationSetSearch(
            snapshot.context, history=snapshot.history,
            belief=snapshot.belief, actor_policy=actor, profile=profile)
        result = search.run(simulation_budget=int(config.reference_simulations))
    except SearchError:
        return None
    tags = set(critical_tags if critical_tags is not None
               else config.budget_profile.critical_tags)
    return SearchSample.from_search_result(
        result,
        source_group=source_group, generation=config.generation,
        policy_version_source=policy_version_source,
        opponent_policy_version=snapshot.opponent_version,
        planes=snapshot.planes, scalars=snapshot.scalars,
        special_state_tags=snapshot.tags,
        teacher_seed=config.budget_profile.search_seed,
        teacher_stop_reason="reference",
        teacher_tier=config.budget_profile.reference_tier,
        teacher_status=result.status,
        phase=snapshot.phase, dealer=snapshot.dealer,
        hero_seat=snapshot.hero_seat,
        you_cai_bi_kao=snapshot.you_cai_bi_kao,
        shanten=snapshot.shanten, wall_remaining=snapshot.wall_remaining)


def _worker(payload):
    spec, config, completed, critical_tags = payload
    return run_source_game(spec, config, completed_work_ids=completed,
                           critical_tags=critical_tags)


@dataclass
class GenerationResult:
    dataset: SearchDataset
    reference_rows: list
    errors: list
    specs: tuple

    @property
    def ok(self):
        return not self.errors


def generate_dataset(specs: Iterable[SourceGameSpec],
                     config: GenerationConfig, *, workers=1,
                     completed_work_ids: Iterable[str] = (),
                     critical_tags: Sequence[str] | None = None):
    """Generate all specs; worker count never changes the resulting rows."""
    specs = list(specs)
    completed = frozenset(completed_work_ids)
    payloads = [(spec, config, completed, critical_tags) for spec in specs]
    if int(workers) <= 1:
        results = [_worker(payload) for payload in payloads]
    else:
        with ProcessPoolExecutor(max_workers=int(workers)) as pool:
            results = list(pool.map(_worker, payloads))
    samples, reference_rows, errors = [], [], []
    for result in results:
        if not result.ok:
            errors.append({"spec": result.spec.as_json(), "error": result.error})
            continue
        samples.extend(result.samples)
        reference_rows.extend(result.reference_rows)
    samples.sort(key=lambda sample: (sample.source_group, sample.work_id))
    reference_rows.sort(key=lambda row: (row["source_group"],
                                         row["sample"]["work_id"]))
    return GenerationResult(SearchDataset(samples), reference_rows, errors,
                            tuple(specs))


def resume_dataset(path, *, include_features=True):
    """Load an existing dataset and its completed work identities."""
    path = Path(path)
    if not path.exists():
        return SearchDataset(), frozenset()
    dataset = read_search_dataset(path)
    return dataset, frozenset(sample.work_id for sample in dataset.samples)


def write_reference_contexts(path, rows):
    Path(path).write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows), encoding="utf-8")
    return path


def read_reference_contexts(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def generation_manifest(result: GenerationResult, config: GenerationConfig, *,
                        splits: Mapping[str, Mapping[str, int]] | None = None,
                        extra: Mapping[str, Any] | None = None):
    """Reproducibility manifest for a generation run."""
    from .dataset_report import coverage_gate, dataset_report

    groups = sorted({sample.source_group for sample in result.dataset.samples})
    by_split: dict[str, list[str]] = {"train": [], "validation": [],
                                      "final-test": [], "unassigned": []}
    for group in groups:
        seed = int(group.split(":")[1])
        split = split_for_seed(seed, splits=splits) or "unassigned"
        by_split[split].append(group)
    report = dataset_report(result.dataset)
    value = {
        "schema": GENERATION_SCHEMA,
        "generation": int(config.generation),
        "policy_source": str(config.policy_source),
        "feature_contract_fingerprint": feature_contract_fingerprint(),
        "population": config.population.as_json(),
        "teacher_budget": config.budget_profile.as_json(),
        "search_profile": config.search_profile.as_json(),
        "belief_profile": config.belief_profile.as_json(),
        "reference_mode": bool(config.reference_mode),
        "reference_simulations": int(config.reference_simulations),
        "count": len(result.dataset.samples),
        "reference_rows": len(result.reference_rows),
        "source_groups": groups,
        "source_group_splits": by_split,
        "dataset_fingerprint": result.dataset.fingerprint,
        "report": report,
        "coverage": coverage_gate(
            report, SearchDistillationProfile().critical_bucket_minimums),
        "errors": list(result.errors),
        "oracle": False,
    }
    if extra:
        value["extra"] = dict(extra)
    from ..decision.profile import fingerprint
    value["fingerprint"] = fingerprint(value, 24)
    return value
