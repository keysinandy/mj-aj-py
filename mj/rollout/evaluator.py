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
        })

    def _unsupported_result(self, config_fp, actions, error):
        message = f"{type(error).__name__}:{error}"
        state = {"rows": [], "next_sample_id": 0,
                 "config_fingerprint": config_fp, "terminal": True,
                 "stop_reason": "unsupported_context", "ambiguous": False}
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
            for row in resume_data.get("rows", ()):
                if not isinstance(row, dict):
                    continue
                row_actions = row.get("outcomes") or {}
                if set(int(a) for a in row_actions) != set(actions):
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
                        row_valid = False
                        break
                    out = dict(out)
                    out["group_failed"] = bool(row.get("group_failed"))
                    restored[str(action)] = out
                if not row_valid:
                    continue
                row = dict(row)
                row["outcomes"] = restored
                completed_ids.add(sid)
                rows.append(row)
                for action in actions:
                    out = restored[str(action)]
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
            for action in actions:
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
                for action in actions:
                    out = row_outcomes[str(action)]
                    out["group_failed"] = True
                    outcomes[action].append(out)
                    failures.append({"sample_id": sid, "action": action,
                                     "error": out.get("error")})
            else:
                for action in actions:
                    out = row_outcomes[str(action)]
                    outcomes[action].append(out)
                    rewards[action].append(float(out["reward"]))
            rows.append({"sample_id": sid,
                         "world_fingerprint": world.fingerprint,
                         "outcomes": row_outcomes, "group_failed": row_failed})
            completed_ids.add(sid)
            if len(completed_ids) < self.n0:
                continue
            if len(completed_ids) % self.batch != 0 and len(completed_ids) != self.n0:
                continue
            if len(actions) == 1:
                ambiguous = False
                stop_reason = "only_legal_action"
                break
            summaries = [self._candidate_summary(
                action, rewards[action], outcomes[action],
                max(1, len(actions) * (len(actions) - 1))) for action in actions]
            summaries.sort(key=lambda d: (
                d["EV"] if d["EV"] is not None else -math.inf,
                -int(d["action"])), reverse=True)
            best, runner = summaries[0], summaries[1]
            low = best["CI"]["low"]
            high = runner["CI"]["high"]
            if low is not None and high is not None and low > high:
                ambiguous = False
                stop_reason = "simultaneous_ci_separated"
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
        delta_summary = _bounded_interval(
            deltas, self.reward_bound * 2.0, self.look_alpha,
            max(1, len(actions) * (len(actions) - 1)))
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
        status = "ok" if not ambiguous and not failures else (
            "ambiguous" if ambiguous else "ok_with_failures")
        state = {"rows": rows, "next_sample_id": next_id,
                 "config_fingerprint": config_fp, "terminal": True,
                 "stop_reason": stop_reason, "ambiguous": ambiguous}
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
            max_looks=self.max_looks, reward_bound=self.reward_bound)


def evaluate_paired(context, actions=None, **kwargs):
    return PairedTeacher(context, **kwargs).evaluate(actions)
