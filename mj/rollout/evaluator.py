"""Common-random-number paired rollout evaluation and bounded intervals."""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

from ..decision.context import ContextError
from ..decision.profile import fingerprint
from ..decision.score_value import theoretical_reward_bound
from .belief import BeliefSampler
from .simulator import FixedContinuation, run_rollout


PAIRWISE_RACING_VERSION = "paired-racing-v2"


def _mean(values):
    return statistics.fmean(values) if values else None


def _bounded_interval(values, bound, alpha, comparisons=1):
    """Hoeffding simultaneous interval for values in [-bound, bound]."""
    n = len(values)
    if not n:
        return {"n": 0, "mean": None, "low": None, "high": None,
                "alpha": float(alpha), "comparisons": int(comparisons)}
    log_term = math.log(max(2.0, 2.0 * comparisons / alpha))
    half = 2.0 * bound * math.sqrt(log_term / (2.0 * n))
    mean = statistics.fmean(values)
    return {"n": n, "mean": mean, "low": mean - half, "high": mean + half,
            "half_width": half, "alpha": float(alpha),
            "comparisons": int(comparisons),
            "method": "hoeffding-bounded-simultaneous"}


def _paired_interval(values, reward_bound, alpha, comparisons=1):
    """Hoeffding interval for a shared-world reward difference.

    Each reward is in ``[-reward_bound, reward_bound]``; therefore a paired
    difference is in ``[-2*reward_bound, 2*reward_bound]``.  Keeping this
    separate from the marginal interval makes the unit and proof bound
    explicit in the serialized teacher evidence.
    """
    interval = _bounded_interval(
        values, 2.0 * float(reward_bound), alpha, comparisons)
    interval["method"] = "hoeffding-paired-bounded-simultaneous"
    interval["difference_bound"] = 2.0 * float(reward_bound)
    return interval


def _paired_values(rows, left, right):
    """Return valid CRN deltas and sample ids for two actions."""
    values = []
    sample_ids = []
    for row in rows:
        if row.get("group_failed"):
            continue
        outcomes = row.get("outcomes") or {}
        a = outcomes.get(str(left), outcomes.get(left))
        b = outcomes.get(str(right), outcomes.get(right))
        if (not a or not b or a.get("status") != "ok" or
                b.get("status") != "ok" or a.get("reward") is None or
                b.get("reward") is None):
            continue
        values.append(float(a["reward"]) - float(b["reward"]))
        sample_ids.append(int(row["sample_id"]))
    return values, sample_ids


@dataclass(frozen=True)
class TeacherResult:
    status: str
    context_hash: str
    profile_fingerprint: str
    belief_version: str
    continuation_version: str
    actions: tuple
    candidates: tuple
    best_action: int | None
    runner_up: int | None
    paired_deltas: tuple
    sample_count: int
    attempted_samples: int
    failed_samples: int
    ambiguous: bool
    unsupported: bool
    stop_reason: str
    failures: tuple = ()
    rows: tuple = ()
    paired_rows: tuple = ()
    resume_state: dict | None = None
    fingerprint: str = ""
    alpha: float = 0.05
    max_looks: int = 1
    reward_bound: float = 0.0
    pairwise_deltas: tuple = ()
    elimination_history: tuple = ()
    pairwise_racing_version: str = PAIRWISE_RACING_VERSION

    def as_json(self):
        return {
            "status": self.status, "context_hash": self.context_hash,
            "profile_fingerprint": self.profile_fingerprint,
            "belief_version": self.belief_version,
            "continuation_version": self.continuation_version,
            "actions": list(self.actions), "candidates": list(self.candidates),
            "best_action": self.best_action, "runner_up": self.runner_up,
            "paired_deltas": list(self.paired_deltas),
            "sample_count": self.sample_count,
            "attempted_samples": self.attempted_samples,
            "failed_samples": self.failed_samples,
            "ambiguous": self.ambiguous, "unsupported": self.unsupported,
            "stop_reason": self.stop_reason, "failures": list(self.failures),
            "rows": list(self.rows), "resume_state": self.resume_state,
            "paired_rows": list(self.paired_rows),
            "fingerprint": self.fingerprint, "alpha": self.alpha,
            "max_looks": self.max_looks, "reward_bound": self.reward_bound,
            "pairwise_deltas": list(self.pairwise_deltas),
            "elimination_history": list(self.elimination_history),
            "pairwise_racing_version": self.pairwise_racing_version,
        }


class PairedTeacher:
    """Evaluate root actions on shared sampled worlds.

    The default continuation is shape-v1 and never sees the complete sampled
    world: ``run_rollout`` supplies an actor-specific detached view.  A failed
    candidate invalidates the entire sample row for paired statistics.
    """

    def __init__(self, context, *, profile_fingerprint="", seed=0,
                 belief_version="uniform_unseen-v1", continuation=None,
                 n0=32, batch=32, nmax=512, alpha=0.05,
                 reward_bound=None, max_steps=4096):
        self.context = context
        self.profile_fingerprint = profile_fingerprint
        self.seed = int(seed)
        self.belief_version = str(belief_version)
        self.continuation = continuation or FixedContinuation("shape-v1")
        self.n0 = max(1, int(n0))
        self.batch = max(1, int(batch))
        self.nmax = max(self.n0, int(nmax))
        self.alpha = float(alpha)
        if not 0 < self.alpha < 1:
            raise ValueError("alpha must be between zero and one")
        self.reward_bound = (float(reward_bound) if reward_bound is not None
                             else theoretical_reward_bound(context.base or 1))
        self.max_steps = max(1, int(max_steps))
        self.max_looks = max(
            1, 1 + math.ceil(max(0, self.nmax - self.n0) / self.batch))
        # A union bound over all candidate comparisons and all predeclared
        # sequential looks keeps the interval valid after repeated checking.
        self.look_alpha = self.alpha / self.max_looks

    def _config_fingerprint(self):
        return fingerprint({
            "context_hash": self.context.context_hash,
            "profile_fingerprint": self.profile_fingerprint,
            "belief_version": self.belief_version,
            "continuation": self.continuation.as_json(),
            "seed": self.seed, "n0": self.n0, "batch": self.batch,
            "nmax": self.nmax, "alpha": self.alpha,
            "max_looks": self.max_looks,
            "reward_bound": self.reward_bound,
            "pairwise_racing_version": PAIRWISE_RACING_VERSION,
        })

    def _unsupported_result(self, config_fp, actions, error):
        message = f"{type(error).__name__}:{error}"
        state = {"rows": [], "next_sample_id": 0,
                 "config_fingerprint": config_fp, "terminal": True,
                 "stop_reason": "unsupported_context", "ambiguous": False,
                 "pairwise_racing_version": PAIRWISE_RACING_VERSION}
        return TeacherResult(
            status="unsupported", context_hash=self.context.context_hash,
            profile_fingerprint=self.profile_fingerprint,
            belief_version=self.belief_version,
            continuation_version=self.continuation.version,
            actions=tuple(actions), candidates=(), best_action=None,
            runner_up=None, paired_deltas=(), sample_count=0,
            attempted_samples=0, failed_samples=0, ambiguous=False,
            unsupported=True, stop_reason="unsupported_context",
            failures=({"error": message},), rows=(), paired_rows=(),
            resume_state=state, fingerprint=config_fp, alpha=self.alpha,
            max_looks=self.max_looks, reward_bound=self.reward_bound)

    def _candidate_summary(self, action, rewards, outcomes, comparisons):
        # Rewards are never clipped.  The bound is a proof-based interval
        # constant, not the maximum observed score.
        interval = _bounded_interval(
            rewards, self.reward_bound, self.look_alpha, comparisons)
        valid_outcomes = [o for o in outcomes
                          if o.get("status") == "ok" and
                          not o.get("group_failed", False)]
        wins = [o for o in valid_outcomes
                if o.get("winner") == self.context.hero_seat]
        mults = [o.get("multiplier") for o in valid_outcomes
                 if o.get("winner") == self.context.hero_seat and
                 o.get("multiplier") is not None]
        draws = sum(bool(o.get("draw")) for o in valid_outcomes)
        return {
            "action": action, "EV": interval["mean"], "CI": interval,
            "samples": len(rewards), "valid_samples": len(rewards),
            "win_rate": len(wins) / len(valid_outcomes) if valid_outcomes else None,
            "avg_win_multiplier": _mean(mults) if mults else None,
            "draw_rate": draws / len(valid_outcomes) if valid_outcomes else None,
            "failed": sum(o.get("status") != "ok" or
                           o.get("group_failed", False) for o in outcomes),
        }

    def _pairwise_summary(self, rows, left, right, comparisons):
        values, sample_ids = _paired_values(rows, left, right)
        interval = _paired_interval(
            values, self.reward_bound, self.look_alpha, comparisons)
        if len(values) > 1:
            interval["variance"] = statistics.pvariance(values)
            interval["std_error"] = math.sqrt(
                interval["variance"] / len(values))
        elif values:
            interval["variance"] = 0.0
            interval["std_error"] = None
        interval["paired_sample_ids"] = sample_ids
        return {"left": left, "right": right, "delta": interval}

    def _active_pairwise(self, rows, actions):
        actions = tuple(actions)
        comparisons = max(1, len(actions) * (len(actions) - 1) // 2)
        return {
            (left, right): self._pairwise_summary(
                rows, left, right, comparisons)
            for left in actions for right in actions if left != right
        }

    def evaluate(self, actions=None, *, resume=None):
        """Run deterministic sequential batches and return a teacher artifact."""
        actions = tuple(sorted(set(
            self.context.legal_actions if actions is None else actions)))
        config_fp = self._config_fingerprint()
        try:
            self.context.validate_for("rollout")
        except ContextError as exc:
            return self._unsupported_result(config_fp, actions, exc)
        if not actions:
            raise ValueError("teacher requires at least one root action")
        for action in actions:
            if self.context.legal_actions and action not in self.context.legal_actions:
                raise ValueError(f"root action {action} is not legal in context")
        rewards = {action: [] for action in actions}
        outcomes = {action: [] for action in actions}
        rows = []
        failures = []
        attempted = 0
        active_actions = list(actions)
        elimination_history = []
        resume_data = resume
        if isinstance(resume, dict) and isinstance(
                resume.get("resume_state"), dict):
            resume_data = resume["resume_state"]
        if (resume_data and
                resume_data.get("config_fingerprint") not in (None, config_fp)):
            raise ValueError("resume config_fingerprint does not match teacher plan")
        # Resume accepts the result's JSON-like state.  Only complete shared
        # sample rows are restored; partial rows are retried from their id.
        completed_ids = set()
        if resume_data:
            restored_active = resume_data.get("active_actions")
            if restored_active is not None:
                active_actions = [int(action) for action in restored_active
                                  if int(action) in actions]
                if not active_actions:
                    active_actions = list(actions)
            for row in resume_data.get("rows", ()):
                if not isinstance(row, dict):
                    continue
                row_actions = row.get("outcomes") or {}
                if not set(int(a) for a in row_actions).issubset(set(actions)):
                    continue
                sid = int(row.get("sample_id"))
                # A retried writer may contain the same completed row twice;
                # restore the first complete copy only, never double-counting
                # its rewards or treating it as two samples.
                if sid in completed_ids:
                    continue
                row_valid = True
                restored = {}
                for action in actions:
                    out = row_actions.get(str(action), row_actions.get(action))
                    if out is None:
                        # Racing may stop sampling an eliminated action.  Do
                        # not fabricate a later outcome for its paired data.
                        continue
                    out = dict(out)
                    out["group_failed"] = bool(row.get("group_failed"))
                    restored[str(action)] = out
                if not row_valid or not restored:
                    continue
                row = dict(row)
                row["outcomes"] = restored
                completed_ids.add(sid)
                rows.append(row)
                for action in actions:
                    out = restored.get(str(action))
                    if out is None:
                        continue
                    outcomes[action].append(out)
                    if (not row.get("group_failed") and
                            out.get("status") == "ok" and
                            out.get("reward") is not None):
                        rewards[action].append(float(out["reward"]))
                    else:
                        failures.append({"sample_id": sid, "action": action,
                                         "error": out.get("error")})
        next_id = 0
        sampler = BeliefSampler(self.context, seed=self.seed,
                                belief_version=self.belief_version)
        terminal_resume = bool(resume_data and resume_data.get("terminal"))
        stop_reason = (str(resume_data.get("stop_reason", "nmax"))
                       if terminal_resume else "nmax")
        ambiguous = (bool(resume_data.get("ambiguous", True))
                     if terminal_resume else True)
        while not terminal_resume and len(completed_ids) < self.nmax:
            while next_id in completed_ids:
                next_id += 1
            sid = next_id
            next_id += 1
            attempted += 1
            world = sampler.sample(sid)
            row_outcomes = {}
            row_failed = False
            sampled_actions = tuple(active_actions)
            for action in sampled_actions:
                outcome = run_rollout(
                    self.context, world, action, self.continuation,
                    max_steps=self.max_steps)
                data = outcome.as_json()
                data["group_failed"] = False
                row_outcomes[str(action)] = data
                if not outcome.valid:
                    row_failed = True
            # Keep the failure row for the denominator, but never append any
            # reward from a partially successful candidate group.
            if row_failed:
                for action in sampled_actions:
                    out = row_outcomes[str(action)]
                    out["group_failed"] = True
                    outcomes[action].append(out)
                    failures.append({"sample_id": sid, "action": action,
                                         "error": out.get("error")})
            else:
                for action in sampled_actions:
                    out = row_outcomes[str(action)]
                    outcomes[action].append(out)
                    rewards[action].append(float(out["reward"]))
            rows.append({"sample_id": sid,
                         "world_fingerprint": world.fingerprint,
                         "outcomes": row_outcomes, "group_failed": row_failed,
                         "sampled_actions": list(sampled_actions)})
            completed_ids.add(sid)
            if len(completed_ids) < self.n0:
                continue
            if len(completed_ids) % self.batch != 0 and len(completed_ids) != self.n0:
                continue
            if len(active_actions) == 1:
                ambiguous = False
                stop_reason = ("only_legal_action" if len(actions) == 1
                               else "paired_elimination")
                break
            # Shared-world differences cancel a large part of the rollout
            # noise.  Use simultaneous pairwise intervals for both safe
            # elimination and stopping; marginal best/runner CIs remain only
            # descriptive fields in the candidate table.
            pairwise = self._active_pairwise(rows, active_actions)
            dominated = set()
            for left in active_actions:
                for right in active_actions:
                    if left == right:
                        continue
                    interval = pairwise[(left, right)]["delta"]
                    if interval.get("low") is not None and interval["low"] > 0:
                        dominated.add(right)
            if dominated and len(dominated) < len(active_actions):
                before = list(active_actions)
                active_actions = [action for action in active_actions
                                  if action not in dominated]
                elimination_history.append({
                    "sample_count": len(completed_ids),
                    "before": before,
                    "eliminated": sorted(dominated),
                    "after": list(active_actions),
                    "pairwise": [pairwise[(left, right)] for left in before
                                 for right in before if left != right
                                 and right in dominated],
                })
            if len(active_actions) == 1:
                ambiguous = False
                stop_reason = "paired_elimination"
                break
        if not rows:
            raise ValueError("teacher did not execute a sample")
        summaries = [self._candidate_summary(
            action, rewards[action], outcomes[action],
            max(1, len(actions) * (len(actions) - 1))) for action in actions]
        summaries.sort(key=lambda d: (
            d["EV"] if d["EV"] is not None else -math.inf,
            -int(d["action"])), reverse=True)
        best_action = summaries[0]["action"] if summaries else None
        runner_action = summaries[1]["action"] if len(summaries) > 1 else None
        deltas = []
        paired_rows = []
        if runner_action is not None:
            by_id = {int(row["sample_id"]): row for row in rows}
            for sid in sorted(by_id):
                row = by_id[sid]
                a = row["outcomes"].get(str(best_action))
                b = row["outcomes"].get(str(runner_action))
                if (a and b and not row.get("group_failed") and
                        a.get("status") == b.get("status") == "ok"):
                    delta = float(a["reward"]) - float(b["reward"])
                    deltas.append(delta)
                    paired_rows.append({"sample_id": sid,
                                        "world_fingerprint": row.get(
                                            "world_fingerprint"),
                                        "delta": delta})
        delta_summary = _paired_interval(
            deltas, self.reward_bound, self.look_alpha,
            max(1, len(actions) * (len(actions) - 1) // 2))
        if deltas:
            delta_summary["variance"] = (statistics.pvariance(deltas)
                                          if len(deltas) > 1 else 0.0)
            delta_summary["std_error"] = (
                math.sqrt(statistics.pvariance(deltas) / len(deltas))
                if len(deltas) > 1 else None)
            delta_summary["paired_sample_ids"] = [
                row["sample_id"] for row in paired_rows]
        paired = [{"best": best_action, "runner_up": runner_action,
                   "delta": delta_summary}]
        pairwise = self._active_pairwise(rows, actions)
        pairwise_deltas = tuple(pairwise.values())
        status = "ok" if not ambiguous and not failures else (
            "ambiguous" if ambiguous else "ok_with_failures")
        state = {"rows": rows, "next_sample_id": next_id,
                 "config_fingerprint": config_fp, "terminal": True,
                 "stop_reason": stop_reason, "ambiguous": ambiguous,
                 "active_actions": list(active_actions),
                 "elimination_history": elimination_history,
                 "pairwise_racing_version": PAIRWISE_RACING_VERSION}
        return TeacherResult(
            status=status, context_hash=self.context.context_hash,
            profile_fingerprint=self.profile_fingerprint,
            belief_version=self.belief_version,
            continuation_version=self.continuation.version,
            actions=actions, candidates=tuple(summaries),
            best_action=best_action, runner_up=runner_action,
            paired_deltas=tuple(paired),
            sample_count=sum(not bool(row.get("group_failed")) for row in rows),
            attempted_samples=len(rows),
            failed_samples=sum(bool(row.get("group_failed")) for row in rows),
            ambiguous=ambiguous,
            unsupported=False, stop_reason=stop_reason,
            failures=tuple(failures), rows=tuple(rows),
            paired_rows=tuple(paired_rows), resume_state=state,
            fingerprint=config_fp, alpha=self.alpha,
            max_looks=self.max_looks, reward_bound=self.reward_bound,
            pairwise_deltas=pairwise_deltas,
            elimination_history=tuple(elimination_history))


def evaluate_paired(context, actions=None, **kwargs):
    return PairedTeacher(context, **kwargs).evaluate(actions)
